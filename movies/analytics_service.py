from django.core.cache import cache

from analytics.models import (
    AnalyticsManager,
    BookingAnalytics,
    ExperienceAnalytics,
    MovieAnalytics,
    RevenueAnalytics,
)


class AnalyticsService:
    """Facade for movie admin analytics metrics and dashboard caching."""

    DASHBOARD_CACHE_KEY = "movies_admin_dashboard_metrics"
    DASHBOARD_CACHE_TIMEOUT = 60 * 60  # 1 hour

    @classmethod
    def get_dashboard_metrics(cls):
        metrics = cache.get(cls.DASHBOARD_CACHE_KEY)
        if metrics is not None:
            return metrics, True

        metrics = {
            "daily_revenue": AnalyticsManager.get_cached_data(
                "daily_revenue", RevenueAnalytics.get_daily_revenue
            ),
            "weekly_revenue": AnalyticsManager.get_cached_data(
                "weekly_revenue", RevenueAnalytics.get_weekly_revenue
            ),
            "monthly_revenue": AnalyticsManager.get_cached_data(
                "monthly_revenue", RevenueAnalytics.get_monthly_revenue
            ),
            "popular_movies": AnalyticsManager.get_cached_data(
                "popular_movies", MovieAnalytics.get_popular_movies
            ),
            "theater_occupancy": AnalyticsManager.get_cached_data(
                "theater_occupancy", MovieAnalytics.get_theater_occupancy
            ),
            "popular_experiences": AnalyticsManager.get_cached_data(
                "popular_experiences", ExperienceAnalytics.get_popular_experiences
            ),
            "venue_occupancy": AnalyticsManager.get_cached_data(
                "venue_occupancy", ExperienceAnalytics.get_venue_occupancy
            ),
            "booking_hours": AnalyticsManager.get_cached_data(
                "peak_booking_hours", BookingAnalytics.get_peak_booking_hours
            ),
            "cancellation_rate": AnalyticsManager.get_cached_data(
                "cancellation_rate", BookingAnalytics.get_cancellation_rate
            ),
        }

        cache.set(cls.DASHBOARD_CACHE_KEY, metrics, cls.DASHBOARD_CACHE_TIMEOUT)
        return metrics, False
