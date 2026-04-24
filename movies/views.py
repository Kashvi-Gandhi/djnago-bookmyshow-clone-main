import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Subquery
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from time import perf_counter

from .models import Booking, EmailQueue, Genre, Language, Movie, Seat, Theater, SeatReservation

logger = logging.getLogger(__name__)
perf_logger = logging.getLogger("movies.perf")

def movie_list(request):
    perf_start = perf_counter()
    search_query = request.GET.get("search", "").strip()
    genre_ids = [gid for gid in request.GET.getlist("genres") if gid.isdigit()]
    language_ids = [lid for lid in request.GET.getlist("languages") if lid.isdigit()]
    sort_param = request.GET.get("sort", "name_asc")
    # Default to offset/page-number pagination; keyset can be opted in via querystring.
    use_keyset = request.GET.get("pagination") == "keyset"
    try:
        per_page = int(request.GET.get("per_page", 12))
    except ValueError:
        per_page = 12
    per_page = max(6, min(per_page, 48))
    cursor = request.GET.get("cursor")

    queryset = Movie.objects.select_related("language").prefetch_related("genres")

    if search_query:
        # Use prefix search to stay sargable on the (name) index.
        queryset = queryset.filter(name__istartswith=search_query)

    base_for_results = queryset
    if language_ids:
        base_for_results = base_for_results.filter(language_id__in=language_ids)
    filtered_query = base_for_results
    if genre_ids:
        filtered_query = filtered_query.filter(genres__id__in=genre_ids)

    sort_options = {
        "name_asc": "name",
        "name_desc": "-name",
        "rating_desc": "-rating",
        "rating_asc": "rating",
    }
    order_by = sort_options.get(sort_param, "name")
    filtered_query = filtered_query.order_by(order_by, "id").distinct()

    # Live counts, excluding the facet being counted to show "what's available if you add this filter"
    base_genre_counts = queryset
    if language_ids:
        base_genre_counts = base_genre_counts.filter(language_id__in=language_ids)
    base_genre_ids = Subquery(base_genre_counts.distinct().values("id"))

    base_language_counts = queryset
    if genre_ids:
        base_language_counts = base_language_counts.filter(genres__id__in=genre_ids)
    base_language_ids = Subquery(base_language_counts.distinct().values("id"))

    genres_with_counts = Genre.objects.annotate(
        filtered_count=Count(
            "movies",
            filter=Q(movies__id__in=base_genre_ids),
            distinct=True,
        )
    )
    languages_with_counts = Language.objects.annotate(
        filtered_count=Count(
            "movies",
            filter=Q(movies__id__in=base_language_ids),
            distinct=True,
        )
    )

    explain_plan = None
    if settings.DEBUG and request.GET.get("explain"):
        # Lightweight EXPLAIN to validate index usage during development only.
        explain_plan = filtered_query.explain()
        logger.info("movie_list EXPLAIN\n%s", explain_plan)

    page_obj = None
    movies_page = None
    next_cursor = None
    has_next = False

    if use_keyset:
        # Keyset pagination to avoid deep OFFSET scans when catalogs grow.
        order_field = order_by.lstrip("-")
        descending = order_by.startswith("-")
        ordering = [order_by, "-id" if descending else "id"]
        keyset_qs = filtered_query.order_by(*ordering)
        if cursor:
            try:
                cursor_val, cursor_id = cursor.split("|", 1)
                cursor_id = int(cursor_id)
                comp = "lt" if descending else "gt"
                filters = Q(**{f"{order_field}__{comp}": cursor_val})
                filters |= Q(**{order_field: cursor_val, f"id__{comp}": cursor_id})
                keyset_qs = keyset_qs.filter(filters)
            except ValueError:
                pass
        movies_slice = list(keyset_qs[: per_page + 1])
        has_next = len(movies_slice) > per_page
        movies_page = movies_slice[:per_page]
        if has_next:
            last = movies_page[-1]
            last_val = getattr(last, order_field)
            next_cursor = f"{last_val}|{last.id}"
    else:
        paginator = Paginator(filtered_query, per_page)
        page_number = request.GET.get("page", 1)
        try:
            page_obj = paginator.page(page_number)
        except PageNotAnInteger:
            page_obj = paginator.page(1)
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages)

    preserved_query = request.GET.copy()
    preserved_query.pop("page", None)
    preserved_query.pop("cursor", None)
    base_querystring = preserved_query.urlencode()

    context = {
        "movies": page_obj or movies_page,
        "page_obj": page_obj,
        "genres": genres_with_counts,
        "languages": languages_with_counts,
        "selected_genres": set(map(int, genre_ids)) if genre_ids else set(),
        "selected_languages": set(map(int, language_ids)) if language_ids else set(),
        "search_query": search_query,
        "sort_param": sort_param,
        "base_querystring": base_querystring,
        "use_keyset": use_keyset,
        "has_next": has_next,
        "next_cursor": next_cursor,
        "explain_plan": explain_plan,
        "per_page": per_page,
        "page_sizes": [6, 9, 12, 18, 24, 36, 48],
    }

    duration_ms = (perf_counter() - perf_start) * 1000
    query_count = len(connection.queries) if settings.DEBUG else "n/a"
    # Log only when notably heavy to keep noise low.
    if duration_ms > 150 or settings.DEBUG:
        perf_logger.info(
            "movie_list perf: %.1f ms, queries=%s, search=%s, genres=%s, languages=%s, sort=%s, keyset=%s, per_page=%s",
            duration_ms,
            query_count,
            bool(search_query),
            len(genre_ids),
            len(language_ids),
            sort_param,
            use_keyset,
            per_page,
        )
    return render(request, "movies/movie_list.html", context)


