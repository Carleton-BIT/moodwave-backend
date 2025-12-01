from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    # Spotify tokens
    spotify_id = models.CharField(max_length=255, blank=True, null=True)
    access_token = models.CharField(max_length=512, blank=True, null=True)
    refresh_token = models.CharField(max_length=512, blank=True, null=True)
    token_expires_at = models.DateTimeField(blank=True, null=True)

    sync_total = models.IntegerField(default=0)
    sync_done = models.IntegerField(default=0)
    sync_in_progress = models.BooleanField(default=False)
    profile_built = models.BooleanField(default=False)
    profile_updated = models.BooleanField(default=False)

    avg_acousticness = models.FloatField(blank=True, null=True)
    avg_danceability = models.FloatField(blank=True, null=True)
    avg_energy = models.FloatField(blank=True, null=True)
    avg_valence = models.FloatField(blank=True, null=True)
    top_genres = models.JSONField(default=list, blank=True)

    last_profile_update = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return self.user.username

    def needs_profile_refresh(self, days=1):
        if not self.last_profile_update:
            return True
        return (timezone.now() - self.last_profile_update).days >= days


class Track(models.Model):
    user_profile = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="tracks"
    )

    spotify_id = models.CharField(max_length=255)
    name = models.CharField(max_length=255, blank=True, null=True)
    artists = models.JSONField(default=list, blank=True)
    artist_ids = models.JSONField(default=list, blank=True)
    album_image = models.URLField(blank=True, null=True)

    lyrics = models.TextField(blank=True, null=True)
    lyric_valence = models.FloatField(blank=True, null=True)
    lyric_energy = models.FloatField(blank=True, null=True)
    genres = models.JSONField(default=list, blank=True)
    mood = models.CharField(max_length=50, blank=True, null=True)
    lyrics_missing = models.BooleanField(default=False)

    soundcloud_url = models.URLField(blank=True, null=True)
    soundcloud_id = models.CharField(max_length=255, blank=True, null=True)
    source = models.CharField(max_length=50, default="")


    def __str__(self):
        return f"{self.name} — {', '.join(self.artists)}"



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


class PlaylistTrack(models.Model):
    playlist = models.ForeignKey(Playlist, on_delete=models.CASCADE)
    track = models.ForeignKey(Track, on_delete=models.CASCADE)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("playlist", "track")

    def __str__(self):
        return f"{self.playlist.name}: {self.track.name}"
