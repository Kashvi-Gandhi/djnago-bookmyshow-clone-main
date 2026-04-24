from django.urls import path
from . import views

urlpatterns = [
    path('', views.movie_list, name='movie_list'),
    path('<int:movie_id>/', views.movie_detail, name='movie_detail'),
    path('<int:movie_id>/theaters', views.theater_list, name='theater_list'),
    path('theater/<int:theater_id>/seats/book/', views.book_seats, name='book_seats'),
    path('theater/<int:theater_id>/seats/api/availability/', views.seat_availability_api, name='seat_availability_api'),
    path('payment/<int:payment_id>/', views.payment_page, name='payment_page'),
    path('payment/<int:payment_id>/confirm/', views.payment_confirm, name='payment_confirm'),
    path('booking/<int:booking_id>/success/', views.booking_success, name='booking_success'),
    path('booking/<int:booking_id>/failed/', views.booking_failed, name='booking_failed'),
    path('webhooks/stripe/', views.stripe_webhook, name='stripe_webhook'),
]