def _trailer_context(movie):
    embed_url = movie.trailer_embed_url
    poster_url = movie.trailer_poster_url
    watch_url = getattr(movie, "trailer_watch_url", None)

    if embed_url:
        allowed_prefix = embed_url.split("?", 1)[0]
        allowed_prefix = allowed_prefix.rsplit("/", 1)[0] + "/"
        return {
            "trailer_embed_url": embed_url,
            "trailer_poster_url": poster_url,
            "trailer_watch_url": watch_url,
            "trailer_embed_allowed_prefix": allowed_prefix,
        }

    api_key = getattr(settings, "YOUTUBE_API_KEY", "")
    if not api_key:
        return {"trailer_embed_url": None, "trailer_poster_url": None}

    cache_key = f"movies:trailer_video_id:{movie.id}"
    cached = cache.get(cache_key)
    if cached is None:
        from .youtube_api import search_trailer_video_id

        video_id = search_trailer_video_id(movie.name, api_key=api_key)
        cache.set(cache_key, video_id or "", 60 * 60 * 24 * 7)  # 7 days
        cached = video_id or ""

    if not cached:
        return {"trailer_embed_url": None, "trailer_poster_url": None}

    embed_base = (getattr(settings, "YOUTUBE_EMBED_BASE", "") or "").strip()
    if not embed_base:
        embed_base = "https://www.youtube-nocookie.com/embed/"
    if not embed_base.endswith("/"):
        embed_base += "/"

    embed_url = f"{embed_base}{cached}?rel=0&modestbranding=1"
    return {
        "trailer_embed_url": embed_url,
        "trailer_poster_url": f"https://i.ytimg.com/vi/{cached}/hqdefault.jpg",
        "trailer_watch_url": f"https://www.youtube.com/watch?v={cached}",
        "trailer_embed_allowed_prefix": embed_url.split("?", 1)[0].rsplit("/", 1)[0] + "/",
    }


def movie_detail(request, movie_id):
    movie = get_object_or_404(Movie.objects.select_related('language').prefetch_related('genres'), id=movie_id)
    context = {
        'movie': movie,
        **_trailer_context(movie),
    }
    return render(request, 'movies/movie_detail.html', context)


def theater_list(request,movie_id):
    movie = get_object_or_404(Movie,id=movie_id)
    theater=Theater.objects.filter(movie=movie)
    context = {
        'movie': movie,
        'theaters': theater,
        **_trailer_context(movie),
    }
    return render(request,'movies/theater_list.html', context)



