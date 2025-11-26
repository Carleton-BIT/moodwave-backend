import base64
import threading
from datetime import timedelta
import requests
from django.contrib import messages
from django.contrib.auth import login, logout as django_logout
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.http import HttpResponse
from rest_framework.decorators import authentication_classes, permission_classes, api_view
from rest_framework.permissions import AllowAny
import time
from django.shortcuts import render, redirect
from django.utils import timezone
from term_project import settings
from .forms import SignUpForm
from .models import UserProfile, Track, Playlist, PlaylistTrack
from .services.spotify_service import build_user_audio_profile_from_spotify, get_user_top_tracks


@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
@login_required(login_url='/login')
def index(request):
    profile = request.user.userprofile

    # If no Spotify connected, ask user to connect
    if not profile.access_token:
        return redirect('spotify_login')

    if request.session.get("needs_sync", False):
        def run_sync():
            build_user_audio_profile_from_spotify(profile, limit=30)
            print("Background mood sync completed")

        threading.Thread(target=run_sync, daemon=True).start()
        request.session["needs_sync"] = False  # prevent running twice

        messages.info(request, "Syncing your vibe in the background…")

    moods = (
        Track.objects
        .values_list('mood', flat=True)
        .distinct()
        .order_by('mood')
    )

    genres = settings.MOODWAVE_GENRES
    songs = None

    if request.method == "POST":
        selected_mood = request.POST.get("mood")
        selected_genre = request.POST.get("genre")

        if selected_mood:
            from .services.spotify_service import recommend_tracks_for_mood

            songs = recommend_tracks_for_mood(
                profile=profile,
                selected_mood=selected_mood,
                selected_genre=selected_genre,
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




@login_required
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

@login_required
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
        track = Track.objects.filter(id=tid).first()
        if track:
            PlaylistTrack.objects.get_or_create(
                playlist=playlist,
                track=track
            )

    messages.success(request, f"Playlist '{playlist.name}' saved to your profile.")
    return redirect('player', position=0)


@login_required
def top_tracks(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    top_tracks = []

    if profile.access_token:
        # fetch top tracks from Spotify
        all_tracks = get_user_top_tracks(profile.access_token, limit=30)
        top_tracks = all_tracks[:5]

    return render(request, 'toptracks.html', {'profile': profile, 'top_tracks': top_tracks,})



def signup(request):
    if request.method == 'POST':
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            UserProfile.objects.create(user=user)
            login(request, user)
            return redirect('index')
    else:
        form = SignUpForm()
    return render(request, 'signup.html', {'form': form})


def logout_view(request):
    django_logout(request)
    return redirect('login')


@login_required
def mood_sync(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    build_user_audio_profile_from_spotify(profile, limit=30)
    messages.success(request, "Mood profile updated using lyrics")
    return redirect('index')

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

@login_required
def sync_progress(request):
    p = request.user.userprofile
    return JsonResponse({
        "in_progress": p.sync_in_progress,
        "done": p.sync_done,
        "total": p.sync_total,
        "percent": int((p.sync_done / max(p.sync_total, 1)) * 100),
    })



#Spotify authentication
SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_PROFILE_URL = "https://api.spotify.com/v1/me"

@login_required
def spotify_login(request):
    client_id = settings.SPOTIFY_CLIENT_ID
    redirect_uri = settings.SPOTIFY_REDIRECT_URI
    # scopes let us get top tracks and recently played
    scope = "user-top-read user-read-recently-played user-library-read user-read-private"

    params = {
        "response_type": "code",
        "client_id": client_id,
        "scope": scope,
        "redirect_uri": redirect_uri,
    }
    # build url
    url = SPOTIFY_AUTH_URL + "?" + "&".join(f"{k}={requests.utils.quote(v)}" for k, v in params.items())
    return redirect(url)


@login_required
def spotify_callback(request):
    error = request.GET.get("error")
    code = request.GET.get("code")

    if error:
        messages.error(request, f"Spotify auth error: {error}")
        return redirect('index')

    if not code:
        messages.error(request, "No authorization code returned from Spotify.")
        return redirect('index')

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
        token_resp = requests.post(SPOTIFY_TOKEN_URL, data=data, headers=headers, timeout=10)
        token_resp.raise_for_status()
        token_json = token_resp.json()
    except Exception as e:
        messages.error(request, f"Token exchange failed: {e}")
        return redirect('index')

    access_token = token_json.get("access_token")
    refresh_token = token_json.get("refresh_token")
    expires_in = token_json.get("expires_in", 3600)

    if not access_token:
        messages.error(request, "Spotify did not return an access token.")
        return redirect('index')

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    profile.access_token = access_token
    profile.refresh_token = refresh_token
    profile.token_expires_at = timezone.now() + timedelta(seconds=int(expires_in))
    profile.save()

    # Fetch spotify_id
    try:
        me = requests.get(SPOTIFY_PROFILE_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=10)
        me.raise_for_status()
        me_json = me.json()
        profile.spotify_id = me_json.get("id")
        profile.save()
    except Exception:
        pass

    return redirect('index')


