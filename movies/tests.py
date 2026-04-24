from django.test import Client, TestCase, TransactionTestCase
from django.test.utils import override_settings
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth.models import User
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta

from .models import Genre, Language, Movie, EmailQueue, Theater, Seat, SeatReservation, Booking, Payment
from .payment_service import reserve_seats, create_booking_with_payment_from_reservations, cleanup_expired_reservations


class MovieFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.lang_en, _ = Language.objects.get_or_create(code="en", name="English")
        cls.lang_hi, _ = Language.objects.get_or_create(code="hi", name="Hindi")

        cls.genre_action = Genre.objects.create(name="Action", slug="action")
        cls.genre_drama = Genre.objects.create(name="Drama", slug="drama")

        dummy_img = SimpleUploadedFile("test.jpg", b"filecontent", content_type="image/jpeg")

        m1 = Movie.objects.create(
            name="Alpha", rating=8.5, cast="", description="", language=cls.lang_en
        , image=dummy_img)
        m1.genres.add(cls.genre_action)

        dummy_img2 = SimpleUploadedFile("test2.jpg", b"filecontent", content_type="image/jpeg")
        m2 = Movie.objects.create(
            name="Bravo", rating=7.0, cast="", description="", language=cls.lang_en
        , image=dummy_img2)
        m2.genres.add(cls.genre_drama)

        dummy_img3 = SimpleUploadedFile("test3.jpg", b"filecontent", content_type="image/jpeg")
        m3 = Movie.objects.create(
            name="Charlie", rating=6.0, cast="", description="", language=cls.lang_hi
        , image=dummy_img3)
        m3.genres.add(cls.genre_action, cls.genre_drama)

    def setUp(self):
        self.client = Client()

    def _get_counts(self, params):
        resp = self.client.get(reverse("movie_list"), params)
        self.assertEqual(resp.status_code, 200)
        genres = {g.id: g.filtered_count for g in resp.context["genres"]}
        languages = {l.id: l.filtered_count for l in resp.context["languages"]}
        return genres, languages

    def test_counts_respect_language_filter(self):
        genres, languages = self._get_counts({"languages": [self.lang_en.id]})
        self.assertEqual(genres[self.genre_action.id], 1)
        self.assertEqual(genres[self.genre_drama.id], 1)
        # Language facet counts ignore the active language filter to show what is available if you change it.
        self.assertEqual(languages[self.lang_en.id], 2)
        self.assertEqual(languages[self.lang_hi.id], 1)

    def test_counts_respect_genre_filter(self):
        genres, languages = self._get_counts({"genres": [self.genre_action.id]})
        self.assertEqual(languages[self.lang_en.id], 1)
        self.assertEqual(languages[self.lang_hi.id], 1)

    def test_keyset_pagination_has_cursor(self):
        # add extra movies to exceed page size (12)
        lang = self.lang_en
        for i in range(20):
            Movie.objects.create(
                name=f"Movie {i}",
                rating=5 + i % 3,
                cast="",
                description="",
                language=lang,
                image=SimpleUploadedFile(f"more{i}.jpg", b"filecontent", content_type="image/jpeg"),
            )
        resp = self.client.get(
            reverse("movie_list"),
            {"pagination": "keyset", "sort": "rating_desc"},
        )
        self.assertEqual(resp.status_code, 200)
        movies = resp.context["movies"]
        self.assertEqual(len(movies), 12)
        self.assertTrue(resp.context["has_next"])
        self.assertIsNotNone(resp.context["next_cursor"])

    def test_per_page_is_clamped(self):
        resp = self.client.get(reverse("movie_list"), {"per_page": 500})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["per_page"], 48)

        resp = self.client.get(reverse("movie_list"), {"per_page": 1})
        self.assertEqual(resp.context["per_page"], 6)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class EmailQueueTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", email="kashvig2014@gmail.com", password="pw12345")

    def test_process_email_queue_marks_sent(self):
        EmailQueue.objects.create(
            to_email="kashvig2014@gmail.com",
            subject="Your tickets",
            template="emails/booking_confirmation",
            context={
                "user": {"username": "alice"},
                "movie": {"name": "Test Movie"},
                "theater": {"name": "Test Theater"},
                "show_time": timezone.now().isoformat(),
                "seats": ["A1", "A2"],
                "payment_ids": ["pid-1"],
                "booked_at": timezone.now().isoformat(),
            },
        )

        call_command("process_email_queue", limit=5, dry_run=True)
        task = EmailQueue.objects.get()
        self.assertEqual(task.status, "sent")
        self.assertEqual(task.attempts, 1)

    def test_failed_render_is_requeued_with_backoff(self):
        task = EmailQueue.objects.create(
            to_email="kashvig2014@gmail.com",
            subject="Broken template",
            template="emails/does_not_exist",
            context={},
        )

        call_command("process_email_queue", limit=1, dry_run=True)
        task.refresh_from_db()
        self.assertEqual(task.status, "queued")  # not failed yet, still retrying
        self.assertEqual(task.attempts, 1)
        self.assertGreater(task.scheduled_at, timezone.now())