@login_required(login_url='/login/')
def book_seats(request, theater_id):
    theater = get_object_or_404(Theater, id=theater_id)
    seats = Seat.objects.filter(theater=theater)
    reservations = SeatReservation.objects.filter(
        seat__theater=theater,
        expires_at__gt=timezone.now()
    ).select_related('seat', 'user')

    # Create a set of reserved seat IDs for quick lookup
    reserved_seat_ids = set(reservations.values_list('seat_id', flat=True))

    if request.method == 'POST':
        selected_seats = request.POST.getlist('seats')
        if not selected_seats:
            return render(request, "movies/seat_selection.html", {
                'theater': theater,
                "seats": seats,
                'reservations': reservations,
                'reserved_seat_ids': reserved_seat_ids,
                'error': "No seat selected"
            })

        # Convert to integers and validate
        try:
            seat_ids = [int(seat_id) for seat_id in selected_seats]
        except ValueError:
            return render(request, "movies/seat_selection.html", {
                'theater': theater,
                "seats": seats,
                'reservations': reservations,
                'reserved_seat_ids': reserved_seat_ids,
                'error': "Invalid seat selection"
            })

        # Reserve seats temporarily
        from .payment_service import reserve_seats
        success, result = reserve_seats(request.user, seat_ids, theater_id)

        if not success:
            return render(request, "movies/seat_selection.html", {
                'theater': theater,
                "seats": seats,
                'reservations': reservations,
                'reserved_seat_ids': reserved_seat_ids,
                'error': result
            })

        # Create booking with payment intent using reservations
        from .payment_service import create_booking_with_payment_from_reservations
        success, result = create_booking_with_payment_from_reservations(request.user, result, theater_id)

        if not success:
            return render(request, "movies/seat_selection.html", {
                'theater': theater,
                "seats": seats,
                'reservations': reservations,
                'reserved_seat_ids': reserved_seat_ids,
                'error': result
            })

        # Redirect to payment page with client_secret
        return redirect('payment_page', payment_id=result['payment'].id)

    return render(request, 'movies/seat_selection.html', {
        'theater': theater,
        "seats": seats,
        'reservations': reservations,
        'reserved_seat_ids': reserved_seat_ids
    })


@login_required(login_url='/login/')
def payment_page(request, payment_id):
    """Page to complete payment with Stripe Elements"""
    from .models import Payment
    payment = get_object_or_404(Payment.objects.select_related('booking__movie', 'booking__theater'), id=payment_id)

    # Ensure user owns this payment
    if payment.booking.user != request.user:
        return redirect('profile')

    hold_minutes = getattr(settings, "SEAT_HOLD_MINUTES", 2)
    hold_deadline = payment.created_at + timedelta(minutes=hold_minutes)
    seconds_remaining = int((hold_deadline - timezone.now()).total_seconds())

    # Fail fast in the UI if the hold already expired (scheduler may run slightly later).
    if payment.status == "pending" and seconds_remaining <= 0:
        return redirect('booking_failed', booking_id=payment.booking.id)

    # Check if payment is still pending
    if payment.status != 'pending':
        if payment.status == 'succeeded':
            return redirect('booking_success', booking_id=payment.booking.id)
        else:
            return redirect('booking_failed', booking_id=payment.booking.id)

    related_bookings = Booking.objects.filter(
        payment_id=payment.stripe_payment_intent_id
    ).select_related("seat", "movie", "theater").order_by("id")

    context = {
        'payment': payment,
        'booking': payment.booking,
        'related_bookings': related_bookings,
        'seat_hold_seconds_remaining': max(0, seconds_remaining),
        'stripe_public_key': settings.STRIPE_PUBLIC_KEY,
        'client_secret': payment.client_secret,
    }

    return render(request, 'movies/payment.html', context)


@login_required(login_url='/login/')
@require_POST
def payment_confirm(request, payment_id):
    """
    Confirm a Stripe payment server-side after client-side confirmation.

    This is a safety net for environments where webhooks aren't configured or
    can't be delivered (common in local dev). Webhooks remain the preferred path
    in production, but this keeps the booking state consistent for the user.
    """
    from .models import Payment

    payment = get_object_or_404(
        Payment.objects.select_related("booking__user"),
        id=payment_id,
    )

    if payment.booking.user_id != request.user.id:
        return JsonResponse({"error": "Forbidden"}, status=403)

    if not getattr(settings, "STRIPE_SECRET_KEY", ""):
        return JsonResponse({"error": "Stripe is not configured"}, status=400)

    # If we've already processed it (webhook or previous confirm), return quickly.
    if payment.status in {"succeeded", "failed", "cancelled"}:
        return JsonResponse(
            {
                "payment_status": payment.status,
                "booking_id": payment.booking_id,
            }
        )

    import stripe

    stripe.api_key = settings.STRIPE_SECRET_KEY

    try:
        intent = stripe.PaymentIntent.retrieve(payment.stripe_payment_intent_id)
    except stripe.error.StripeError as e:
        return JsonResponse({"error": str(e)}, status=400)

    from .payment_service import (
        _handle_payment_canceled,
        _handle_payment_failure,
        _handle_payment_success,
    )

    stripe_status = getattr(intent, "status", None)

    if stripe_status == "succeeded":
        _handle_payment_success(payment)
    elif stripe_status == "canceled":
        _handle_payment_canceled(payment)
    elif stripe_status == "requires_payment_method":
        _handle_payment_failure(payment)

    # Refresh to return the latest status after handlers
    payment.refresh_from_db()

    return JsonResponse(
        {
            "stripe_status": stripe_status,
            "payment_status": payment.status,
            "booking_id": payment.booking_id,
        }
    )


