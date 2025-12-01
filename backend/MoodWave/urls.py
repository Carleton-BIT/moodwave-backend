from django.urls import path
from . import views
from rest_framework.authtoken.views import obtain_auth_token


urlpatterns = [
    path('', views.index, name='index'),
    path('toptracks/', views.top_tracks, name='top-tracks'),

    # API ROUTES
    path('api/top-tracks/', views.api_top_tracks, name='api_top_tracks'),
    path('api/spotify/start/', views.spotify_start, name='spotify_start'),
    path('api/spotify/start/', views.spotify_start, name='spotify_start'),

    path('spotify/callback/', views.spotify_callback, name='spotify_callback'),

    path('api/register/', views.signUp),

    path("api/user-info/", views.userInformation),
    path("login/token", obtain_auth_token),

    path("api/user-stats/", views.user_stats),


    path("api/mood-sync/", views.mood_sync_api),

    path("api/recommendations/", views.api_recommendations),


    # Player pages
    path('player/<int:position>/', views.player, name='player'),
    path('save-playlist/', views.save_playlist, name='save_playlist'),


]
