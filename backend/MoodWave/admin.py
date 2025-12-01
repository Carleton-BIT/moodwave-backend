from django.contrib import admin
from django.contrib.auth import models

from .models import (
    UserProfile,
    Track,
    Playlist,
    PlaylistTrack,
)


# -----------------------------------------------------
# UserProfile Admin
# -----------------------------------------------------
class UserPlaylistInline(admin.TabularInline):
    model = Playlist
    extra = 1
    fields = ("name", "created_at")
    readonly_fields = ("created_at",)



@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "spotify_id", "created_at", "last_profile_update")
    search_fields = ("user__username", "spotify_id")
    list_filter = ("created_at", "last_profile_update")
    readonly_fields = ("created_at", "last_profile_update")
    inlines = [UserPlaylistInline]

# -----------------------------------------------------
# Track Admin
# -----------------------------------------------------
@admin.register(Track)
class TrackAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "primary_artist",
        "mood",
        "spotify_id",
        "soundcloud_id",
        "source",
        "lyrics_present",
    )
    search_fields = ("name", "artists", "spotify_id", "soundcloud_id", "mood")
    list_filter = ("mood", "source", "lyrics_missing")
    readonly_fields = ("spotify_id",)

    def primary_artist(self, obj):
        return ", ".join(obj.artists) if obj.artists else "Unknown"

    def lyrics_present(self, obj):
        return bool(obj.lyrics) and not obj.lyrics_missing
    lyrics_present.boolean = True
    lyrics_present.short_description = "Has Lyrics?"


# -----------------------------------------------------
# PlaylistTrack Inline (Appear inside Playlist)
# -----------------------------------------------------
class PlaylistTrackInline(admin.TabularInline):
    model = PlaylistTrack
    extra = 1
    autocomplete_fields = ("track",)



# -----------------------------------------------------
# Playlist Admin
# -----------------------------------------------------
@admin.register(Playlist)
class PlaylistAdmin(admin.ModelAdmin):
    list_display = ("name", "user_profile", "created_at")
    search_fields = ("name", "user_profile__user__username")
    list_filter = ("created_at",)

    inlines = [PlaylistTrackInline]


# -----------------------------------------------------
# PlaylistTrack Admin
# -----------------------------------------------------
@admin.register(PlaylistTrack)
class PlaylistTrackAdmin(admin.ModelAdmin):
    list_display = ("playlist", "track", "added_at")
    search_fields = ("playlist__name", "track__name")
    list_filter = ("added_at",)



