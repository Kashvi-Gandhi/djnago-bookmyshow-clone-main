from datetime import timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Booking, Experience, ExperienceType, Seat, SeatReservation, Session
from .youtube_api import search_trailer_video_id


def experience_home(request):
    return render(request, "experiences/home.html")


def experience_list(request, type_slug):
    try:
        exp_type = ExperienceType(type_slug)
    except ValueError:
        return redirect("experience_home")

    experiences = Experience.objects.filter(type=exp_type).order_by("-rating", "name")[:200]
    return render(
        request,
        "experiences/list.html",
        {"experiences": experiences, "type_slug": type_slug, "type_label": exp_type.label},
    )


def _trailer_context(experience):
    embed_url = experience.trailer_embed_url
    poster_url = experience.trailer_poster_url
    if embed_url:
        return {
            "trailer_embed_url": embed_url,
            "trailer_poster_url": poster_url,
            "trailer_watch_url": getattr(experience, "trailer_watch_url", None),
        }

    api_key = getattr(settings, "YOUTUBE_API_KEY", "")
    if not api_key:
        return {"trailer_embed_url": None, "trailer_poster_url": None}

    # Cache in-memory per-process if cache backend is not configured
    video_id = search_trailer_video_id(experience.name, api_key=api_key)
    if not video_id:
        return {"trailer_embed_url": None, "trailer_poster_url": None}

    embed_base = (getattr(settings, "YOUTUBE_EMBED_BASE", "") or "").strip()
    if not embed_base:
        embed_base = "https://www.youtube-nocookie.com/embed/"
    if not embed_base.endswith("/"):
        embed_base += "/"
    return {
        "trailer_embed_url": f"{embed_base}{video_id}?rel=0&modestbranding=1",
        "trailer_poster_url": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        "trailer_watch_url": f"https://www.youtube.com/watch?v={video_id}",
    }


def experience_detail(request, experience_id):
    experience = get_object_or_404(Experience, id=experience_id)
    sessions = (
        Session.objects.filter(experience=experience, start_time__gte=timezone.now() - timedelta(hours=2))
        .select_related("venue")
        .order_by("start_time")[:50]
    )
    ctx = {"experience": experience, "sessions": sessions, **_trailer_context(experience)}
    return render(request, "experiences/detail.html", ctx)


@login_required(login_url="/login/")
def book_seats(request, session_id):
    session = get_object_or_404(Session.objects.select_related("experience", "venue"), id=session_id)
    seats = Seat.objects.filter(session=session).order_by("seat_number")
    reservations = SeatReservation.objects.filter(
        seat__session=session, expires_at__gt=timezone.now()
    ).select_related("seat", "user")
    reserved_seat_ids = set(reservations.values_list("seat_id", flat=True))

    if request.method == "POST":
        selected_seats = request.POST.getlist("seats")
        if not selected_seats:
            return render(
                request,
                "experiences/seat_selection.html",
                {
                    "session": session,
                    "seats": seats,
                    "reservations": reservations,
                    "reserved_seat_ids": reserved_seat_ids,
                    "error": "No seat selected",
                },
            )
        try:
            seat_ids = [int(s) for s in selected_seats]
        except ValueError:
            seat_ids = []
        if not seat_ids:
            return render(
                request,
                "experiences/seat_selection.html",
                {
                    "session": session,
                    "seats": seats,
                    "reservations": reservations,
                    "reserved_seat_ids": reserved_seat_ids,
                    "error": "Invalid seat selection",
                },
            )

        from .payment_service import create_booking_with_payment_from_reservations, reserve_seats

        ok, result = reserve_seats(request.user, seat_ids, session_id)
        if not ok:
            return render(
                request,
                "experiences/seat_selection.html",
                {
                    "session": session,
                    "seats": seats,
                    "reservations": reservations,
                    "reserved_seat_ids": reserved_seat_ids,
                    "error": result,
                },
            )

        ok, result = create_booking_with_payment_from_reservations(request.user, result, session_id)
        if not ok:
            return render(
                request,
                "experiences/seat_selection.html",
                {
                    "session": session,
                    "seats": seats,
                    "reservations": reservations,
                    "reserved_seat_ids": reserved_seat_ids,
                    "error": result,
                },
            )

        return redirect("experience_payment_page", payment_id=result["payment"].id)

    return render(
        request,
        "experiences/seat_selection.html",
        {"session": session, "seats": seats, "reservations": reservations, "reserved_seat_ids": reserved_seat_ids},
    )


