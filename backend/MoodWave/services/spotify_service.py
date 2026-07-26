# MoodWave/services/spotify_service.py
import random
import requests
from datetime import timedelta, time

from django.conf import settings
from django.utils import timezone

from MoodWave.models import (
    GlobalTrack,
    UserTrack,
    UserProfile,
    Playlist,
    PlaylistTrack,
)
from .lyric_analysis import fetch_lyrics, classify_lyrics_emotion
from .mood_classification import classify_mood
from MoodWave.services.soundcloud_service import (
    search_soundcloud_track,
    get_related_tracks,
    sync_soundcloud_track,
    extract_artists,
)

SPOTIFY_TOP_TRACKS_URL = "https://api.spotify.com/v1/me/top/tracks"
SPOTIFY_SEARCH_URL = "https://api.spotify.com/v1/search"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"


def refresh_access_token(profile: UserProfile):
    if profile.token_expires_at and profile.token_expires_at > timezone.now():
        return profile.access_token

    if not profile.refresh_token:
        return None

    data = {
        "grant_type": "refresh_token",
        "refresh_token": profile.refresh_token,
        "client_id": settings.SPOTIFY_CLIENT_ID,
        "client_secret": settings.SPOTIFY_CLIENT_SECRET,
    }

    r = requests.post(SPOTIFY_TOKEN_URL, data=data)
    token_data = r.json()

    if "access_token" not in token_data:
        # Spotify rejected the refresh (revoked/expired/invalid) — bail out cleanly
        return None

    profile.access_token = token_data["access_token"]
    profile.token_expires_at = timezone.now() + timedelta(
        seconds=token_data.get("expires_in", 3600)
    )
    profile.save(update_fields=["access_token", "token_expires_at"])
    return profile.access_token

def get_user_top_tracks(access_token, limit=50):
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"limit": limit, "time_range": "short_term"}

    r = requests.get(SPOTIFY_TOP_TRACKS_URL, headers=headers, params=params)
    print("STATUS:", r.status_code)
    data = r.json()

    tracks = []
    for item in data.get("items", []):
        artist_objs = item["artists"]

        artist_names = [a["name"] for a in artist_objs]
        artist_ids = [a.get("id") for a in artist_objs]

        tracks.append(
            {
                "id": item["id"],
                "name": item["name"],
                "artists": artist_names,
                "artist_ids": artist_ids,
                "album_image": (
                    item["album"]["images"][0]["url"]
                    if item["album"]["images"]
                    else ""
                ),
                "source": "Spotify",
            }
        )

    return tracks


def get_artist_genres(access_token, artist_id):
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"https://api.spotify.com/v1/artists/{artist_id}"

    for attempt in range(3):  # retry up to 3 times
        try:
            r = requests.get(url, headers=headers, timeout=5)
            r.raise_for_status()
            return r.json().get("genres", [])
        except requests.exceptions.SSLError:
            time.sleep(0.4)
        except requests.exceptions.RequestException:
            break

    return []


def build_user_audio_profile_from_spotify(profile: UserProfile, limit=30):
    """
    Build/refresh the user's profile:
    - Fetch top Spotify tracks
    - Populate/refresh GlobalTrack (metadata, lyrics, mood, genres, SC info)
    - Ensure a UserTrack exists linking user <-> track
    """
    access_token = refresh_access_token(profile)
    top_tracks = get_user_top_tracks(access_token, limit)

    profile.sync_in_progress = True
    profile.sync_done = 0
    profile.sync_total = limit
    profile.save(update_fields=["sync_in_progress", "sync_done", "sync_total"])

    for t in top_tracks:
        # Global track for this Spotify ID
        global_track, created = GlobalTrack.objects.get_or_create(
            spotify_id=t["id"],
            defaults={
                "name": t["name"],
                "artists": t["artists"],
                "artist_ids": t["artist_ids"],
                "album_image": t["album_image"],
                "source": "Spotify",
            },
        )

        # If we already have mood/lyrics/genres etc, we can skip heavy work
        if (
            not created
            and global_track.lyrics
            and global_track.mood
            and global_track.genres
        ):
            # Still ensure the user has a UserTrack pointing here
            UserTrack.objects.get_or_create(
                user_profile=profile,
                track=global_track,
            )
            continue

        #  Lyrics + emotion + mood (only if missing and not flagged missing)
        if (created or not global_track.lyrics) and not global_track.lyrics_missing:
            lyrics = fetch_lyrics(t["name"], ", ".join(t["artists"]))
            if lyrics:
                global_track.lyrics = lyrics

                val, energy = classify_lyrics_emotion(lyrics)
                global_track.lyric_valence = val
                global_track.lyric_energy = energy

                global_track.mood = classify_mood(val, energy)
                print("Mood:", global_track.mood)
            else:
                global_track.lyrics_missing = True

        # Genres (if we have at least one Spotify artist)
        if t["artist_ids"] and not global_track.genres:
            genres = get_artist_genres(access_token, t["artist_ids"][0])
            global_track.genres = genres

        # SoundCloud info (if missing)
        if not global_track.soundcloud_id or not global_track.soundcloud_url:
            artists_str = " ".join(t["artists"])  # e.g. "Drake 21 Savage"
            query = f"{t['name']} {artists_str}".strip()

            sc = search_soundcloud_track(query, settings.SOUNDCLOUD_CLIENT_ID)
            print("SEARCH:", query, "=>", sc)

            if sc:
                global_track.soundcloud_id = str(sc["soundcloud_id"])
                global_track.soundcloud_url = sc["soundcloud_url"]

        global_track.save()

        # Ensure a UserTrack exists for this user
        UserTrack.objects.get_or_create(
            user_profile=profile,
            track=global_track,
        )

        profile.sync_done += 1
        profile.save(update_fields=["sync_done"])


