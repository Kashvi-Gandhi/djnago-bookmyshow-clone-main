from django.db import models
from django.core.cache import cache
from django.utils import timezone
from datetime import timedelta
from django.db.models import Count, Sum, Avg, F, Q, ExpressionWrapper, FloatField
from django.db.models.functions import TruncDate, TruncHour, ExtractHour, Cast

class AnalyticsManager:
    """
    Manager for optimized, cached analytics queries.
    Uses a hybrid approach: fast memory cache (LocMem) falling back 
    to persistent DB cache (AnalyticsCache) for heavy aggregations.
    """

    CACHE_KEY_PREFIX = 'analytics_'
    CACHE_TIMEOUT = 3600  # 1 hour for real-time charts

    @classmethod
    def get_cached_data(cls, key, query_func, *args, **kwargs):
        """Get data from cache or compute and cache it"""
        cache_key = f"{cls.CACHE_KEY_PREFIX}{key}"
        data = cache.get(cache_key)
        if data is None:
            # Try persistent DB cache
            from movies.models import AnalyticsCache
            data = AnalyticsCache.get_or_none(cache_key)
            
            if data is None:
                # Compute fresh data using DB aggregation
                data = query_func(*args, **kwargs)
                # Update both cache layers
                cache.set(cache_key, data, cls.CACHE_TIMEOUT)
                AnalyticsCache.set_cache(
                    cache_key=cache_key, 
                    data=data, 
                    cache_label=key if any(key == k[0] for k in AnalyticsCache.CACHE_KEYS) else "daily_revenue",
                    ttl_minutes=cls.CACHE_TIMEOUT // 60
                )
        return data

    @classmethod
    def invalidate_cache(cls, key=None):
        """Invalidate cache for specific key or all analytics cache"""
        if key:
            cache_key = f"{cls.CACHE_KEY_PREFIX}{key}"
            cache.delete(cache_key)
        else:
            cache.clear()
            from movies.models import AnalyticsCache
            AnalyticsCache.objects.all().delete()



class RevenueAnalytics:
    """Analytics for revenue data"""

    @staticmethod
    def get_daily_revenue(days=30):
        """Get daily revenue for the last N days"""
        from movies.models import Payment
        from experiences.models import Payment as ExpPayment

        end_date = timezone.now().date()
        start_date = end_date - timedelta(days=days)

        # Movies revenue
        movies_revenue = Payment.objects.filter(
            status='succeeded',
            created_at__date__gte=start_date,
            created_at__date__lte=end_date
        ).annotate(
            date=TruncDate('created_at')
        ).values('date').annotate(
            revenue=Sum('amount')
        ).order_by('date')

        # Experiences revenue
        exp_revenue = ExpPayment.objects.filter(
            status='succeeded',
            created_at__date__gte=start_date,
            created_at__date__lte=end_date
        ).annotate(
            date=TruncDate('created_at')
        ).values('date').annotate(
            revenue=Sum('amount')
        ).order_by('date')

        # Combine revenues
        revenue_dict = {}
        for item in movies_revenue:
            revenue_dict[item['date']] = item['revenue']

        for item in exp_revenue:
            date = item['date']
            revenue_dict[date] = revenue_dict.get(date, 0) + item['revenue']

        return sorted(revenue_dict.items())

    @staticmethod
    def get_weekly_revenue(weeks=12):
        """Get weekly revenue for the last N weeks"""
        from movies.models import Payment
        from experiences.models import Payment as ExpPayment
        from django.db.models.functions import TruncWeek

        end_date = timezone.now().date()
        start_date = end_date - timedelta(weeks=weeks)

        # Movies revenue
        movies_revenue = Payment.objects.filter(
            status='succeeded',
            created_at__date__gte=start_date,
            created_at__date__lte=end_date
        ).annotate(
            week=TruncWeek('created_at')
        ).values('week').annotate(
            revenue=Sum('amount')
        ).order_by('week')

        # Experiences revenue
        exp_revenue = ExpPayment.objects.filter(
            status='succeeded',
            created_at__date__gte=start_date,
            created_at__date__lte=end_date
        ).annotate(
            week=TruncWeek('created_at')
        ).values('week').annotate(
            revenue=Sum('amount')
        ).order_by('week')

        # Combine revenues
        revenue_dict = {}
        for item in movies_revenue:
            revenue_dict[item['week']] = item['revenue']

        for item in exp_revenue:
            week = item['week']
            revenue_dict[week] = revenue_dict.get(week, 0) + item['revenue']

        return sorted(revenue_dict.items())

    @staticmethod
    def get_monthly_revenue(months=12):
        """Get monthly revenue for the last N months"""
        from movies.models import Payment
        from experiences.models import Payment as ExpPayment
        from django.db.models.functions import TruncMonth

        end_date = timezone.now().date()
        start_date = end_date - timedelta(days=months*30)

        # Movies revenue
        movies_revenue = Payment.objects.filter(
            status='succeeded',
            created_at__date__gte=start_date,
            created_at__date__lte=end_date
        ).annotate(
            month=TruncMonth('created_at')
        ).values('month').annotate(
            revenue=Sum('amount')
        ).order_by('month')

        # Experiences revenue
        exp_revenue = ExpPayment.objects.filter(
            status='succeeded',
            created_at__date__gte=start_date,
            created_at__date__lte=end_date
        ).annotate(
            month=TruncMonth('created_at')
        ).values('month').annotate(
            revenue=Sum('amount')
        ).order_by('month')

        # Combine revenues
        revenue_dict = {}
        for item in movies_revenue:
            revenue_dict[item['month']] = item['revenue']

        for item in exp_revenue:
            month = item['month']
            revenue_dict[month] = revenue_dict.get(month, 0) + item['revenue']

        return sorted(revenue_dict.items())


