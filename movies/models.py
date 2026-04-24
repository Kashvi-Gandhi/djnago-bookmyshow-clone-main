from django.contrib.auth.models import User
from django.conf import settings
from django.db import models
from django.utils import timezone
from django.core.exceptions import ValidationError
from urllib.parse import urlparse, parse_qs
import uuid
import re


class Genre(models.Model):
    """Genre taxonomy to support multi-select filtering."""

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["slug"]),
        ]

    def __str__(self) -> str:
        return self.name


class Language(models.Model):
    """Language lookup table for sargable filtering."""

    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["code"]),
            models.Index(fields=["name"]),
        ]

    def __str__(self) -> str:
        return self.name


class Movie(models.Model):
    name = models.CharField(max_length=255)
    image = models.ImageField(upload_to="movies/")
    rating = models.DecimalField(max_digits=3, decimal_places=1)
    cast = models.TextField()
    description = models.TextField(blank=True, null=True)  # optional
    release_date = models.DateField(blank=True, null=True, help_text="Movie release date")
    runtime = models.IntegerField(blank=True, null=True, help_text="Runtime in minutes")
    director = models.CharField(max_length=255, blank=True, null=True, help_text="Movie director name(s)")
    trailer_url = models.URLField(
        blank=True,
        null=True,
        help_text="Full YouTube URL (youtube.com or youtu.be). Stored safely as video ID.",
    )
    language = models.ForeignKey(
        Language,
        on_delete=models.PROTECT,
        related_name="movies",
        db_index=True,
    )
    genres = models.ManyToManyField(Genre, related_name="movies", blank=True)

    class Meta:
        ordering = ["-rating", "name"]
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["rating"]),
            models.Index(fields=["language"]),
            models.Index(fields=["language", "name"]),
            models.Index(fields=["name", "id"]),
            models.Index(fields=["rating", "id"]),
        ]

    def __str__(self) -> str:
        return self.name

    @staticmethod
    def _extract_youtube_id(url: str):
        """
        Extract the YouTube video ID from youtube.com/watch?v=... or youtu.be/<id>.
        Returns None if not parseable.
        """
        if not url:
            return None
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return None
        host = parsed.hostname or ""
        host = host.lower()
        allowed_hosts = {
            "www.youtube.com",
            "youtube.com",
            "m.youtube.com",
            "youtu.be",
        }
        if host not in allowed_hosts:
            return None
        if host == "youtu.be":
            vid = parsed.path.strip("/").split("/", 1)[0]
            return Movie._normalize_youtube_id(vid)
        qs = parse_qs(parsed.query)
        vid_list = qs.get("v", [])
        if vid_list:
            return Movie._normalize_youtube_id(vid_list[0])
        # Fallback for embed or /v/ style
        path_parts = parsed.path.split("/")
        if len(path_parts) >= 3 and path_parts[1] in {"embed", "v"}:
            return Movie._normalize_youtube_id(path_parts[2])
        # Support newer share formats like /shorts/<id> and /live/<id>
        if len(path_parts) >= 3 and path_parts[1] in {"shorts", "live"}:
            return Movie._normalize_youtube_id(path_parts[2])
        return None

    _YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")

    @staticmethod
    def _normalize_youtube_id(value: str):
        if not value:
            return None
        candidate = value.strip()
        if not candidate:
            return None
        # Defensive validation: keep IDs URL/path-safe and reasonably sized.
        if not Movie._YOUTUBE_ID_RE.match(candidate):
            return None
        return candidate

    def clean(self):
        super().clean()
        if self.trailer_url:
            vid = self._extract_youtube_id(self.trailer_url)
            if not vid:
                raise ValidationError(
                    {"trailer_url": "Enter a valid YouTube URL (youtube.com or youtu.be)."}
                )

    @property
    def trailer_video_id(self):
        return self._extract_youtube_id(self.trailer_url)

    @property
    def trailer_embed_url(self):
        vid = self.trailer_video_id
        if not vid:
            return None
        base = (getattr(settings, "YOUTUBE_EMBED_BASE", "") or "").strip()
        if not base:
            base = "https://www.youtube-nocookie.com/embed/"
        if not base.endswith("/"):
            base += "/"
        return f"{base}{vid}?rel=0&modestbranding=1"

    @property
    def trailer_watch_url(self):
        vid = self.trailer_video_id
        if not vid:
            return None
        return f"https://www.youtube.com/watch?v={vid}"

    @property
    def trailer_poster_url(self):
        vid = self.trailer_video_id
        if not vid:
            return None
        return f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"


