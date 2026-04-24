from django.contrib import admin

from .models import (
    Booking,
    EmailQueue,
    Experience,
    Payment,
    Seat,
    SeatReservation,
    Session,
    Venue,
)


@admin.register(Experience)
class ExperienceAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "type", "rating")
    list_filter = ("type",)
    search_fields = ("name",)


@admin.register(Venue)
class VenueAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "city")
    search_fields = ("name", "city")


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ("id", "experience", "venue", "start_time", "ticket_price", "seat_map_enabled")
    list_filter = ("seat_map_enabled", "venue__city")
    search_fields = ("experience__name", "venue__name")


@admin.register(Seat)
class SeatAdmin(admin.ModelAdmin):
    list_display = ("id", "session", "seat_number", "is_booked")
    list_filter = ("is_booked",)


@admin.register(SeatReservation)
class SeatReservationAdmin(admin.ModelAdmin):
    list_display = ("id", "seat", "user", "expires_at", "created_at")
    list_filter = ("expires_at",)


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "experience", "session", "seat", "status", "booked_at", "payment_id")
    list_filter = ("status",)
    search_fields = ("user__username", "experience__name", "payment_id")


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("id", "booking", "stripe_payment_intent_id", "status", "amount", "created_at")
    list_filter = ("status",)
    search_fields = ("stripe_payment_intent_id",)


@admin.register(EmailQueue)
class EmailQueueAdmin(admin.ModelAdmin):
    list_display = ("id", "to_email", "subject", "status", "attempts", "scheduled_at", "updated_at")
    list_filter = ("status",)
    search_fields = ("to_email", "subject")

# Register your models here.