@login_required(login_url='/login/')
def booking_success(request, booking_id):
    """Success page after payment"""
    booking = get_object_or_404(Booking.objects.select_related('movie', 'theater', 'seat'), id=booking_id)

    # Ensure user owns this booking
    if booking.user != request.user:
        return redirect('profile')

    related_bookings = Booking.objects.filter(payment_id=booking.payment_id).select_related("seat").order_by("id")
    return render(request, 'movies/booking_success.html', {'booking': booking, 'related_bookings': related_bookings})


@login_required(login_url='/login/')
def booking_failed(request, booking_id):
    """Failure page after payment"""
    booking = get_object_or_404(Booking.objects.select_related('movie', 'theater', 'seat'), id=booking_id)

    # Ensure user owns this booking
    if booking.user != request.user:
        return redirect('profile')

    related_bookings = Booking.objects.filter(payment_id=booking.payment_id).select_related("seat").order_by("id")
    return render(request, 'movies/booking_failed.html', {'booking': booking, 'related_bookings': related_bookings})


def stripe_webhook(request):
    """Handle Stripe webhooks with idempotency"""
    from .payment_service import process_payment_webhook

    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')

    if not sig_header:
        return JsonResponse({'error': 'Missing signature'}, status=400)

    success, message = process_payment_webhook(payload, sig_header)

    if success:
        return JsonResponse({'status': 'ok'})
    else:
        return JsonResponse({'error': message}, status=400)


def seat_availability_api(request, theater_id):
    """
    API endpoint to get real-time seat availability.
    
    EDGE CASE HANDLING:
    1. Multiple devices selecting same seat:
       - Returns current reservation status
       - User sees locked seats if another device reserved them
    
    2. Rapid polling from client:
       - Safe to call every second
       - Database queries optimized with indexes
    
    3. Network reconnection:
       - Client can verify if selected seats still available
       - Server-side truth of record
    
    Returns JSON with seat status and reservation timeouts
    """
    theater = get_object_or_404(Theater, id=theater_id)
    
    # Get all seats for theater
    seats = Seat.objects.filter(theater=theater).select_related('reservation')
    
    # Get active reservations
    active_reservations = SeatReservation.objects.filter(
        seat__theater=theater,
        expires_at__gt=timezone.now()
    ).values_list('seat_id', 'expires_at', 'user_id')
    
    reservation_map = {
        seat_id: {
            'expires_at': expires_at.isoformat(),
            'expires_in_seconds': int((expires_at - timezone.now()).total_seconds()),
            'reserved_by_me': user_id == request.user.id if request.user.is_authenticated else False
        }
        for seat_id, expires_at, user_id in active_reservations
    }
    
    seat_data = []
    for seat in seats:
        seat_info = {
            'id': seat.id,
            'seat_number': seat.seat_number,
            'is_booked': seat.is_booked,
        }
        
        if seat.id in reservation_map:
            seat_info['is_reserved'] = True
            seat_info['reservation'] = reservation_map[seat.id]
        else:
            seat_info['is_reserved'] = False
        
        seat_data.append(seat_info)
    
    return JsonResponse({
        'theater_id': theater_id,
        'timestamp': timezone.now().isoformat(),
        'seats': seat_data,
        'total_seats': len(seat_data),
        'available_seats': sum(1 for s in seat_data if not s['is_booked'] and not s.get('is_reserved', False)),
        'booked_seats': sum(1 for s in seat_data if s['is_booked']),
        'reserved_seats': sum(1 for s in seat_data if s.get('is_reserved', False)),
    })