@login_required(login_url="/login/")
def payment_page(request, payment_id):
    from .models import Payment

    payment = get_object_or_404(
        Payment.objects.select_related("booking__experience", "booking__session__venue", "booking__session"),
        id=payment_id,
    )
    if payment.booking.user_id != request.user.id:
        return redirect("profile")

    hold_minutes = getattr(settings, "SEAT_HOLD_MINUTES", 2)
    seconds_remaining = int((payment.created_at + timedelta(minutes=hold_minutes) - timezone.now()).total_seconds())
    if payment.status == "pending" and seconds_remaining <= 0:
        return redirect("experience_booking_failed", booking_id=payment.booking.id)

    if payment.status != "pending":
        if payment.status == "succeeded":
            return redirect("experience_booking_success", booking_id=payment.booking.id)
        return redirect("experience_booking_failed", booking_id=payment.booking.id)

    related_bookings = (
        Booking.objects.filter(payment_id=payment.stripe_payment_intent_id)
        .select_related("seat", "experience", "session__venue", "session")
        .order_by("booked_at")
    )

    return render(
        request,
        "experiences/payment.html",
        {
            "payment": payment,
            "booking": payment.booking,
            "related_bookings": related_bookings,
            "seat_hold_seconds_remaining": max(0, seconds_remaining),
            "stripe_public_key": settings.STRIPE_PUBLIC_KEY,
            "client_secret": payment.client_secret,
        },
    )


@login_required(login_url="/login/")
@require_POST
def payment_confirm(request, payment_id):
    from .models import Payment

    payment = get_object_or_404(Payment.objects.select_related("booking__user"), id=payment_id)
    if payment.booking.user_id != request.user.id:
        return JsonResponse({"error": "Forbidden"}, status=403)
    if not getattr(settings, "STRIPE_SECRET_KEY", ""):
        return JsonResponse({"error": "Stripe not configured"}, status=400)

    if payment.status in {"succeeded", "failed", "cancelled"}:
        return JsonResponse({"payment_status": payment.status, "booking_id": str(payment.booking_id)})

    import stripe

    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        intent = stripe.PaymentIntent.retrieve(payment.stripe_payment_intent_id)
    except stripe.error.StripeError as e:
        return JsonResponse({"error": str(e)}, status=400)

    from .payment_service import _handle_payment_failure, _handle_payment_success

    if intent.status == "succeeded":
        _handle_payment_success(payment)
    elif intent.status in {"canceled", "requires_payment_method"}:
        _handle_payment_failure(payment, cancelled=intent.status == "canceled")

    payment.refresh_from_db()
    return JsonResponse({"stripe_status": intent.status, "payment_status": payment.status, "booking_id": str(payment.booking_id)})


@login_required(login_url="/login/")
def booking_success(request, booking_id):
    booking = get_object_or_404(
        Booking.objects.select_related("experience", "session__venue", "seat"), id=booking_id
    )
    if booking.user_id != request.user.id:
        return redirect("profile")
    related_bookings = Booking.objects.filter(payment_id=booking.payment_id).select_related("seat").order_by("booked_at")
    return render(request, "experiences/booking_success.html", {"booking": booking, "related_bookings": related_bookings})


@login_required(login_url="/login/")
def booking_failed(request, booking_id):
    booking = get_object_or_404(
        Booking.objects.select_related("experience", "session__venue", "seat"), id=booking_id
    )
    if booking.user_id != request.user.id:
        return redirect("profile")
    related_bookings = Booking.objects.filter(payment_id=booking.payment_id).select_related("seat").order_by("booked_at")
    return render(request, "experiences/booking_failed.html", {"booking": booking, "related_bookings": related_bookings})


def stripe_webhook(request):
    from .payment_service import process_payment_webhook

    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE")
    if not sig_header:
        return JsonResponse({"error": "Missing signature"}, status=400)
    ok, msg = process_payment_webhook(payload, sig_header)
    return JsonResponse({"status": "ok"} if ok else {"error": msg}, status=200 if ok else 400)


def seat_availability_api(request, session_id):
    session = get_object_or_404(Session, id=session_id)
    seats = Seat.objects.filter(session=session).select_related("reservation")
    active = SeatReservation.objects.filter(
        seat__session=session, expires_at__gt=timezone.now()
    ).values_list("seat_id", "expires_at", "user_id")

    reservation_map = {
        seat_id: {
            "expires_at": expires_at.isoformat(),
            "expires_in_seconds": int((expires_at - timezone.now()).total_seconds()),
            "reserved_by_me": user_id == request.user.id if request.user.is_authenticated else False,
        }
        for seat_id, expires_at, user_id in active
    }

    seat_data = []
    for seat in seats:
        info = {"id": seat.id, "seat_number": seat.seat_number, "is_booked": seat.is_booked}
        if seat.id in reservation_map:
            info["is_reserved"] = True
            info["reservation"] = reservation_map[seat.id]
        else:
            info["is_reserved"] = False
        seat_data.append(info)

    return JsonResponse(
        {
            "session_id": session_id,
            "timestamp": timezone.now().isoformat(),
            "seats": seat_data,
        }
    )

# Create your views here.
