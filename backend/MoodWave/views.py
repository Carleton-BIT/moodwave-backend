import base64
from datetime import timedelta
import requests
from django.contrib.auth import authenticate
from django.shortcuts import get_object_or_404
from django.http import JsonResponse
from rest_framework.authentication import TokenAuthentication
from rest_framework.authtoken.models import Token
from rest_framework.decorators import authentication_classes, permission_classes, api_view
from rest_framework.permissions import AllowAny, IsAuthenticated
from django.shortcuts import redirect
from django.utils import timezone
from rest_framework.response import Response
from term_project import settings
from .models import UserProfile, GlobalTrack, UserTrack, Playlist, PlaylistTrack
from .services.moodDescriptions import MOOD_DESCRIPTIONS
from .services.spotify_service import build_user_audio_profile_from_spotify, get_user_top_tracks, recommend_tracks_for_mood, refresh_access_token
from .services.api_serializer import RegisterSerializer


@api_view(['GET'])
def api_top_tracks(request):
    profile = request.user.userprofile

    # Refresh Spotify token
    access_token = refresh_access_token(profile)

    # Fetch User's top 5 songs
    top_tracks = get_user_top_tracks(access_token, limit=5)

    # Send to frontend
    data = []
    for t in top_tracks:
        data.append({
            "spotify_id": t["id"],
            "title": t["name"],
            "artists": ", ".join(t["artists"]),
            "album_image": t["album_image"],
        })

    return JsonResponse({"top_tracks": data}, status=200)


@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def api_save_playlist(request):
    user_profile = request.user.userprofile

    name = request.data.get("name", "MoodWave Mix")
    tracks_data = request.data.get("tracks", [])

    if not tracks_data:
        return Response(
            {
                "status": "error",
                "message": "No tracks provided."
            },
            status=400
        )

    # Create or get playlist
    playlist, _ = Playlist.objects.get_or_create(
        user_profile=user_profile,
        name=name
    )

    PlaylistTrack.objects.filter(playlist=playlist).delete()

    for t in tracks_data:
        sc_url = t.get("soundcloud_url")
        title = t.get("title")
        artists_field = t.get("artists")
        album_image = t.get("album_image")
        spotify_id = t.get("spotify_id")

        # Normalize artists
        if isinstance(artists_field, str):
            artists_list = [artists_field]
        else:
            artists_list = artists_field or []

        # Find an existing GlobalTrack
        gt = None

        if sc_url:
            gt = GlobalTrack.objects.filter(soundcloud_url=sc_url).first()

        if not gt and spotify_id:
            gt = GlobalTrack.objects.filter(spotify_id=spotify_id).first()

        # If it doesn't exist, create a new GlobalTrack
        if not gt:
            synthetic_spotify_id = spotify_id or f"sc-{sc_url}"

            gt = GlobalTrack.objects.create(
                spotify_id=synthetic_spotify_id,
                name=title or "Unknown title",
                artists=artists_list,
                artist_ids=[],
                album_image=album_image,
                soundcloud_url=sc_url,
                source="SoundCloud",
            )

        user_track, _ = UserTrack.objects.get_or_create(
            user_profile=user_profile,
            track=gt
        )

        # Add to Playlist
        PlaylistTrack.objects.get_or_create(
            playlist=playlist,
            track=user_track
        )

    return Response({"playlist_name": playlist.name}, status=200)



@api_view(["GET"])
@authentication_classes([TokenAuthentication])
def user_playlists(request):
    profile = request.user.userprofile

    playlists = []
    for p in profile.playlists.all().order_by("-created_at"):
        pts = PlaylistTrack.objects.filter(playlist=p)

        cover = None
        if pts.exists():
            first_pt = pts.select_related("track__track").first()
            cover = first_pt.track.track.album_image #First song in playlist set as playlist cover

        playlists.append({
            "id": p.id,
            "name": p.name,
            "trackCount": pts.count(),
            "cover": cover,
        })

    return Response({"playlists": playlists}, status=200)


