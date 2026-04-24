import re
import uuid
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

from django.contrib.auth.models import User
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class ExperienceType(models.TextChoices):
    EVENT = "event", "Event"
    PREMIERE = "premiere", "Premiere"
    MUSIC_STUDIO = "music_studio", "Music Studio"


class Experience(models.Model):
    name = models.CharField(max_length=255, db_index=True)
    type = models.CharField(max_length=32, choices=ExperienceType.choices, db_index=True)
    image = models.ImageField(upload_to="experiences/", blank=True, null=True)
    rating = models.DecimalField(max_digits=3, decimal_places=1, default=0)
    description = models.TextField(blank=True, null=True)
    trailer_url = models.URLField(blank=True, null=True)

    class Meta:
        ordering = ["-rating", "name"]
        indexes = [models.Index(fields=["type", "name"])]

    def __str__(self) -> str:
        return f"{self.name} ({self.type})"

    _YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")

    @staticmethod
    def _normalize_youtube_id(value: str):
        if not value:
            return None
        candidate = value.strip()
        if not candidate:
            return None
        if not Experience._YOUTUBE_ID_RE.match(candidate):
            return None
        return candidate

    @staticmethod
    def _extract_youtube_id(url: str):
        if not url:
            return None
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return None
        host = (parsed.hostname or "").lower()
        allowed_hosts = {"www.youtube.com", "youtube.com", "m.youtube.com", "youtu.be"}
        if host not in allowed_hosts:
            return None
        if host == "youtu.be":
            vid = parsed.path.strip("/").split("/", 1)[0]
            return Experience._normalize_youtube_id(vid)
        qs = parse_qs(parsed.query)
        vid_list = qs.get("v", [])
        if vid_list:
            return Experience._normalize_youtube_id(vid_list[0])
        path_parts = parsed.path.split("/")
        if len(path_parts) >= 3 and path_parts[1] in {"embed", "v", "shorts", "live"}:
            return Experience._normalize_youtube_id(path_parts[2])
        return None

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


class Venue(models.Model):
    name = models.CharField(max_length=255)
    city = models.CharField(max_length=120, blank=True, default="")
    address = models.TextField(blank=True, default="")

    def __str__(self) -> str:
        return f"{self.name}{' - ' + self.city if self.city else ''}"


class Session(models.Model):
    experience = models.ForeignKey(Experience, on_delete=models.CASCADE, related_name="sessions")
    venue = models.ForeignKey(Venue, on_delete=models.PROTECT, related_name="sessions")
    start_time = models.DateTimeField(db_index=True)
    ticket_price = models.DecimalField(max_digits=9, decimal_places=2, default=250.00)
    seat_map_enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["start_time", "id"]
        indexes = [
            models.Index(fields=["experience", "start_time"]),
            models.Index(fields=["venue", "start_time"]),
        ]

    def __str__(self) -> str:
        return f"{self.experience.name} @ {self.venue.name} ({self.start_time})"


class Seat(models.Model):
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="seats")
    seat_number = models.CharField(max_length=10)
    is_booked = models.BooleanField(default=False)

    class Meta:
        unique_together = ["session", "seat_number"]
        indexes = [models.Index(fields=["session", "is_booked"])]

    def __str__(self) -> str:
        return f"{self.seat_number} ({self.session_id})"


class SeatReservation(models.Model):
    seat = models.OneToOneField(Seat, on_delete=models.CASCADE, related_name="reservation")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="experience_seat_reservations")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at


class Booking(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="experience_bookings")
    seat = models.ForeignKey(Seat, on_delete=models.PROTECT)
    session = models.ForeignKey(Session, on_delete=models.PROTECT)
    experience = models.ForeignKey(Experience, on_delete=models.PROTECT)
    status = models.CharField(
        max_length=32,
        default="pending_payment",
        choices=[
            ("pending_payment", "Pending Payment"),
            ("confirmed", "Confirmed"),
            ("cancelled", "Cancelled"),
        ],
    )
    booked_at = models.DateTimeField(auto_now_add=True, db_index=True)
    payment_id = models.CharField(max_length=255, blank=True, default="", db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "booked_at"]),
            models.Index(fields=["payment_id"]),
        ]


class Payment(models.Model):
    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name="payment")
    stripe_payment_intent_id = models.CharField(max_length=255, unique=True)
    client_secret = models.CharField(max_length=255, blank=True)
    amount = models.DecimalField(max_digits=9, decimal_places=2)
    currency = models.CharField(max_length=10, default="INR")
    status = models.CharField(
        max_length=32,
        default="pending",
        choices=[
            ("pending", "Pending"),
            ("succeeded", "Succeeded"),
            ("failed", "Failed"),
            ("cancelled", "Cancelled"),
        ],
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)


class PaymentAttempt(models.Model):
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="attempts")
    stripe_event_id = models.CharField(max_length=255, unique=True)
    event_type = models.CharField(max_length=128)
    payload = models.BinaryField()
    signature_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ["payment", "stripe_event_id"]


class EmailQueue(models.Model):
    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("sent", "Sent"),
        ("failed", "Failed"),
    ]

    to_email = models.EmailField()
    subject = models.CharField(max_length=255)
    template = models.CharField(max_length=255)
    context = models.JSONField(default=dict)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued", db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True, default="")
    scheduled_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


def hold_deadline(created_at, hold_minutes: int):
    return created_at + timedelta(minutes=hold_minutes)
