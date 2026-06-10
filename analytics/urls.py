from django.urls import path
from . import views

app_name = 'analytics'


urlpatterns = [ 
    path('dashboard/', views.analytics_dashboard, name='dashboard'),
    path('revenue-trends/', views.revenue_trends_dashboard, name='revenue_trends'),
    path('movie-performance/', views.movie_performance_dashboard, name='movie_performance'),
    path('experience-insights/', views.experience_insights_dashboard, name='experience_insights'),
    path('api/revenue/', views.revenue_data_api, name='revenue_api'),
    path('api/popular-movies/', views.popular_movies_api, name='popular_movies_api'),
    path('api/theater-occupancy/', views.theater_occupancy_api, name='theater_occupancy_api'),
    path('api/popular-experiences/', views.popular_experiences_api, name='popular_experiences_api'),
    path('api/venue-occupancy/', views.venue_occupancy_api, name='venue_occupancy_api'),
    path('api/booking-hours/', views.booking_hours_api, name='booking_hours_api'),
    path('api/cancellation-rate/', views.cancellation_rate_api, name='cancellation_rate_api'),
    path('invalidate-cache/', views.invalidate_cache, name='invalidate_cache'),
]