class TrailerValidationTests(TestCase):
    def setUp(self):
        self.lang, _ = Language.objects.get_or_create(code="en", name="English")
        self.img = SimpleUploadedFile("poster.jpg", b"filecontent", content_type="image/jpeg")

    def _movie(self, trailer_url):
        return Movie(
            name="Trailer Movie",
            rating=8.0,
            cast="Somebody",
            description="",
            language=self.lang,
            image=self.img,
            trailer_url=trailer_url,
        )

    def test_valid_youtube_watch_url(self):
        m = self._movie("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        m.full_clean()  # should not raise
        self.assertEqual(m.trailer_video_id, "dQw4w9WgXcQ")
        self.assertIn("dQw4w9WgXcQ", m.trailer_embed_url)

    def test_valid_youtu_short_url(self):
        m = self._movie("https://youtu.be/dQw4w9WgXcQ")
        m.full_clean()
        self.assertEqual(m.trailer_video_id, "dQw4w9WgXcQ")

    def test_valid_youtube_shorts_url(self):
        m = self._movie("https://www.youtube.com/shorts/dQw4w9WgXcQ")
        m.full_clean()
        self.assertEqual(m.trailer_video_id, "dQw4w9WgXcQ")

    def test_valid_youtube_live_url(self):
        m = self._movie("https://www.youtube.com/live/dQw4w9WgXcQ?si=abc123")
        m.full_clean()
        self.assertEqual(m.trailer_video_id, "dQw4w9WgXcQ")

    def test_invalid_host_rejected(self):
        m = self._movie("https://vimeo.com/123")
        with self.assertRaises(ValidationError):
            m.full_clean()

    def test_missing_video_param_rejected(self):
        m = self._movie("https://www.youtube.com/watch?v=")
        with self.assertRaises(ValidationError):
            m.full_clean()


# ============================================================================
# CONCURRENCY-SAFE SEAT RESERVATION TESTS
# ============================================================================
# These tests demonstrate how race conditions are prevented and the system
# ensures consistency under concurrent load.
# ============================================================================

class SeatReservationConcurrencyTestCase(TransactionTestCase):
    """
    Tests for concurrent seat reservations demonstrating race condition prevention.
    Uses TransactionTestCase for actual database transaction testing.
    """
    
    def setUp(self):
        """Set up test data"""
        self.language, _ = Language.objects.get_or_create(code="en", defaults={"name": "English"})
        self.movie = Movie.objects.create(
            name="Test Movie",
            image=SimpleUploadedFile("test.jpg", b"content", content_type="image/jpeg"),
            rating=8.5,
            cast="Test Cast",
            language=self.language
        )
        
        self.theater = Theater.objects.create(
            name="Test Theater",
            movie=self.movie,
            time=timezone.now() + timedelta(hours=2)
        )
        
        # Create 10 seats
        self.seats = []
        for i in range(10):
            seat = Seat.objects.create(
                theater=self.theater,
                seat_number=f"A{i+1}",
                is_booked=False
            )
            self.seats.append(seat)
        
        self.user1 = User.objects.create_user(username="user1", password="pass123")
        self.user2 = User.objects.create_user(username="user2", password="pass123")

    def test_concurrent_same_seat_booking_prevents_double_booking(self):
        """
        RACE CONDITION TEST: Two users booking the SAME seat simultaneously
        
        CONSISTENCY GUARANTEE:
        - Only ONE user can book each seat
        - The other receives "seat not available" error
        
        MECHANISM:
        - SELECT FOR UPDATE acquires database lock
        - Lock queue ensures only one transaction wins
        - Loser's transaction sees seat already reserved
        """
        seat_id = self.seats[0].id
        results = []
        
        def book_seat(user):
            success, message = reserve_seats(user, [seat_id], self.theater.id)
            results.append({
                'user': user.username,
                'success': success,
                'message': message
            })
        
        # Execute both bookings in parallel (simulating simultaneous HTTP requests)
        with ThreadPoolExecutor(max_workers=2) as executor:
            executor.submit(book_seat, self.user1)
            executor.submit(book_seat, self.user2)
        
        # Exactly one should succeed
        successes = [r for r in results if r['success']]
        failures = [r for r in results if not r['success']]
        
        self.assertEqual(len(successes), 1, f"Expected 1 success, got {len(successes)}")
        self.assertEqual(len(failures), 1, f"Expected 1 failure, got {len(failures)}")
        
        # Verify only one reservation exists
        self.assertEqual(SeatReservation.objects.count(), 1)

    def test_concurrent_different_seats_both_succeed(self):
        """
        TEST: Two users booking DIFFERENT seats concurrently
        EXPECTED: Both succeed (no artificial bottleneck)
        """
        results = []
        
        def book_seat(user, seat_id):
            success, _ = reserve_seats(user, [seat_id], self.theater.id)
            results.append({'user': user.username, 'success': success})
        
        with ThreadPoolExecutor(max_workers=2) as executor:
            executor.submit(book_seat, self.user1, self.seats[0].id)
            executor.submit(book_seat, self.user2, self.seats[1].id)
        
        successes = [r for r in results if r['success']]
        self.assertEqual(len(successes), 2, "Both users should successfully book different seats")

    def test_atomic_multi_seat_booking_all_or_nothing(self):
        """
        ATOMICITY TEST: Booking multiple seats fails if ANY seat unavailable
        
        CONSISTENCY GUARANTEE:
        - No partial bookings
        - All seats booked together or none at all
        - Database never left in inconsistent state
        """
        # Pre-book one seat
        self.seats[1].is_booked = True
        self.seats[1].save()
        
        # Try to book 3 seats (one is already booked)
        seat_ids = [self.seats[0].id, self.seats[1].id, self.seats[2].id]
        success, error = reserve_seats(self.user1, seat_ids, self.theater.id)
        
        # Should fail completely
        self.assertFalse(success)
        
        # Verify NO reservations created (all-or-nothing guarantee)
        self.assertEqual(SeatReservation.objects.count(), 0)

    def test_cannot_double_book_after_check(self):
        """
        TOCTOU (Time-Of-Check-Time-Of-Use) TEST:
        User sees seat available, but another user books it before they do
        
        CONSISTENCY GUARANTEE:
        - Check + Book happens atomically
        - No TOCTOU gap - seat cannot be stolen between check and book
        """
        seat_id = self.seats[0].id
        
        # User 1 books first
        success1, _ = reserve_seats(self.user1, [seat_id], self.theater.id)
        self.assertTrue(success1)
        
        # User 2 tries to book same seat before User 1 completes payment
        success2, error = reserve_seats(self.user2, [seat_id], self.theater.id)
        self.assertFalse(success2)
        
        # Only 1 reservation exists
        self.assertEqual(SeatReservation.objects.count(), 1)


class ReservationTimeoutTestCase(TransactionTestCase):
    """Tests for automatic timeout and cleanup of expired reservations"""
    
    def setUp(self):
        """Set up test data"""
        self.language, _ = Language.objects.get_or_create(code="en", defaults={"name": "English"})
        self.movie = Movie.objects.create(
            name="Test Movie",
            image=SimpleUploadedFile("test.jpg", b"content", content_type="image/jpeg"),
            rating=8.5,
            cast="Test Cast",
            language=self.language
        )
        self.theater = Theater.objects.create(
            name="Test Theater",
            movie=self.movie,
            time=timezone.now() + timedelta(hours=2)
        )
        self.seat = Seat.objects.create(
            theater=self.theater,
            seat_number="A1",
            is_booked=False
        )
        self.user = User.objects.create_user(username="user1", password="pass123")

    def test_expired_reservations_cleaned_up(self):
        """
        TIMEOUT TEST: Expired reservations are automatically cleaned
        
        SCENARIO:
        - User reserves seat but closes app
        - Reservation expires after 2 minutes
        - Cleanup scheduler removes reservation
        - Seat becomes available for other users
        """
        # Create expired reservation
        SeatReservation.objects.create(
            seat=self.seat,
            user=self.user,
            expires_at=timezone.now() - timedelta(seconds=1)  # Already expired
        )
        
        self.assertEqual(SeatReservation.objects.count(), 1)
        
        # Run cleanup
        expired_count, _ = cleanup_expired_reservations()
        
        # Verify cleanup removed it
        self.assertEqual(expired_count, 1)
        self.assertEqual(SeatReservation.objects.count(), 0)

    def test_cleanup_idempotent(self):
        """
        IDEMPOTENCY TEST: Safe to run cleanup multiple times
        
        This is important for distributed systems where multiple
        cleanup workers might run simultaneously
        """
        # Create expired reservation
        SeatReservation.objects.create(
            seat=self.seat,
            user=self.user,
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        
        # First cleanup
        count1, _ = cleanup_expired_reservations()
        self.assertEqual(count1, 1)
        
        # Second cleanup - should find nothing
        count2, _ = cleanup_expired_reservations()
        self.assertEqual(count2, 0)
        
        # Third cleanup - still nothing
        count3, _ = cleanup_expired_reservations()
        self.assertEqual(count3, 0)

    def test_fresh_reservations_not_cleaned(self):
        """
        TEST: Fresh reservations are not removed during cleanup
        Only expired ones should be removed
        """
        # Create fresh reservation (expires in 1 minute)
        fresh = SeatReservation.objects.create(
            seat=self.seat,
            user=self.user,
            expires_at=timezone.now() + timedelta(minutes=1)
        )
        
        # Create expired reservation
        seat2 = Seat.objects.create(theater=self.theater, seat_number="A2")
        expired = SeatReservation.objects.create(
            seat=seat2,
            user=self.user,
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        
        # Run cleanup
        expired_count, _ = cleanup_expired_reservations()
        
        # Only expired should be removed
        self.assertEqual(expired_count, 1)
        self.assertEqual(SeatReservation.objects.count(), 1)
        
        # Fresh one should still exist
        self.assertTrue(SeatReservation.objects.filter(id=fresh.id).exists())
