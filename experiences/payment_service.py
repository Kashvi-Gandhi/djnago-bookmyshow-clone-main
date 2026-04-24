import logging
import time
from datetime import timedelta

import stripe
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.utils import OperationalError
from django.utils import timezone

from .models import Booking, Payment, PaymentAttempt, Seat, SeatReservation

logger = logging.getLogger(__name__)


class PaymentService:
    def __init__(self):
        stripe.api_key = (settings.STRIPE_SECRET_KEY or "").strip()

    def create_payment_intent(self, booking, amount, currency="INR"):
        try:
            intent = stripe.PaymentIntent.create(
                amount=int(float(amount) * 100),
                currency=(currency or "INR").lower(),
                metadata={
                    "booking_id": str(booking.id),
                    "user_id": str(booking.user.id),
                    "experience": booking.experience.name,
                    "venue": booking.session.venue.name,
                    "seats": booking.seat.seat_number,
                    "session_id": str(booking.session_id),
                },
                description=f"Ticket: {booking.experience.name} - {booking.seat.seat_number}",
                receipt_email=booking.user.email if booking.user.email else None,
            )
            return intent
        except stripe.error.StripeError as e:
            raise Exception(f"Payment creation failed: {str(e)}")

    def cancel_payment(self, payment_intent_id):
        try:
            intent = stripe.PaymentIntent.cancel(payment_intent_id)
            return True, intent
        except stripe.error.StripeError as e:
            return False, str(e)


def reserve_seats(user, seat_ids, session_id, reservation_minutes=2):
    from .models import Session

    max_attempts = 6 if getattr(settings, "DATABASES", {}).get("default", {}).get("ENGINE", "").endswith("sqlite3") else 1
    for attempt in range(max_attempts):
        try:
            with transaction.atomic():
                session = Session.objects.select_for_update().get(id=session_id)

                seats = Seat.objects.select_for_update().filter(
                    id__in=seat_ids,
                    session=session,
                    is_booked=False,
                ).exclude(reservation__expires_at__gt=timezone.now())

                if len(seats) != len(seat_ids):
                    return False, "Some seats are no longer available."

                expires_at = timezone.now() + timedelta(minutes=reservation_minutes)
                reservations = []
                for seat in seats:
                    try:
                        reservations.append(
                            SeatReservation.objects.create(seat=seat, user=user, expires_at=expires_at)
                        )
                    except IntegrityError:
                        return False, "Some seats are no longer available."
                return True, reservations
        except OperationalError as e:
            if "locked" in str(e).lower() and attempt < max_attempts - 1:
                time.sleep(0.05 * (attempt + 1))
                continue
            return False, "Database busy. Please try again."
        except Exception as e:
            logger.exception("Seat reservation error: %s", str(e))
            return False, "Error reserving seats."

    return False, "Database busy. Please try again."


def create_booking_with_payment_from_reservations(user, reservations, session_id):
    from .models import Session

    if not reservations:
        return False, "No reservations provided"

    try:
        with transaction.atomic():
            session = Session.objects.select_for_update().select_related("experience", "venue").get(id=session_id)

            reservation_ids = [r.id for r in reservations]
            valid = SeatReservation.objects.select_for_update().filter(
                id__in=reservation_ids,
                user=user,
                expires_at__gt=timezone.now(),
                seat__session=session,
                seat__is_booked=False,
            ).select_related("seat")

            if len(valid) != len(reservations):
                return False, "Some reservations expired. Please select seats again."

            bookings = []
            total_amount = 0
            for reservation in valid:
                booking = Booking.objects.create(
                    user=user,
                    seat=reservation.seat,
                    session=session,
                    experience=session.experience,
                    status="pending_payment",
                )
                bookings.append(booking)
                total_amount += float(session.ticket_price)
                reservation.seat.is_booked = True
                reservation.seat.save(update_fields=["is_booked"])

            valid.delete()

            payment_intent = PaymentService().create_payment_intent(bookings[0], total_amount, currency=settings.STRIPE_CURRENCY)

            payment = Payment.objects.create(
                booking=bookings[0],
                stripe_payment_intent_id=payment_intent.id,
                client_secret=payment_intent.client_secret,
                amount=total_amount,
                currency=settings.STRIPE_CURRENCY,
                status="pending",
            )

            Booking.objects.filter(id__in=[b.id for b in bookings]).update(payment_id=payment.stripe_payment_intent_id)

            return True, {"bookings": bookings, "payment": payment, "client_secret": payment_intent.client_secret}
    except Exception as e:
        logger.exception("Booking creation error: %s", str(e))
        return False, f"Error creating booking: {str(e)}"