class MovieAnalytics:
    """Analytics for movie bookings"""

    @staticmethod
    def get_popular_movies(limit=10):
        """Get most popular movies by booking count"""
        from movies.models import Booking

        return list(Booking.objects.filter(
            status__in=['confirmed', 'pending_payment']
        ).values(
            'movie__name', 'movie__id'
        ).annotate(
            booking_count=Count('id')
        ).order_by('-booking_count')[:limit])

    @staticmethod
    def get_theater_occupancy():
        """Get theater occupancy rates"""
        from movies.models import Theater

        return list(Theater.objects.annotate(
            total_seats=Count('seats'),
            booked_seats=Count('seats', filter=Q(seats__is_booked=True))
        ).annotate(
            occupancy_rate=ExpressionWrapper(
                Cast(F('booked_seats'), FloatField()) / Cast(F('total_seats'), FloatField()) * 100,
                output_field=FloatField()
            )
        ).filter(total_seats__gt=0).values(
            'name', 
            'movie__name', 
            'total_seats', 
            'booked_seats', 
            'occupancy_rate'
        ).order_by('-occupancy_rate'))


class ExperienceAnalytics:
    """Analytics for experience bookings"""

    @staticmethod
    def get_popular_experiences(limit=10):
        """Get most popular experiences by booking count"""
        from experiences.models import Booking

        return list(Booking.objects.filter(
            status__in=['confirmed', 'pending_payment']
        ).values(
            'experience__name', 'experience__id', 'experience__type'
        ).annotate(
            booking_count=Count('id')
        ).order_by('-booking_count')[:limit])

    @staticmethod
    def get_venue_occupancy():
        """Get venue occupancy rates"""
        from experiences.models import Session

        return list(Session.objects.annotate(
            total_seats=Count('seats'),
            booked_seats=Count('seats', filter=Q(seats__is_booked=True))
        ).annotate(
            occupancy_rate=ExpressionWrapper(
                Cast(F('booked_seats'), FloatField()) / Cast(F('total_seats'), FloatField()) * 100,
                output_field=FloatField()
            )
        ).filter(total_seats__gt=0).values(
            'venue__name',
            'experience__name',
            'start_time',
            'total_seats',
            'booked_seats',
            'occupancy_rate'
        ).order_by('-occupancy_rate'))


class BookingAnalytics:
    """Analytics for booking patterns"""

    @staticmethod
    def get_peak_booking_hours():
        """Get peak booking hours"""
        from movies.models import Booking
        from experiences.models import Booking as ExpBooking

        # Movies bookings
        movies_hours = Booking.objects.filter(
            status__in=['confirmed', 'pending_payment']
        ).annotate(
            hour=ExtractHour('booked_at')
        ).values('hour').annotate(
            booking_count=Count('id')
        ).order_by('hour')

        # Experience bookings
        exp_hours = ExpBooking.objects.filter(
            status__in=['confirmed', 'pending_payment']
        ).annotate(
            hour=ExtractHour('booked_at')
        ).values('hour').annotate(
            booking_count=Count('id')
        ).order_by('hour')

        # Combine hours
        hours_dict = {}
        for item in movies_hours:
            hours_dict[item['hour']] = item['booking_count']

        for item in exp_hours:
            hour = item['hour']
            hours_dict[hour] = hours_dict.get(hour, 0) + item['booking_count']

        return sorted(hours_dict.items())

    @staticmethod
    def get_cancellation_rate():
        """Get overall cancellation rate"""
        from movies.models import Booking
        from experiences.models import Booking as ExpBooking

        # Movies
        movies_total = Booking.objects.count()
        movies_cancelled = Booking.objects.filter(status='cancelled').count()

        # Experiences
        exp_total = ExpBooking.objects.count()
        exp_cancelled = ExpBooking.objects.filter(status='cancelled').count()

        total_bookings = movies_total + exp_total
        total_cancelled = movies_cancelled + exp_cancelled

        if total_bookings > 0:
            cancellation_rate = (total_cancelled / total_bookings) * 100
            return round(cancellation_rate, 2)

        return 0.0
