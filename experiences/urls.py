from django.urls import path

from . import views

urlpatterns = [
    path("", views.experience_home, name="experience_home"),
    path("events/", views.experience_list, {"type_slug": "event"}, name="event_list"),
    path("premieres/", views.experience_list, {"type_slug": "premiere"}, name="premiere_list"),
    path("music-studios/", views.experience_list, {"type_slug": "music_studio"}, name="music_studio_list"),
    path("<int:experience_id>/", views.experience_detail, name="experience_detail"),
    path("session/<int:session_id>/seats/book/", views.book_seats, name="experience_book_seats"),
    path("session/<int:session_id>/seats/api/availability/", views.seat_availability_api, name="experience_seat_availability_api"),
    path("payment/<int:payment_id>/", views.payment_page, name="experience_payment_page"),
    path("payment/<int:payment_id>/confirm/", views.payment_confirm, name="experience_payment_confirm"),
    path("booking/<uuid:booking_id>/success/", views.booking_success, name="experience_booking_success"),
    path("booking/<uuid:booking_id>/failed/", views.booking_failed, name="experience_booking_failed"),
    path("webhooks/stripe/", views.stripe_webhook, name="experience_stripe_webhook"),
]

