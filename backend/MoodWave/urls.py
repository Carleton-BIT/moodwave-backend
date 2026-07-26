from django.urls import path
from . import views
from rest_framework.authtoken.views import obtain_auth_token


urlpatterns = [
    # API ROUTES
    path('api/top-tracks/', views.api_top_tracks, name='api_top_tracks'),

    path("api/sync-status/", views.sync_status),
    path('api/spotify/start/', views.spotify_start, name='spotify_start'),
    path('api/spotify/start/', views.spotify_start, name='spotify_start'),

    path('spotify/callback/', views.spotify_callback, name='spotify_callback'),

    path('api/register/', views.signUp),

    path("api/user-info/", views.userInformation),
    path("login/", views.login),

    path("api/user-stats/", views.user_stats),

    path("api/save-playlist/", views.api_save_playlist, name="api_save_playlist"),
    path("api/user-playlists/", views.user_playlists),
    path("api/get-playlist/", views.api_get_playlist),

    path("api/mood-sync/", views.mood_sync_api),

    path("api/recommendations/", views.api_recommendations),

]