def process_payment_webhook(event_data, signature):
    stripe.api_key = (settings.STRIPE_SECRET_KEY or "").strip()
    webhook_secret = (settings.STRIPE_WEBHOOK_SECRET or "").strip()

    try:
        event = stripe.Webhook.construct_event(event_data, signature, webhook_secret)
    except ValueError:
        return False, "Invalid payload"
    except stripe.error.SignatureVerificationError:
        return False, "Invalid signature"

    event_id = event.id
    event_type = event.type
    payment_intent_id = event.data.object.id

    try:
        payment = Payment.objects.get(stripe_payment_intent_id=payment_intent_id)
    except Payment.DoesNotExist:
        return False, "Payment not found"

    attempt, created = PaymentAttempt.objects.get_or_create(
        payment=payment,
        stripe_event_id=event_id,
        defaults={"event_type": event_type, "payload": event_data, "signature_verified": True},
    )
    if not created:
        return True, "Event already processed"

    if event_type == "payment_intent.succeeded":
        return _handle_payment_success(payment)
    if event_type in {"payment_intent.payment_failed", "payment_intent.canceled"}:
        return _handle_payment_failure(payment, cancelled=event_type.endswith("canceled"))

    return True, "Unhandled"


def _send_confirmation_email_now(booking_ids):
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string

    bookings = list(
        Booking.objects.select_related("user", "experience", "session__venue", "seat").filter(id__in=booking_ids)
    )
    if not bookings:
        return

    # One email per payment
    grouped = {}
    for booking in bookings:
        if not booking.user.email:
            continue
        grouped.setdefault((booking.user_id, booking.payment_id), []).append(booking)

    for (_user_id, payment_id), group in grouped.items():
        first = group[0]
        context = {
            "user": {"username": first.user.username, "email": first.user.email},
            "experience": {"name": first.experience.name, "type": first.experience.type},
            "venue": {"name": first.session.venue.name, "city": first.session.venue.city},
            "seats": [b.seat.seat_number for b in group],
            "payment_ids": [payment_id],
            "start_time": first.session.start_time.isoformat(),
            "booked_at": min(b.booked_at for b in group).isoformat(),
        }

        subject = f"Your tickets for {first.experience.name}"
        try:
            txt_body = render_to_string("emails/experience_confirmation.txt", context)
            html_body = render_to_string("emails/experience_confirmation.html", context)
            msg = EmailMultiAlternatives(subject, txt_body, settings.DEFAULT_FROM_EMAIL, [first.user.email])
            msg.attach_alternative(html_body, "text/html")
            msg.send(fail_silently=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Immediate email failed: %s", str(exc))
            try:
                from .models import EmailQueue

                EmailQueue.objects.create(
                    to_email=first.user.email,
                    subject=subject,
                    template="emails/experience_confirmation",
                    context=context,
                )
            except Exception:
                logger.exception("Failed to enqueue experience email after send failure")


def _handle_payment_success(payment):
    try:
        with transaction.atomic():
            payment = Payment.objects.select_for_update().get(id=payment.id)
            if payment.status == "succeeded":
                return True, "Already succeeded"
            if payment.status in {"failed", "cancelled"}:
                return True, f"Already {payment.status}"

            payment.status = "succeeded"
            payment.save(update_fields=["status"])

            booking_ids = list(
                Booking.objects.filter(payment_id=payment.stripe_payment_intent_id).values_list("id", flat=True)
            )
            Booking.objects.filter(id__in=booking_ids).update(status="confirmed")
            transaction.on_commit(lambda: _send_confirmation_email_now(booking_ids))
            return True, "Confirmed"
    except Exception as e:
        logger.exception("Payment success handling failed: %s", str(e))
        return False, str(e)


def _handle_payment_failure(payment, cancelled: bool = False):
    try:
        with transaction.atomic():
            payment = Payment.objects.select_for_update().get(id=payment.id)
            if payment.status in {"failed", "cancelled"}:
                return True, "Already handled"

            payment.status = "cancelled" if cancelled else "failed"
            payment.save(update_fields=["status"])

            booking_ids = list(
                Booking.objects.filter(payment_id=payment.stripe_payment_intent_id).values_list("id", flat=True)
            )
            bookings = Booking.objects.filter(id__in=booking_ids)
            seat_ids = list(bookings.values_list("seat_id", flat=True))
            Seat.objects.filter(id__in=seat_ids).update(is_booked=False)
            Booking.objects.filter(id__in=booking_ids).update(status="cancelled")
            return True, "Cancelled"
    except Exception as e:
        logger.exception("Payment failure handling failed: %s", str(e))
        return False, str(e)


def cleanup_expired_reservations():
    now = timezone.now()
    payment_timeout = now - timedelta(minutes=getattr(settings, "SEAT_HOLD_MINUTES", 2))

    expired_count = 0
    cancelled_payments = 0

    try:
        with transaction.atomic():
            expired = SeatReservation.objects.filter(expires_at__lt=now)
            expired_count = expired.count()
            expired.delete()
    except Exception:
        logger.exception("SeatReservation cleanup failed")

    pending = Payment.objects.filter(status="pending", created_at__lt=payment_timeout)
    for payment in pending:
        try:
            with transaction.atomic():
                payment = Payment.objects.select_for_update().get(id=payment.id)
                if payment.status != "pending":
                    continue
                PaymentService().cancel_payment(payment.stripe_payment_intent_id)
                _handle_payment_failure(payment, cancelled=True)
                cancelled_payments += 1
        except Exception:
            logger.exception("Failed to cancel pending payment %s", payment.id)

    return expired_count, cancelled_payments

