import base64
import threading
from datetime import timedelta
import requests
from django.contrib import messages
from django.contrib.auth import logout as django_logout
from django.http import JsonResponse
from rest_framework import status
from rest_framework.authentication import TokenAuthentication
from rest_framework.authtoken.models import Token
from rest_framework.decorators import authentication_classes, permission_classes, api_view
from rest_framework.permissions import AllowAny, IsAuthenticated
from django.shortcuts import render, redirect
from django.utils import timezone
from rest_framework.response import Response
from term_project import settings
from .models import UserProfile, Track, Playlist, PlaylistTrack
from .services.moodDescriptions import MOOD_DESCRIPTIONS
from .services.spotify_service import build_user_audio_profile_from_spotify, get_user_top_tracks, \
    recommend_tracks_for_mood
from .services.api_serializer import RegisterSerializer


@api_view(['GET', 'POST'])
def index(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)

    # If no Spotify connected, ask user to connect
    if not profile.access_token:
        return redirect('spotify_login')

    if request.session.get("needs_sync", False):
        def run_sync():
            build_user_audio_profile_from_spotify(profile, limit=30)
            print("Background mood sync completed")

        threading.Thread(target=run_sync, daemon=True).start()
        request.session["needs_sync"] = False

        messages.info(request, "Syncing your vibe in the background…")

    moods = (
        Track.objects.filter(user_profile=profile)
        .values_list('mood', flat=True)
        .distinct()
    )

    genres = settings.MOODWAVE_GENRES
    songs = None

    if request.method == "POST":
        selected_mood = request.POST.get("mood")

        if selected_mood:

            songs = recommend_tracks_for_mood(
                profile=profile,
                selected_mood=selected_mood,
                limit=20
            )

            # Store playlist session
            request.session["current_playlist"] = [t.id for t in songs]
            request.session["current_playlist_mood"] = selected_mood

    sync_total = int(profile.sync_total or 0)
    sync_done = int(profile.sync_done or 0)
    progress_percent = 0
    if profile.sync_total > 0:
        progress_percent = int((sync_done / sync_total) * 100)

    context = {
        "moods": moods,
        "genres": genres,
        "songs": songs,
        # add converted numeric fields to template
        "sync_total": sync_total,
        "sync_done": sync_done,
        "sync_in_progress": profile.sync_in_progress,
        "progress_percent": progress_percent,
    }
    return render(request, "index.html", context)


@api_view(['GET'])
def api_top_tracks(request):
    profile = request.user.userprofile

    tracks = Track.objects.filter(user_profile=profile)[:3]

    data = []
    for t in tracks:
        data.append({
            "id": t.id,
            "spotify_id": t.spotify_id,
            "title": t.name,
            "artists": ", ".join(t.artists),
            "album_image": t.album_image,
            "mood": t.mood,
            "preview_url": t.soundcloud_url or "",  # player integration later
        })

    return JsonResponse({"top_tracks": data})


def player(request, position):
    playlist_ids = request.session.get("current_playlist", [])
    playlist_mood = request.session.get("current_playlist_mood")

    if not playlist_ids:
        messages.error(request, "No active playlist. Pick a mood first.")
        return redirect("index")

    try:
        position = int(position)
    except ValueError:
        position = 0

    if position < 0 or position >= len(playlist_ids):
        position = 0

    track = Track.objects.get(id=playlist_ids[position])

    prev_index = position - 1 if position > 0 else None
    next_index = position + 1 if position < len(playlist_ids) - 1 else None

    context = {
        "track": track,
        "position": position,
        "prev_index": prev_index,
        "next_index": next_index,
        "playlist_mood": playlist_mood,
        "playlist_length": len(playlist_ids),
    }
    return render(request, "player.html", context)

def save_playlist(request):
    if request.method != "POST":
        return redirect("index")

    playlist_ids = request.session.get("current_playlist", [])
    playlist_mood = request.session.get("current_playlist_mood")

    if not playlist_ids:
        messages.error(request, "No active playlist to save.")
        return redirect("index")

    name = request.POST.get("name") or (
        f"{playlist_mood} mix" if playlist_mood else "MoodWave Playlist"
    )

    # Create or get playlist for user with this name
    playlist, created = Playlist.objects.get_or_create(
        user_profile=request.user.userprofile,
        name=name,
    )

    # Reset tracks for this playlist so saving again overwrites
    PlaylistTrack.objects.filter(playlist=playlist).delete()

    for tid in playlist_ids:
        track = Track.objects.filter(id=tid, user_profile=request.user.userprofile).first()
        if track:
            PlaylistTrack.objects.get_or_create(
                playlist=playlist,
                track=track
            )

    messages.success(request, f"Playlist '{playlist.name}' saved to your profile.")
    return redirect('player', position=0)


