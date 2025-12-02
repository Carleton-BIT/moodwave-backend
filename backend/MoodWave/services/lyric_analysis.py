import requests, re, json
from bs4 import BeautifulSoup
from term_project import settings
from unidecode import unidecode


#---NORMALIZE PARAMS---
def normalize_parameter(s):
    # normalize artist/title to match Genius query params
    return unidecode(s).lower().strip()


#---FETCH LYRICS FROM GENIUS---
def fetch_lyrics(track_name, artist_name):
    base_url = "https://genius.com/"
    artists_raw = normalize_parameter(artist_name)
    title = normalize_parameter(track_name)

    # Remove things like "(Remix)"
    title = re.sub(r"\(.*?\)", "", title).strip()
    title = title.replace(",", "")

    # Split artists
    artist_list = [a.strip() for a in re.split(r",|&|feat\.|ft\.", artists_raw) if a.strip()]

    # Build all possible artist combinations (ex: partynextdoor-and-drake)
    artist_slugs = set()

    # For songs with one artist
    for a in artist_list:
        artist_slugs.add(a.replace(" ", "-").replace("'", ""))

    # For songs with multiple artists
    if len(artist_list) > 1:
        for i in range(len(artist_list)):
            for j in range(i + 1, len(artist_list)):
                a1 = artist_list[i].replace(" ", "-")
                a2 = artist_list[j].replace(" ", "-")
                artist_slugs.add(f"{a1}-and-{a2}")
                artist_slugs.add(f"{a2}-and-{a1}")

    artist_slugs.add(artists_raw.replace(" ", "-"))

    # Handle url variants
    base_title = title.replace("’", "").replace("'", "")
    title_variants = {
        base_title.replace("&", "and").replace(" ", "-"),
        base_title.replace("&", "").replace(" ", "-"),
        base_title.replace("&", "and").replace(" ", ""),
        base_title.replace(" ", "-"),
        base_title.replace(" ", ""),
    }

    for artist_slug in artist_slugs:
        for title_slug in title_variants:
            url = f"{base_url}{artist_slug}-{title_slug}-lyrics"
            print("Trying:", url)

            try:
                r = requests.get(url, timeout=6)
                if r.status_code != 200:
                    continue

                soup = BeautifulSoup(r.text, "html5lib")
                lyric_divs = soup.find_all(attrs={"data-lyrics-container": "true"})
                if not lyric_divs:
                    continue

                lyrics = "\n".join(div.get_text("\n") for div in lyric_divs)
                lyrics = re.sub(r"\n\n+", "\n", lyrics).strip()

                # remove irrelevant text
                irrelevant_phrases = [
                    "Contributors",
                    "Translations",
                    "Read more", "Read More",
                    "LyricsExplanation",
                ]

                cleaned_lines = []
                for line in lyrics.split("\n"):
                    stripped = line.strip()

                    # skip empty lines
                    if not stripped:
                        continue

                    # skip lines that contain irrelevant text
                    if any(t.lower() in stripped.lower() for t in irrelevant_phrases):
                        continue

                    # skip long explanation paragraphs
                    if len(stripped) > 160:
                        continue

                    cleaned_lines.append(stripped)

                lyrics = "\n".join(cleaned_lines).strip()

                if len(lyrics) > 50:
                    print(f"FOUND using: {artist_slug} / {title_slug}")
                    return lyrics

            except Exception as e:
                print("Error:", e)

    print(f"No lyrics found for {track_name} by {artist_name}")
    return None



#---HUGGINGFACE API CONFIG---
HF_API_URL = "https://router.huggingface.co/hf-inference/models/j-hartmann/emotion-english-distilroberta-base"
HF_HEADERS = {
    "Authorization": f"Bearer {getattr(settings, 'HF_API_KEY', '')}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}

# mapping emotion → (valence, energy)
EMOTION_TO_VALENCE_ENERGY = {
    "joy":        (+0.8, +0.4),
    "sadness":    (-0.7, -0.35),
    "anger":      (-0.6, +0.55),
    "fear":       (-0.4, +0.25),
    "surprise":   (+0.1, +0.4),
    "disgust":    (-0.45, +0.3),
    "neutral":    (-0.05, +0.05),
    "love":       (+0.4, +0.15),
}


#---CLASSIFY LYRICS EMOTION---
def classify_lyrics_emotion(lyrics: str):
    # This function sends the song lyrics to a HuggingFace emotion-classification model(https://huggingface.co/j-hartmann/emotion-english-distilroberta-base?text=we+was+supposed+to+be%2C+oh-ohTogether+forever+alreadyIm+getting+older%2C+pressures+getting+heavy)
    # HuggingFace runs a transformer that takes text and returns emotion labels like "joy", "sadness", etc.
    # We then map that predicted emotion into a (valence, energy) pair.

    if not lyrics:
        return 0.0, 0.0


    lyrics_text = lyrics[:1000] if len(lyrics) > 1000 else lyrics

    payload = {"inputs": lyrics_text}

    try:
        # call HuggingFace API
        resp = requests.post(HF_API_URL, headers=HF_HEADERS, data=json.dumps(payload), timeout=25)
        text = resp.text.strip()
        print("HF STATUS:", resp.status_code)
        print("HF PREVIEW:", text[:200])

        # empty body
        if not text:
            print("Empty response body")
            return 0.0, 0.0

        data = resp.json()

    except Exception as e:
        print("HF request failed:", e)
        return 0.0, 0.0

    # Handle errors returned
    if isinstance(data, dict) and "error" in data:
        print("HF model error:", data["error"])
        return 0.0, 0.0

    if isinstance(data, dict) and "0" in data:
        data = data["0"]

    if isinstance(data, list) and len(data) > 0 and isinstance(data[0], list):
        data = data[0]

    if not isinstance(data, list):
        print("Unexpected HF format:", data)
        return 0.0, 0.0

    # Sort them by score
    sorted_labels = sorted(data, key=lambda x: x.get("score", 0), reverse=True)

    # Pick top 2 emotions
    top_n = sorted_labels[:2]

    # Map average valence & energy from your top 2 emotions
    total_weight = sum([item["score"] for item in top_n])
    val = sum(EMOTION_TO_VALENCE_ENERGY.get(item["label"].lower().strip(), (0, 0))[0] * item["score"] for item in
              top_n) / total_weight
    energy = sum(EMOTION_TO_VALENCE_ENERGY.get(item["label"].lower().strip(), (0, 0))[1] * item["score"] for item in
                 top_n) / total_weight

    #Emotion Labels
    print("Top emotions:", [(i["label"], round(i["score"], 3)) for i in top_n])
    print(f"→ Valence={val:.3f}, Energy={energy:.3f}")

    return val, energy