class Theater(models.Model):
    name = models.CharField(max_length=255)
    movie = models.ForeignKey(
        Movie, on_delete=models.CASCADE, related_name="theaters"
    )
    time = models.DateTimeField()

    def __str__(self) -> str:
        return f"{self.name} - {self.movie.name} at {self.time}"


class Seat(models.Model):
    theater = models.ForeignKey(
        Theater, on_delete=models.CASCADE, related_name="seats"
    )
    seat_number = models.CharField(max_length=10)
    is_booked = models.BooleanField(default=False)

    def __str__(self) -> str:
        return f"{self.seat_number} in {self.theater.name}"


class SeatReservation(models.Model):
    """Temporary seat reservation with auto-expiry"""
    seat = models.OneToOneField(Seat, on_delete=models.CASCADE, related_name="reservation")
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        indexes = [
            models.Index(fields=["expires_at"]),
            models.Index(fields=["seat", "expires_at"]),
        ]

    def __str__(self) -> str:
        return f"Reservation for {self.seat.seat_number} by {self.user.username} until {self.expires_at}"

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at

    @classmethod
    def cleanup_expired(cls):
        """Delete expired reservations"""
        expired = cls.objects.filter(expires_at__lt=timezone.now())
        count = expired.count()
        expired.delete()
        return count


class Booking(models.Model):
    STATUS_CHOICES = [
        ("pending_payment", "Pending Payment"),
        ("confirmed", "Confirmed"),
        ("cancelled", "Cancelled"),
        ("refunded", "Refunded"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    seat = models.OneToOneField(Seat, on_delete=models.CASCADE)
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE)
    theater = models.ForeignKey(Theater, on_delete=models.CASCADE)
    booked_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending_payment", db_index=True)
    payment_id = models.CharField(max_length=64, default=uuid.uuid4, editable=False)

    def __str__(self) -> str:
        return (
            f"Booking by {self.user.username} for "
            f"{self.seat.seat_number} at {self.theater.name} ({self.status})"
        )


class Payment(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("succeeded", "Succeeded"),
        ("failed", "Failed"),
        ("cancelled", "Cancelled"),
        ("refunded", "Refunded"),
    ]

    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name="payment")
    stripe_payment_intent_id = models.CharField(max_length=255, unique=True)
    client_secret = models.CharField(max_length=255, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)  # Amount in INR
    currency = models.CharField(max_length=3, default="INR")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending", db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Payment {self.stripe_payment_intent_id} - {self.status}"


class PaymentAttempt(models.Model):
    """Tracks webhook attempts and idempotency"""
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="attempts")
    stripe_event_id = models.CharField(max_length=255, unique=True)  # Idempotency key
    event_type = models.CharField(max_length=100)
    payload = models.JSONField()
    processed_at = models.DateTimeField(auto_now_add=True)
    signature_verified = models.BooleanField(default=False)

    class Meta:
        unique_together = ["payment", "stripe_event_id"]

    def __str__(self) -> str:
        return f"Attempt {self.stripe_event_id} for {self.payment}"


class EmailQueue(models.Model):
    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("sent", "Sent"),
        ("failed", "Failed"),
    ]

    to_email = models.EmailField()
    subject = models.CharField(max_length=255)
    template = models.CharField(max_length=255)
    context = models.JSONField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued", db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    scheduled_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["scheduled_at", "id"]
        indexes = [
            models.Index(fields=["status", "scheduled_at"]),
        ]

    def __str__(self) -> str:
        return f"Email to {self.to_email} [{self.status}]"
