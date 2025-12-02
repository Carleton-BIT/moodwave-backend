from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


# ───────────────────────────────────────────────────────────────
# USER PROFILE
# ───────────────────────────────────────────────────────────────

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    # Spotify tokens
    spotify_id = models.CharField(max_length=255, blank=True, null=True)
    access_token = models.CharField(max_length=512, blank=True, null=True)
    refresh_token = models.CharField(max_length=512, blank=True, null=True)
    token_expires_at = models.DateTimeField(blank=True, null=True)

    # Sync tracking
    sync_total = models.IntegerField(default=0)
    sync_done = models.IntegerField(default=0)
    sync_in_progress = models.BooleanField(default=False)

    profile_built = models.BooleanField(default=False)
    profile_updated = models.BooleanField(default=False)

    top_genres = models.JSONField(default=list, blank=True)
    last_profile_update = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return self.user.username

    def needs_profile_refresh(self, days=1):
        if not self.last_profile_update:
            return True
        return (timezone.now() - self.last_profile_update).days >= days



# ───────────────────────────────────────────────────────────────
# GLOBAL TRACK (One per Spotify song)
# ───────────────────────────────────────────────────────────────

class GlobalTrack(models.Model):
    spotify_id = models.CharField(max_length=255, unique=True)

    # Core metadata
    name = models.CharField(max_length=255)
    artists = models.JSONField(default=list)
    artist_ids = models.JSONField(default=list)

    album_image = models.URLField(blank=True, null=True)
    genres = models.JSONField(default=list)

    # Lyrics and mood info
    lyrics = models.TextField(blank=True, null=True)
    lyric_valence = models.FloatField(blank=True, null=True)
    lyric_energy = models.FloatField(blank=True, null=True)
    mood = models.CharField(max_length=50, blank=True, null=True)
    lyrics_missing = models.BooleanField(default=False)

    # SoundCloud metadata
    soundcloud_url = models.URLField(blank=True, null=True)
    soundcloud_id = models.CharField(max_length=255, blank=True, null=True)

    source = models.CharField(max_length=50, default="Spotify")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} — {', '.join(self.artists)}"


# ───────────────────────────────────────────────────────────────
# USER TRACK (Per user, for user personalization)
# ───────────────────────────────────────────────────────────────

class UserTrack(models.Model):
    user_profile = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="user_tracks"
    )

    track = models.ForeignKey(GlobalTrack, on_delete=models.CASCADE)


    user_mood = models.CharField(max_length=50, blank=True, null=True)
    liked = models.BooleanField(default=False)
    last_played = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user_profile', 'track')

    def __str__(self):
        return f"{self.user_profile.user.username} — {self.track.name}"


# ───────────────────────────────────────────────────────────────
# PLAYLIST
# ───────────────────────────────────────────────────────────────

class Playlist(models.Model):
    user_profile = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="playlists"
    )
    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} — {self.user_profile.user.username}"


# ───────────────────────────────────────────────────────────────
# PLAYLIST TRACK
# ───────────────────────────────────────────────────────────────

class PlaylistTrack(models.Model):
    playlist = models.ForeignKey(Playlist, on_delete=models.CASCADE)
    track = models.ForeignKey(UserTrack, on_delete=models.CASCADE)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("playlist", "track")

    def __str__(self):
        return f"{self.playlist.name}: {self.track.track.name}"
