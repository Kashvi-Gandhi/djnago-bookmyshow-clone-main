from django.shortcuts import render, redirect
from functools import wraps
from django.urls import reverse
from django.http import JsonResponse
from django.views.decorators.cache import cache_page
from django.core.cache import cache
from django.utils import timezone
from .models import (
    AnalyticsManager, RevenueAnalytics, MovieAnalytics,
    ExperienceAnalytics, BookingAnalytics
)
from movies.models import AdminUser, AdminActivityLog


def admin_required(view_func):
    """
    Enhanced RBAC decorator to check if user has an active Admin profile.
    Prevents privilege escalation by verifying the AdminUser record.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            # For API calls, return 401. For page loads, redirect to login.
            if request.path.startswith('/analytics/api/') or request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'error': 'Unauthorized'}, status=401)
            return redirect(f"{reverse('login')}?next={request.path}")

        try:
            admin_profile = request.user.admin_profile
            
            # Strict RBAC: Use the model's permission property to check
            # if this specific admin role is allowed to view analytics.
            if not admin_profile.can_access_analytics:
                if request.path.startswith('/analytics/api/') or request.headers.get('x-requested-with') == 'XMLHttpRequest':
                    return JsonResponse({'error': 'Forbidden'}, status=403)
                return render(request, '403.html', status=403)
            
            # Log admin activity for audit trail
            AdminActivityLog.objects.create(
                admin_user=admin_profile,
                action="view_dashboard" if "api" not in request.path else "export_report",
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', '')[:500]
            )
            admin_profile.update_last_login()
            
        except AdminUser.DoesNotExist:
            if request.path.startswith('/analytics/api/') or request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'error': 'Forbidden'}, status=403)
            return render(request, '403.html', status=403)
        return view_func(request, *args, **kwargs)
    return wrapper


@admin_required
def analytics_dashboard(request):
    """
    Main analytics dashboard view.
    Initial load provides high-level stats; specific charts load via API.
    """
    context = {
        'title': 'Admin Analytics Dashboard',
        'admin_role': request.user.admin_profile.get_role_display(),
        'last_login': request.user.admin_profile.last_login_at,
    }
    return render(request, 'analytics/dashboard.html', context)


@admin_required
@cache_page(3600)  # Cache for 1 hour
def revenue_data_api(request):
    """API endpoint for revenue data"""
    period = request.GET.get('period', 'daily')

    if period == 'daily':
        data = AnalyticsManager.get_cached_data(
            'daily_revenue',
            RevenueAnalytics.get_daily_revenue
        )
    elif period == 'weekly':
        data = AnalyticsManager.get_cached_data(
            'weekly_revenue',
            RevenueAnalytics.get_weekly_revenue
        )
    elif period == 'monthly':
        data = AnalyticsManager.get_cached_data(
            'monthly_revenue',
            RevenueAnalytics.get_monthly_revenue
        )
    else:
        return JsonResponse({'error': 'Invalid period'}, status=400)

    return JsonResponse({'data': data})


@admin_required
@cache_page(3600)
def popular_movies_api(request):
    """API endpoint for popular movies"""
    data = AnalyticsManager.get_cached_data(
        'popular_movies',
        MovieAnalytics.get_popular_movies
    )
    return JsonResponse({'data': data})


@admin_required
@cache_page(3600)
def theater_occupancy_api(request):
    """API endpoint for theater occupancy"""
    raw = AnalyticsManager.get_cached_data(
        'theater_occupancy',
        MovieAnalytics.get_theater_occupancy
    )
    mapped = []
    for item in raw:
        mapped.append({
            'theater_name': item.get('name') or item.get('theater_name') or item.get('theater__name'),
            'movie_name': item.get('movie__name') or item.get('movie_name') or item.get('movie__title'),
            'total_seats': item.get('total_seats', 0),
            'booked_seats': item.get('booked_seats', 0),
            'occupancy_rate': round(item.get('occupancy_rate') or 0, 2),
        })
    return JsonResponse({'data': mapped})


@admin_required
@cache_page(3600)
def popular_experiences_api(request):
    """API endpoint for popular experiences"""
    data = AnalyticsManager.get_cached_data(
        'popular_experiences',
        ExperienceAnalytics.get_popular_experiences
    )
    return JsonResponse({'data': data})


@admin_required
@cache_page(3600)
def venue_occupancy_api(request):
    """API endpoint for venue occupancy"""
    raw = AnalyticsManager.get_cached_data(
        'venue_occupancy',
        ExperienceAnalytics.get_venue_occupancy
    )
    mapped = []
    for item in raw:
        mapped.append({
            'venue_name': item.get('venue__name') or item.get('venue_name') or item.get('venue'),
            'experience_name': item.get('experience__name') or item.get('experience_name') or item.get('experience__title'),
            'start_time': item.get('start_time'),
            'total_seats': item.get('total_seats', 0),
            'booked_seats': item.get('booked_seats', 0),
            'occupancy_rate': round(item.get('occupancy_rate') or 0, 2),
        })
    return JsonResponse({'data': mapped})


@admin_required
@cache_page(3600)
def booking_hours_api(request):
    """API endpoint for peak booking hours"""
    data = AnalyticsManager.get_cached_data(
        'peak_booking_hours',
        BookingAnalytics.get_peak_booking_hours
    )
    return JsonResponse({'data': data})


@admin_required
@cache_page(3600)
def cancellation_rate_api(request):
    """API endpoint for cancellation rate"""
    data = AnalyticsManager.get_cached_data(
        'cancellation_rate',
        BookingAnalytics.get_cancellation_rate
    )
    return JsonResponse({'data': data})


@admin_required
def invalidate_cache(request):
    """Invalidate all analytics cache"""
    AnalyticsManager.invalidate_cache()
    return JsonResponse({'message': 'Cache invalidated successfully'})