def recommend_tracks_for_mood(profile: UserProfile, selected_mood: str, limit=30):
    """
    Returns Spotify + SoundCloud recommendations WITHOUT saving any
    of the recommended SoundCloud tracks to the database.
    Only GlobalTracks (Spotify seeds/filler) are DB objects.
    """

    client_id = settings.SOUNDCLOUD_CLIENT_ID

    # Select spotify seed tracks
    spotify_seed_qs = GlobalTrack.objects.filter(
        source="Spotify",
        mood=selected_mood,
        usertrack__user_profile=profile,
    ).distinct()

    spotify_seed = list(spotify_seed_qs)
    random.shuffle(spotify_seed)
    db_tracks = spotify_seed[:5]

    print("\n---DEBUG ---")
    print("Selected mood:", selected_mood)
    print("Initial seed tracks:", [t.name for t in db_tracks])

    # recommendations
    recommended = []

    # Track ID sets to avoid duplicates
    db_ids = set()  # GlobalTrack IDs
    sc_ids = set()


    for t in db_tracks:
        recommended.append(t)
        db_ids.add(t.id)
        if t.soundcloud_id:
            sc_ids.add(str(t.soundcloud_id))

    # Find global tracks with Souncloud ids to use as seeds
    seed_tracks_qs = GlobalTrack.objects.filter(
        mood=selected_mood,
        soundcloud_id__isnull=False,
        usertrack__user_profile=profile,
    ).distinct()

    seed_tracks = list(seed_tracks_qs)
    random.shuffle(seed_tracks)
    seed_tracks = seed_tracks[:5]

    print("SoundCloud-capable seeds:", [(t.name, t.soundcloud_id) for t in seed_tracks])

    # Fetch related soundcloud tracks
    for seed in seed_tracks:
        related_list = get_related_tracks(seed.soundcloud_id, client_id, limit=15)
        print("Related fetched:", len(related_list))

        for sc_track in related_list:
            synced = {
                "title": sc_track.get("title") or "Unknown title",
                "artists": ", ".join(
                    extract_artists(sc_track.get("title", ""))
                ) or (sc_track.get("user", {}) or {}).get(
                    "username", "Unknown Artist"
                ),
                "album_image": sc_track.get("artwork_url") or "",
                "soundcloud_id": str(sc_track.get("id")),
                "soundcloud_url": sc_track.get("permalink_url"),
            }

            scid = synced["soundcloud_id"]

            if scid in sc_ids:
                continue

            recommended.append(synced)
            sc_ids.add(scid)

            if len(recommended) >= limit:
                break

        if len(recommended) >= limit:
            break

    # Filler tracks from global db
    if len(recommended) < limit:
        filler_needed = limit - len(recommended)

        filler_qs = GlobalTrack.objects.filter(
            mood=selected_mood,
            usertrack__user_profile=profile,
        ).exclude(id__in=db_ids).distinct()[:filler_needed]

        filler = list(filler_qs)
        recommended.extend(filler)
        for f in filler:
            db_ids.add(f.id)


    return recommended
