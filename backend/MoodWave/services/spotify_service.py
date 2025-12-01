import requests
from django.conf import settings
from MoodWave.models import Track, UserProfile, Playlist, PlaylistTrack
from django.utils import timezone
from datetime import timedelta, time
from .lyric_analysis import fetch_lyrics, classify_lyrics_emotion
from .mood_classification import classify_mood
from MoodWave.services.soundcloud_service import search_soundcloud_track, get_related_tracks, sync_soundcloud_track
SPOTIFY_TOP_TRACKS_URL = "https://api.spotify.com/v1/me/top/tracks"
SPOTIFY_SEARCH_URL = "https://api.spotify.com/v1/search"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"


def refresh_access_token(profile: UserProfile):
    if profile.token_expires_at and profile.token_expires_at > timezone.now():
        return profile.access_token

    data = {
        "grant_type": "refresh_token",
        "refresh_token": profile.refresh_token,
        "client_id": settings.SPOTIFY_CLIENT_ID,
        "client_secret": settings.SPOTIFY_CLIENT_SECRET,
    }

    r = requests.post(SPOTIFY_TOKEN_URL, data=data)
    token_data = r.json()

    profile.access_token = token_data["access_token"]
    profile.token_expires_at = timezone.now() + timedelta(seconds=token_data.get("expires_in", 3600))
    profile.save(update_fields=["access_token", "token_expires_at"])
    return profile.access_token


def get_user_top_tracks(access_token, limit=30):
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"limit": limit, "time_range": "short_term"}

    r = requests.get(SPOTIFY_TOP_TRACKS_URL, headers=headers, params=params)
    print("STATUS:", r.status_code)
    #print("RAW RESPONSE:", r.text)
    data = r.json()

    tracks = []
    for item in data.get("items", []):
        artist_objs = item["artists"]

        artist_names = [a["name"] for a in artist_objs]
        artist_ids = [a.get("id") for a in artist_objs]

        tracks.append({
            "id": item["id"],
            "name": item["name"],
            "artists": artist_names,
            "artist_ids": artist_ids,
            "album_image": (
                item["album"]["images"][0]["url"]
                if item["album"]["images"] else ""
            ),
            "source": "Spotify",
        })

    return tracks





def get_artist_genres(access_token, artist_id):
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"https://api.spotify.com/v1/artists/{artist_id}"
    r = requests.get(url, headers=headers)
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


def build_user_audio_profile_from_spotify(profile, limit=30):
    access_token = refresh_access_token(profile)
    top_tracks = get_user_top_tracks(access_token, limit)

    profile.sync_in_progress = True
    profile.sync_done = 0
    profile.sync_total = limit
    profile.save(update_fields=["sync_in_progress", "sync_done", "sync_total"])

    for t in top_tracks:
        #Fetch Track object
        track_obj, created = Track.objects.get_or_create(
            user_profile=profile,
            spotify_id=t["id"],
            defaults={
                "name": t["name"],
                "artists": t["artists"],
                "artist_ids": t["artist_ids"],
                "album_image": t["album_image"],
                "source": "Spotify",
            }
        )

        if (not created
                and track_obj.lyrics
                and track_obj.mood
                and track_obj.genres):
            continue

        # If track is NEW(not in DB), process lyrics
        if (created or not track_obj.lyrics) and not track_obj.lyrics_missing:
            lyrics = fetch_lyrics(t["name"], ", ".join(t["artists"]))
            if lyrics:
                track_obj.lyrics = lyrics

                # emotion classification only if lyrics exist
                val, energy = classify_lyrics_emotion(lyrics)
                track_obj.lyric_valence = val
                track_obj.lyric_energy = energy

                # classify mood
                track_obj.mood = classify_mood(val, energy)
                print("Mood: " + track_obj.mood)
            else:
                track_obj.lyrics_missing = True

        # Fetching artist genre(for rec system)
        genres = []
        if t["artist_ids"]:
            genres = get_artist_genres(access_token, t["artist_ids"][0])
        track_obj.genres = genres

        # Fetch SoundCloud info
        if not track_obj.soundcloud_id or not track_obj.soundcloud_url:
            artists = " ".join(t["artists"])  # e.g. "Drake 21 Savage"
            query = f"{t['name']} {artists}".strip()

            sc = search_soundcloud_track(query, settings.SOUNDCLOUD_CLIENT_ID)
            print("SEARCH:", query, "=>", sc)

            if sc:
                track_obj.soundcloud_id = str(sc["soundcloud_id"])
                track_obj.soundcloud_url = sc["soundcloud_url"]

        track_obj.save()

        profile.sync_done += 1
        profile.save(update_fields=["sync_done"])


