import requests, re


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
    Used For recommendations.
    """
    sc_id = sc_track.get("id")
    if not sc_id:
        return None

    sc_id_str = str(sc_id)
    title = sc_track.get("title") or "Unknown title"

    # Extract artists from title
    artists = extract_artists(title)
    if not artists:
        artists = [(sc_track.get("user") or {}).get("username", "Unknown Artist")]

    artwork = sc_track.get("artwork_url") or ""
    url = sc_track.get("permalink_url")

    # Return clean song object WITHOUT saving
    return {
        "spotify_id": f"sc-{sc_id_str}",
        "name": title,
        "artists": artists,
        "artist_ids": [],
        "album_image": artwork,
        "soundcloud_id": sc_id_str,
        "soundcloud_url": url,
        "source": "SoundCloud",
        "mood": selected_mood,
        "genres": [],
    }



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
