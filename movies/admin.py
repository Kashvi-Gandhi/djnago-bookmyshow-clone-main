from django.contrib import admin
from django.utils import timezone
from .models import Movie, Theater, Seat, Booking, Genre, Language, EmailQueue


@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug']
    search_fields = ['name', 'slug']
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Language)
class LanguageAdmin(admin.ModelAdmin):
    list_display = ['code', 'name']
    search_fields = ['code', 'name']


@admin.register(Movie)
class MovieAdmin(admin.ModelAdmin):
    list_display = ['name', 'rating', 'language', 'release_date', 'runtime', 'director']
    list_filter = ['language', 'genres', 'release_date']
    search_fields = ['name', 'cast', 'description', 'director']
    filter_horizontal = ['genres']
    autocomplete_fields = ['language']
    fieldsets = (
        (None, {"fields": ("name", "image", "rating", "language", "genres", "cast", "description")}),
        ("Movie Details", {"fields": ("release_date", "runtime", "director")}),
        ("Trailer", {"fields": ("trailer_url",)}),
    )


@admin.register(Theater)
class TheaterAdmin(admin.ModelAdmin):
    list_display = ['name', 'movie', 'time']


@admin.register(Seat)
class SeatAdmin(admin.ModelAdmin):
    list_display = ['theater', 'seat_number', 'is_booked']


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ['user', 'seat', 'movie', 'theater', 'booked_at']


@admin.register(EmailQueue)
class EmailQueueAdmin(admin.ModelAdmin):
    list_display = ['to_email', 'subject', 'status', 'attempts', 'scheduled_at', 'updated_at']
    list_filter = ['status', 'scheduled_at', 'attempts']
    search_fields = ['to_email', 'subject']
    readonly_fields = ['context', 'last_error', 'created_at', 'updated_at']
    actions = ['requeue_failed']

    @admin.action(description="Re-queue selected failed emails")
    def requeue_failed(self, request, queryset):
        count = queryset.filter(status='failed').update(
            status='queued',
            scheduled_at=timezone.now(),
            last_error='',
        )
        self.message_user(request, f"Re-queued {count} email(s).")
