import requests, re
from .lyric_analysis import fetch_lyrics, classify_lyrics_emotion
from django.conf import settings
from MoodWave.models import Track
from .mood_classification import classify_mood


SEARCH_URL = "https://api-v2.soundcloud.com/search/tracks"


def extract_artists(title):
    """
    Fetch names from SoundCloud title.
    To help with lyric fetching function
    """
    if not title:
        return []

    # Normalize title separators
    cleaned = title.replace("—", "-").replace("–", "-")

    # separate artists from song title
    if "-" not in cleaned:
        return []

    artist_section = cleaned.split("-")[0].strip()

    # Split multi-artist patterns
    parts = re.split(r"x|X|&|feat\.?|ft\.?", artist_section)

    # Clean and filter
    artists = [p.strip() for p in parts if p.strip()]

    return artists

def sync_soundcloud_track(sc_track, selected_mood):
    """
    Light-weight sync for recommendation use:
    - ensures Track exists
    - assigns the *selected_mood* directly (no lyric analysis)
    - enriches genres from SoundCloud tags
    """
    sc_id = sc_track.get("id")
    if not sc_id:
        return None

    sc_id_str = str(sc_id)

    # Try to find existing track by soundcloud_id
    track_obj = Track.objects.filter(soundcloud_id=sc_id_str).first()

    # If not found, create a new Track
    if not track_obj:
        title = sc_track.get("title") or "Unknown title"

        # Extract artist names from the title
        artists = extract_artists(title)
        # Fallback to uploader username if no artists detected
        if not artists:
            artists = [(sc_track.get("user") or {}).get("username", "Unknown Artist")]

        artwork = sc_track.get("artwork_url") or ""

        track_obj = Track.objects.create(
            spotify_id=f"sc-{sc_id_str}",   # fake ID so field is not empty
            name=title,
            artists=artists,
            artist_ids=[],
            album_image=artwork,
            soundcloud_id=sc_id_str,
            soundcloud_url=sc_track.get("permalink_url"),
            source="soundcloud",
            mood=selected_mood,
            lyrics_missing=True,            # we are skipping lyrics
        )
    else:
        # If we already have it but no mood, assign current selection
        if not track_obj.mood:
            track_obj.mood = selected_mood
    if track_obj.genres is None:
        track_obj.genres = []

    track_obj.save()
    return track_obj


def search_soundcloud_track(track_title, client_id):
    params = {
        "q": track_title,
        "client_id": client_id,
        "limit": 1,
    }

    try:
        r = requests.get(SEARCH_URL, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return None

    results = data.get("collection", [])
    if not results:
        return None

    track = results[0]

    return {
        "soundcloud_id": track.get("id"),
        "soundcloud_url": track.get("permalink_url"),
    }

RELATED_URL = "https://api-v2.soundcloud.com/tracks/{id}/related"


def get_related_tracks(soundcloud_id, client_id, limit=20):
    params = {
        "client_id": client_id,
        "limit": limit,
    }

    try:
        r = requests.get(RELATED_URL.format(id=soundcloud_id), params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return []

    return data.get("collection", [])