@api_view(["GET"])
@authentication_classes([TokenAuthentication])
def api_get_playlist(request):
    playlist_id = request.GET.get("id")

    if not playlist_id:
        return Response({"error": "Missing playlist id"}, status=400)

    try:
        playlist = Playlist.objects.get(
            id=playlist_id,
            user_profile=request.user.userprofile
        )
    except Playlist.DoesNotExist:
        return Response({"error": "Playlist not found"}, status=404)

    tracks = PlaylistTrack.objects.filter(playlist=playlist).select_related("track__track")

    data = []
    for item in tracks:
        ut = item.track          # UserTrack
        gt = ut.track            # GlobalTrack

        data.append({
            "title": gt.name,
            "artists": ", ".join(gt.artists),
            "album": "",
            "album_image": gt.album_image,
            "soundcloud_url": gt.soundcloud_url,
            "spotify_id": gt.spotify_id,
            "id": gt.id,
        })

    return Response({
        "playlist": playlist.name,
        "tracks": data
    })

@api_view(["GET", "POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def mood_sync_api(request):
    profile, created = UserProfile.objects.get_or_create(user=request.user)

    profile.sync_in_progress = True
    profile.sync_done = 0
    profile.sync_total = 50
    profile.save()

    # run in background to avoid 502 timeout
    import threading
    threading.Thread(
        target=build_user_audio_profile_from_spotify,
        args=(profile, 50)
    ).start()

    return Response({"message": "Sync started"})



#Spotify authentication
SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_PROFILE_URL = "https://api.spotify.com/v1/me"


@api_view(["GET"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def spotify_start(request):

    token = request.auth

    if token is None:
        return Response({"error": "Not authenticated"}, status=401)

    spotify_url = (
        SPOTIFY_AUTH_URL
        + "?response_type=code"
        + f"&client_id={settings.SPOTIFY_CLIENT_ID}"
        + f"&redirect_uri={settings.SPOTIFY_REDIRECT_URI}"
        + "&scope=user-top-read user-read-recently-played user-library-read user-read-private"
        + f"&state={token.key}"
    )

    return Response({"spotify_url": spotify_url})



def spotify_callback(request):
    error = request.GET.get("error")
    code = request.GET.get("code")
    user_token = request.GET.get("state")

    if error:
        return redirect("https://moodwave-frontend.vercel.app/connect-spotify?error=spotify")

    if not code or not user_token:
        return redirect("https://moodwave-frontend.vercel.app/connect-spotify?error=missing_state")

    try:
        token = Token.objects.get(key=user_token)
        user = token.user
    except Token.DoesNotExist:
        return redirect("https://moodwave-frontend.vercel.app/connect-spotify?error=bad_token")

    profile, _ = UserProfile.objects.get_or_create(user=user)

    client_id = settings.SPOTIFY_CLIENT_ID
    client_secret = settings.SPOTIFY_CLIENT_SECRET
    redirect_uri = settings.SPOTIFY_REDIRECT_URI

    auth_header = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }

    try:
        token_resp = requests.post(SPOTIFY_TOKEN_URL, data=data, headers=headers)
        token_resp.raise_for_status()
    except Exception:
        return redirect("https://moodwave-frontend.vercel.app/connect-spotify?error=token_exchange")

    token_json = token_resp.json()
    access_token = token_json.get("access_token")
    refresh_token = token_json.get("refresh_token")
    expires_in = token_json.get("expires_in", 3600)

    if not access_token:
        return redirect("https://moodwave-frontend.vercel.app/connect-spotify?error=no_access")

    profile.access_token = access_token
    profile.refresh_token = refresh_token
    profile.token_expires_at = timezone.now() + timedelta(seconds=int(expires_in))
    profile.save()

    return redirect("https://moodwave-frontend.vercel.app/")

@api_view(["POST"])
@permission_classes([AllowAny])
def login(request):
    username = request.data.get("username")
    password = request.data.get("password")

    if not username or not password:
        return Response(
            {"error": "Username and password required"},
            status=400
        )

    print("DATA:", request.data)

    user = authenticate(username=username, password=password)

    if user is None:
        return Response(
            {"error": "Invalid credentials"},
            status=400
        )

    token, _ = Token.objects.get_or_create(user=user)

    return Response({
        "token": token.key,
        "username": user.username,
    })

@api_view(["POST"])
@permission_classes([AllowAny])
def signUp(request):
    serializer = RegisterSerializer(data=request.data)

    if serializer.is_valid():
        user = serializer.save()

        # Create UserProfile automatically
        UserProfile.objects.create(user=user)
        token, created = Token.objects.get_or_create(user=user)

        return Response(
            {
                "message": "Account created successfully.",
                "token": token.key,
                "username": user.username,
            },
            status=201
        )

    return Response(serializer.errors, status=400)

@api_view(["GET"])
@permission_classes([AllowAny])
def userInformation(request):
    token = request.headers.get("Authorization")

    if not token or not token.startswith("Token "):
        return Response({"error": "Not authenticated"}, status=401)

    token_key = token.split("Token ")[1]
    try:
        user = Token.objects.get(key=token_key).user
    except Token.DoesNotExist:
        return Response({"error": "Invalid token"}, status=401)

    profile, _ = UserProfile.objects.get_or_create(user=user)

    mood_list = (
        GlobalTrack.objects.filter(
            usertrack__user_profile=profile,
        )
        .exclude(mood__isnull=True)
        .exclude(soundcloud_id__isnull=True)
        .exclude(mood="")
        .values_list("mood", flat=True)
        .distinct()
    )

    mood_descriptions = {
        mood: MOOD_DESCRIPTIONS.get(mood, "No description")
        for mood in mood_list
    }


    return Response({
        "username": user.username,
        "spotify_connected": bool(profile.access_token),
        "profile_built": bool(profile.profile_built),
        "moods": list(mood_list),
        "mood_descriptions": mood_descriptions,
    })

@api_view(["GET"])
@authentication_classes([TokenAuthentication])
@permission_classes([AllowAny])
def user_stats(request):
    user = request.user
    profile = user.userprofile

    # All Spotify tracks the user has synced
    tracks = GlobalTrack.objects.filter(
        usertrack__user_profile=profile,
        source="Spotify"
    ).filter(
        mood__isnull=False
    ).exclude(
        mood=""
    ).exclude(
        soundcloud_url__isnull=True
    )

    if tracks.count() == 0:
        return Response({
            "mood_distribution": [],
            "top_genre": None,
            "total_tracks": 0,
        })

    total = tracks.count()

    # Count moods
    mood_counts = {}
    for t in tracks:
        mood = t.mood or "Unknown"
        mood_counts[mood] = mood_counts.get(mood, 0) + 1

    mood_distribution = [
        {
            "mood": mood,
            "percentage": round((count / total) * 100, 1)
        }
        for mood, count in mood_counts.items()
    ]

    # Determine top genre
    all_genres = []
    for t in tracks:
        if t.genres:
            all_genres.extend(t.genres)

    top_genre = None
    if all_genres:
        from collections import Counter
        top_genre = Counter(all_genres).most_common(1)[0][0]

    return Response({
        "mood_distribution": mood_distribution,
        "top_genre": top_genre,
        "total_tracks": total,
    })

@api_view(["GET"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def api_recommendations(request):
    mood = request.GET.get("mood")

    if not mood:
        return Response({"error": "Mood is required"}, status=400)

    token = request.headers.get("Authorization")
    profile = None

    if token and token.startswith("Token "):
        try:
            token_key = token.split("Token ")[1]
            user = Token.objects.get(key=token_key).user
            profile = user.userprofile
        except:
            profile = None

    if not profile:
        return Response({"songs": []})

    print("PROFILE:", profile)

    recommended = recommend_tracks_for_mood(profile, mood, limit=30)

    songs = []

    for t in recommended:
        # SoundCloud dict
        if isinstance(t, dict):
            songs.append({
                "title": t.get("title"),
                "artists": t.get("artists"),
                "album_image": t.get("album_image"),
                "soundcloud_url": t.get("soundcloud_url"),
            })
            continue

        # GlobalTrack object
        if isinstance(t, GlobalTrack) and t.soundcloud_url:
            songs.append({
                "title": t.name,
                "artists": ", ".join(t.artists),
                "album_image": t.album_image,
                "soundcloud_url": t.soundcloud_url,
            })

    return Response({"songs": songs})
