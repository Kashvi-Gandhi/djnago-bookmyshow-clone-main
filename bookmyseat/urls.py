from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static
from django.views.static import serve
from experiences import views as experience_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('users/', include('users.urls')),
    path('',include('users.urls')),
    path('movies/', include('movies.urls')),
    path('experiences/', include('experiences.urls')),
    # Convenience routes to match BookMyShow-style sections
    path('events/', experience_views.experience_list, {"type_slug": "event"}, name="events"),
    path('premieres/', experience_views.experience_list, {"type_slug": "premiere"}, name="premieres"),
    path('music-studios/', experience_views.experience_list, {"type_slug": "music_studio"}, name="music_studios"),
]

# Serve media in dev and fallback for non-debug environments.
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
if not settings.DEBUG:
    urlpatterns += [
        re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
    ]
