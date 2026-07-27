import requests, re


SEARCH_URL = "https://api-v2.soundcloud.com/search/tracks"


def is_track_playable(sc_track):
    """
    Best-effort check that a SoundCloud track can actually be streamed in
    the embedded widget, filtering out paywalled (Go+), blocked, and
    preview-only results before they ever reach the player.

    SoundCloud's api-v2 is undocumented, so this is deliberately
    conservative - it only excludes tracks it's confident won't play,
    rather than requiring every field to be present.
    """
    if not isinstance(sc_track, dict):
        return False

    if sc_track.get("streamable") is False:
        return False

    policy = sc_track.get("policy")
    if policy in ("BLOCK", "BLOCKED", "SNIP"):
        return False

    media = sc_track.get("media")
    if isinstance(media, dict):
        transcodings = media.get("transcodings")
        if isinstance(transcodings, list) and len(transcodings) == 0:
            return False

    return True


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
        # Fetch a small pool of candidates instead of just one, since some
        # may be paywalled/blocked and get filtered out below.
        "limit": 5,
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

    track = next((t for t in results if is_track_playable(t)), None)
    if not track:
        return None

    return {
        "soundcloud_id": track.get("id"),
        "soundcloud_url": track.get("permalink_url"),
    }

RELATED_URL = "https://api-v2.soundcloud.com/tracks/{id}/related"


def get_related_tracks(soundcloud_id, client_id, limit=20):
    # Fetch extra raw results up front since paywalled/blocked tracks get
    # filtered out afterward - asking for exactly `limit` would often leave
    # fewer than `limit` usable tracks.
    fetch_limit = min(limit * 2, 200)

    params = {
        "client_id": client_id,
        "limit": fetch_limit,
    }

    try:
        r = requests.get(RELATED_URL.format(id=soundcloud_id), params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return []

    collection = data.get("collection", [])
    playable = [t for t in collection if is_track_playable(t)]
    return playable[:limit]