def top_tracks(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    top_tracks = []

    if profile.access_token:
        # fetch top tracks from Spotify
        all_tracks = get_user_top_tracks(profile.access_token, limit=30)
        top_tracks = all_tracks[:5]

    return render(request, 'toptracks.html', {'profile': profile, 'top_tracks': top_tracks,})






def logout_view(request):
    django_logout(request)
    return redirect('login')


def create_playlist(user, playlist_name, track_ids):
    playlist, _ = Playlist.objects.get_or_create(
        user=user,
        name=playlist_name
    )

    for tid in track_ids:
        track = Track.objects.get(id=tid)
        PlaylistTrack.objects.get_or_create(
            playlist=playlist,
            track=track
        )

    return playlist

@api_view(["GET","POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([AllowAny])
def mood_sync_api(request):
    profile = request.user.userprofile

    # Reset counters
    profile.sync_in_progress = True
    profile.sync_done = 0
    profile.sync_total = 10
    profile.save()

    # Run the full sync (blocking)
    build_user_audio_profile_from_spotify(profile, limit=50)

    # Mark as done
    profile.sync_in_progress = False
    profile.save()

    # Mark profile as built
    if profile.tracks.count() > 10:
        profile.profile_built = True
        profile.save(update_fields=["profile_built"])

    return Response({"message": "Sync complete"})


#Spotify authentication
SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_PROFILE_URL = "https://api.spotify.com/v1/me"


@api_view(["GET"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def spotify_start(request):

    token = request.auth  # DRF stores token here if authenticated

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
    user_token = request.GET.get("state")  # token passed from spotify_start

    if error:
        return redirect("http://localhost:5173/connect-spotify?error=spotify")

    if not code or not user_token:
        return redirect("http://localhost:5173/connect-spotify?error=missing_state")

    # Get user from token
    try:
        token = Token.objects.get(key=user_token)
        user = token.user
    except Token.DoesNotExist:
        return redirect("http://localhost:5173/connect-spotify?error=bad_token")

    profile, _ = UserProfile.objects.get_or_create(user=user)

    # Exchange code for Spotify access token
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
    except Exception as e:
        return redirect("http://localhost:5173/connect-spotify?error=token_exchange")

    token_json = token_resp.json()
    access_token = token_json.get("access_token")
    refresh_token = token_json.get("refresh_token")
    expires_in = token_json.get("expires_in", 3600)

    if not access_token:
        return redirect("http://localhost:5173/connect-spotify?error=no_access")

    # Save tokens to profile
    profile.access_token = access_token
    profile.refresh_token = refresh_token
    profile.token_expires_at = timezone.now() + timedelta(seconds=int(expires_in))
    profile.save()

    # Redirect user back to app
    return redirect("http://localhost:5173/")



@api_view(["POST"])
@permission_classes([AllowAny])
def signUp(request):
    serializer = RegisterSerializer(data=request.data)

    if serializer.is_valid():
        user = serializer.save()

        # Create UserProfile automatically
        UserProfile.objects.create(user=user)

        # Option A: Return token after signup
        token, created = Token.objects.get_or_create(user=user)

        return Response(
            {
                "message": "Account created successfully.",
                "token": token.key,
                "username": user.username,
            },
            status=status.HTTP_201_CREATED
        )

    # If invalid, return clear errors
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

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
        Track.objects.filter(user_profile=profile)
        .exclude(mood__isnull=True)
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

    tracks = Track.objects.filter(user_profile=profile)


    # If no tracks yet
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

    # Convert to percentages
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
@permission_classes([IsAuthenticated])   # allow frontend calls
def api_recommendations(request):
    mood = request.GET.get("mood")

    if not mood:
        return Response({"error": "Mood is required"}, status=400)

    profile = None

    token = request.headers.get("Authorization")
    if token and token.startswith("Token "):
        try:
            token_key = token.split("Token ")[1]
            user = Token.objects.get(key=token_key).user
            profile = user.userprofile
        except:
            profile = None

    # If no authenticated user, fallback to DB-based mood recommendations
    print("PROFILE:", profile)
    if profile:
        recommended = recommend_tracks_for_mood(profile, mood, limit=30)

    else:
        recommended = []


    # Convert Track objects into JSON response
    songs = []

    for t in recommended:

        if not t.soundcloud_url:
            continue

        songs.append({
            "title": t.name,
            "artists": ", ".join(t.artists),
            "album_image": t.album_image,
            "soundcloud_url": t.soundcloud_url,
        })

    return Response({"songs": songs})


