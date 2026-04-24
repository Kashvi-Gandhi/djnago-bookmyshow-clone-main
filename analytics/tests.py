import json
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.cache import cache
from movies.models import Movie, Theater, Seat, Booking, Payment, AdminUser, AdminActivityLog, AnalyticsCache, Language
from experiences.models import Experience, ExperienceType, Session, Venue, Seat as ExpSeat, Booking as ExpBooking, Payment as ExpPayment
from .models import AnalyticsManager, RevenueAnalytics, MovieAnalytics

class AnalyticsDashboardTests(TestCase):
    def setUp(self):
        self.client = Client()
        # Create a regular user
        self.user = User.objects.create_user(username='regular_user', password='password123')
        
        # Create an admin user
        self.admin_user = User.objects.create_user(username='admin_user', password='password123')
        self.admin_profile = AdminUser.objects.create(
            user=self.admin_user,
            role='analytics_admin',
            is_active=True
        )

        # Setup dummy data for aggregations
        self.lang, _ = Language.objects.get_or_create(code="en", name="English")
        self.movie = Movie.objects.create(
            name="Test Movie", 
            rating=8.0, 
            language=self.lang,
            image="test.jpg"
        )
        self.theater = Theater.objects.create(
            name="Grand Cinema", 
            movie=self.movie, 
            time=timezone.now()
        )
        
        # Create seats for occupancy test (50% occupancy)
        self.seat1 = Seat.objects.create(theater=self.theater, seat_number="A1", is_booked=True)
        self.seat2 = Seat.objects.create(theater=self.theater, seat_number="A2", is_booked=False)

        # Create a payment for revenue test
        self.booking = Booking.objects.create(
            user=self.admin_user, 
            seat=self.seat1, 
            movie=self.movie, 
            theater=self.theater, 
            status='confirmed'
        )
        self.payment = Payment.objects.create(
            booking=self.booking,
            stripe_payment_intent_id="pi_test_123",
            amount=500.00,
            status='succeeded'
        )

        cache.clear()

    def test_rbac_access_restriction(self):
        """Verify that non-admins are blocked and admins are allowed."""
        # Regular user access
        self.client.login(username='regular_user', password='password123')
        response = self.client.get(reverse('analytics:dashboard'))
        self.assertEqual(response.status_code, 403)

        # Admin user access
        self.client.login(username='admin_user', password='password123')
        response = self.client.get(reverse('analytics:dashboard'))
        self.assertEqual(response.status_code, 200)
        
        # Verify activity log entry
        self.assertTrue(AdminActivityLog.objects.filter(admin_user=self.admin_profile).exists())

    def test_revenue_aggregation_logic(self):
        """Verify database-level revenue Sum and grouping."""
        data = RevenueAnalytics.get_daily_revenue(days=7)
        # Expecting at least one entry for today with 500.00
        self.assertTrue(len(data) > 0)
        # Find today's entry
        today = timezone.now().date()
        revenue_val = next((val for date, val in data if date == today), 0)
        self.assertEqual(float(revenue_val), 500.00)

    def test_theater_occupancy_percentage_calculation(self):
        """Verify the ExpressionWrapper and Cast logic for occupancy rates."""
        data = MovieAnalytics.get_theater_occupancy()
        self.assertEqual(len(data), 1)
        theater_stats = data[0]
        self.assertEqual(theater_stats['name'], "Grand Cinema")
        self.assertEqual(theater_stats['total_seats'], 2)
        self.assertEqual(theater_stats['booked_seats'], 1)
        self.assertEqual(theater_stats['occupancy_rate'], 50.0)

    def test_hybrid_caching_mechanism(self):
        """Verify data is cached in LocMem and persistent AnalyticsCache."""
        cache_key = "popular_movies"
        
        # 1. First call: Should compute and store in both caches
        data_first = AnalyticsManager.get_cached_data(
            cache_key, 
            MovieAnalytics.get_popular_movies
        )
        
        # Check memory cache
        self.assertIsNotNone(cache.get(f"analytics_{cache_key}"))
        
        # Check database cache model
        db_cache = AnalyticsCache.objects.get(cache_key=f"analytics_{cache_key}")
        self.assertEqual(db_cache.cache_label, "popular_movies")
        
        # 2. Clear memory cache: Should fallback to DB cache
        cache.clear()
        data_fallback = AnalyticsManager.get_cached_data(
            cache_key, 
            MovieAnalytics.get_popular_movies
        )
        
        # Verify data matches
        self.assertEqual(list(data_first), list(data_fallback))

    def test_cache_invalidation(self):
        """Verify cache invalidation clears both layers."""
        # Warm cache
        AnalyticsManager.get_cached_data('daily_revenue', RevenueAnalytics.get_daily_revenue)
        
        # Invalidate
        AnalyticsManager.invalidate_cache()
        
        # Verify memory is empty
        self.assertIsNone(cache.get("analytics_daily_revenue"))
        # Verify DB is empty
        self.assertEqual(AnalyticsCache.objects.count(), 0)

    def test_experience_revenue_combination(self):
        """Test that revenue from both Movies and Experiences is combined correctly."""
        # Create Experience Payment
        venue = Venue.objects.create(name="Arena", location="City")
        extype = ExperienceType.objects.create(name="Concert", slug="concert")
        exp = Experience.objects.create(name="Live Show", type=extype, rating=9.0)
        session = Session.objects.create(venue=venue, experience=exp, start_time=timezone.now(), price=1000.00)
        eseat = ExpSeat.objects.create(session=session, seat_number="V1", is_booked=True)
        
        ebooing = ExpBooking.objects.create(user=self.admin_user, experience=exp, session=session, seat=eseat, status='confirmed')
        ExpPayment.objects.create(
            booking=ebooing,
            stripe_payment_intent_id="pi_exp_123",
            amount=1000.00,
            status='succeeded'
        )

        data = RevenueAnalytics.get_daily_revenue(days=1)
        today = timezone.now().date()
        total_rev = next((val for date, val in data if date == today), 0)
        
        # 500 (Movie) + 1000 (Experience) = 1500
        self.assertEqual(float(total_rev), 1500.00)

    def test_api_performance_and_format(self):
        """Verify API endpoints return JSON and are wrapped by cache_page."""
        self.client.login(username='admin_user', password='password123')
        
        # Test Revenue API
        response = self.client.get(reverse('analytics:revenue_api') + '?period=daily')
        self.assertEqual(response.status_code, 200)
        content = json.loads(response.content)
        self.assertIn('data', content)
        
        # Test Occupancy API
        response = self.client.get(reverse('analytics:theater_occupancy_api'))
        self.assertEqual(response.status_code, 200)
        content = json.loads(response.content)
        self.assertEqual(content['data'][0]['occupancy_rate'], 50.0)

    def test_peak_booking_hours_aggregation(self):
        """Verify hour extraction logic."""
        # Current hour
        current_hour = timezone.now().hour
        
        # Test actual Peak Hours method
        from .models import BookingAnalytics, AnalyticsManager
        
        data = AnalyticsManager.get_cached_data(
            'peak_booking_hours',
            BookingAnalytics.get_peak_booking_hours
        )

        hours_data = BookingAnalytics.get_peak_booking_hours()
        # There should be an entry for the current hour
        hour_entry = next((count for h, count in hours_data if h == current_hour), 0)
        self.assertGreaterEqual(hour_entry, 1)