def recommend_tracks_for_mood(profile, selected_mood, limit=30):
    """
    RECOMMENDATION Algorithm
    1) Picks 5 "seed" tracks which are 5 of the user’s top tracks fetched from spotify and assigned a mood label 
       - these are used to search SoundCloud for related tracks.

    2) For each song:
         - fetch about 15 related SoundCloud tracks
         - sync each one to fit into our DB (mood assignment)
         - Apply genre filter (if selected)

    4) If we still don’t have enough songs,
       add songs that match the mood from our DB.

    """

    client_id = settings.SOUNDCLOUD_CLIENT_ID

    # Grab 5 mood-matched songs from DB (seeds)
    db_tracks = list(
        Track.objects.filter(
            user_profile=profile,
            mood=selected_mood
        ).order_by("-id")[:5]
    )

    print("\n--- RECOMMENDER DEBUG ---")
    print("Selected mood:", selected_mood)
    print("Initial tracks:", list(t.name for t in db_tracks))

    recommended_tracks = db_tracks.copy()
    track_ids = {track.id for track in db_tracks}

    # Check if user has songs with Soundcloud IDs
    seed_tracks = Track.objects.filter(
        user_profile=profile,
        mood=selected_mood,
        soundcloud_id__isnull=False
    )

    print("User seeds with SC ID:", list(seed_tracks.values("name", "soundcloud_id")))

    # use ANY song with a mood in DB that has a Soundcloud ID
    if not seed_tracks.exists():
        print("NO SEED TRACKS FOUND — using DB fallback only")
        seed_tracks = Track.objects.filter(
    user_profile=profile,
    mood=selected_mood,
    soundcloud_id__isnull=False
    )[:5]


    print("DB seeds with SC ID:", list(seed_tracks.values("name","soundcloud_id")))


    # If still no soundcloud recommendations return recommendations from DB
    if not seed_tracks.exists():
        if len(recommended_tracks) < limit:
            filler = Track.objects.filter(
                user_profile=profile,
                mood=selected_mood
            ).exclude(id__in=track_ids)[:limit - len(recommended_tracks)]

        return recommended_tracks

    # ------------------------------------------------------
    # Fetch related SoundCloud tracks
    # ------------------------------------------------------
    for seed in seed_tracks[:5]:
        related_sc_tracks = get_related_tracks(
            seed.soundcloud_id,
            client_id,
            limit=15,
        )
        print("→ Related tracks fetched:", len(related_sc_tracks))

        for sc_track in related_sc_tracks:

            # Make response compatible with our DB
            synced_track = sync_soundcloud_track(
                sc_track,
                selected_mood,# assign mood directly as it was fetched using that mood so logically it should be the same
                profile
            )
            if not synced_track:
                continue

            if synced_track.id in track_ids:
                continue

            # Add to recommendations
            recommended_tracks.append(synced_track)
            track_ids.add(synced_track.id)

            if len(recommended_tracks) >= limit:
                break

        if len(recommended_tracks) >= limit:
            break

    #  If still under the limit, add DB tracks
    if len(recommended_tracks) < limit:
        filler = Track.objects.filter(
            user_profile=profile,
            mood=selected_mood
        ).exclude(id__in=track_ids)[:limit - len(recommended_tracks)]

    return recommended_tracks
