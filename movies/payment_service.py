import stripe
import logging
import time
from django.conf import settings
from django.db import transaction, IntegrityError
from django.db import connection
from django.db.utils import OperationalError
from django.utils import timezone
from datetime import timedelta

from .models import Booking, Payment, Seat, Theater, SeatReservation

logger = logging.getLogger(__name__)


class PaymentService:
    def __init__(self):
        stripe.api_key = (settings.STRIPE_SECRET_KEY or "").strip()

    def create_payment_intent(self, booking, amount, currency="INR"):
        """Create Stripe PaymentIntent for booking"""
        try:
            intent = stripe.PaymentIntent.create(
                amount=int(amount * 100),  # Convert to paisa
                currency=currency.lower(),
                metadata={
                    "booking_id": str(booking.id),
                    "user_id": str(booking.user.id),
                    "movie": booking.movie.name,
                    "theater": booking.theater.name,
                    "seats": booking.seat.seat_number,
                },
                description=f"Movie ticket: {booking.movie.name} - {booking.seat.seat_number}",
                receipt_email=booking.user.email if booking.user.email else None,
            )
            return intent
        except stripe.error.StripeError as e:
            raise Exception(f"Payment creation failed: {str(e)}")

    def confirm_payment(self, payment_intent_id):
        """Confirm payment intent (for server-side confirmation)"""
        try:
            intent = stripe.PaymentIntent.retrieve(payment_intent_id)
            if intent.status == "succeeded":
                return True, intent
            return False, intent
        except stripe.error.StripeError as e:
            return False, str(e)

    def cancel_payment(self, payment_intent_id):
        """Cancel payment intent"""
        try:
            intent = stripe.PaymentIntent.cancel(payment_intent_id)
            return True, intent
        except stripe.error.StripeError as e:
            return False, str(e)

    def refund_payment(self, payment_intent_id, amount=None):
        """Refund payment"""
        try:
            refund = stripe.Refund.create(
                payment_intent=payment_intent_id,
                amount=int(amount * 100) if amount else None,
            )
            return True, refund
        except stripe.error.StripeError as e:
            return False, str(e)


def reserve_seats(user, seat_ids, theater_id, reservation_minutes=2):
    """
    Reserve seats temporarily with atomic transaction and row-level locking.
    
    CONCURRENCY MODEL & RACE CONDITION PREVENTION:
    =============================================
    1. ATOMIC TRANSACTION: Entire operation wrapped in atomic() block
       - All-or-nothing guarantee - partial reservations never occur
       - If any seat fails, all are rolled back
    
    2. ROW-LEVEL LOCKING (select_for_update):
       - Theater: Locked to prevent concurrent movie assignments
       - Seat: Locked to prevent double-booking
       - Duration: For entire transaction duration
       - Type: Pessimistic locking at database level
    
    3. RESERVATION WINDOW:
       - 2-minute temporary lock prevents ghost bookings
       - Stored in database, queryable by payment process
       - Auto-cleanup via scheduled task
    
    4. RACE CONDITION SCENARIOS HANDLED:
       a) Two users selecting same seat milliseconds apart:
          - First user's select_for_update acquires lock
          - Database queue enforces order of updates
          - Second user gets "seat no longer available" error
       
       b) User closes app during reservation:
          - Reservation remains locked for 2 minutes
          - Cleanup task releases after timeout
       
       c) Network interruption:
          - Transaction rollback releases locks immediately
          - Database tracks failed attempts
    
    5. DATABASE-LEVEL CONSISTENCY:
       - Using PostgreSQL: Serializable isolation would be ideal but expensive
       - Using SQLite: Atomic transaction with locks
       - Index on expires_at for fast cleanup queries
    
    Args:
        user: User object making the reservation
        seat_ids: List of seat IDs to reserve
        theater_id: Theater ID for seat validation
        reservation_minutes: Duration of temporary lock (default 2 min)
    
    Returns: (success, reservations or error message)
    """
    # SQLite uses coarse locking; under real concurrency it can raise "database is locked" instead of waiting.
    # We retry a few times to make concurrent different-seat reservations succeed reliably in dev/test.
    max_attempts = 6 if connection.vendor == "sqlite" else 1
    for attempt in range(max_attempts):
        try:
            with transaction.atomic():
                # Acquire row-level locks on Theater and Seats (no-op on SQLite, real row locks on Postgres).
                theater = Theater.objects.select_for_update().get(id=theater_id)

                # Get seats with locks, excluding already booked and existing valid reservations
                seats = Seat.objects.select_for_update().filter(
                    id__in=seat_ids,
                    theater=theater,
                    is_booked=False,
                ).exclude(
                    reservation__expires_at__gt=timezone.now()
                )

                # Verify all requested seats are available
                if len(seats) != len(seat_ids):
                    unavailable_count = len(seat_ids) - len(seats)
                    logger.warning(
                        f"Seat reservation failed for user {user.id}: {unavailable_count} seats unavailable",
                        extra={"theater_id": theater_id, "requested_seats": len(seat_ids)},
                    )
                    return False, "Some seats are no longer available. They may have been booked by another user."

                expires_at = timezone.now() + timedelta(minutes=reservation_minutes)
                reservations = []

                for seat in seats:
                    try:
                        reservation = SeatReservation.objects.create(
                            seat=seat,
                            user=user,
                            expires_at=expires_at,
                        )
                        reservations.append(reservation)
                        logger.info(
                            f"Seat {seat.seat_number} reserved for user {user.id}",
                            extra={"seat_id": seat.id, "expires_at": expires_at},
                        )
                    except IntegrityError as e:
                        # Unique seat constraint prevents double-holds even if select_for_update is ineffective.
                        logger.warning(f"IntegrityError during reservation: {str(e)}")
                        return False, "Some seats are no longer available. They may have been booked by another user."

                logger.info(f"Successfully reserved {len(reservations)} seats for user {user.id}")
                return True, reservations

        except Theater.DoesNotExist:
            logger.error(f"Theater {theater_id} not found")
            return False, "Theater not found"
        except OperationalError as e:
            if connection.vendor == "sqlite" and "locked" in str(e).lower() and attempt < max_attempts - 1:
                time.sleep(0.05 * (attempt + 1))
                continue
            logger.error(
                f"Seat reservation OperationalError: {str(e)}",
                extra={"user_id": user.id, "theater_id": theater_id},
                exc_info=True,
            )
            return False, "Database busy. Please try again."
        except Exception as e:
            logger.error(
                f"Seat reservation error: {str(e)}",
                extra={"user_id": user.id, "theater_id": theater_id},
                exc_info=True,
            )
            return False, f"Error reserving seats: {str(e)}"

    return False, "Database busy. Please try again."


def create_booking_with_payment_from_reservations(user, reservations, theater_id):
    """
    Create booking with payment intent from valid reservations.
    
    CONSISTENCY GUARANTEE:
    - Valid reservations are locked (select_for_update)
    - If payment fails, all seats revert immediately via transaction rollback
    - No orphaned bookings or seats in inconsistent state
    
    EDGE CASE HANDLING:
    1. Reservation expired between reserve_seats and this call:
       - Validation check returns error
       - User must re-reserve
    
    2. Payment intent creation fails:
       - Transaction rollback releases all locks
       - Seats revert to available
    
    3. Network timeout during Stripe API call:
       - Transaction hangs until timeout
       - Locks released on timeout
       - Cleanup task resolves orphaned bookings
    
    Args:
        user: User object with valid reservations
        reservations: List of SeatReservation objects from reserve_seats()
        theater_id: Theater ID for validation
    
    Returns: (success, booking/payment data or error message)
    """
    if not reservations:
        logger.warning(f"Empty reservations list for user {user.id}")
        return False, "No reservations provided"
    
    try:
        with transaction.atomic():
            # Acquire locks on theater
            theater = Theater.objects.select_for_update().get(id=theater_id)

            # Validate all reservations still exist and are valid
            reservation_ids = [r.id for r in reservations]
            valid_reservations = SeatReservation.objects.select_for_update().filter(
                id__in=reservation_ids,
                user=user,
                expires_at__gt=timezone.now(),
                seat__theater=theater,
                seat__is_booked=False
            ).select_related('seat')

            if len(valid_reservations) != len(reservations):
                lost_count = len(reservations) - len(valid_reservations)
                logger.warning(
                    f"Reservation validation failed: {lost_count} reservations expired or invalidated",
                    extra={"user_id": user.id, "theater_id": theater_id}
                )
                return False, "Some reservations have expired or become invalid. Please try selecting seats again."

            # Create bookings in pending state
            bookings = []
            total_amount = 0

            for reservation in valid_reservations:
                booking = Booking.objects.create(
                    user=user,
                    seat=reservation.seat,
                    movie=theater.movie,
                    theater=theater,
                    status="pending_payment"
                )
                bookings.append(booking)
                total_amount += settings.TICKET_PRICE
                
                # Mark seat as booked (temporary until payment confirmed)
                reservation.seat.is_booked = True
                reservation.seat.save()
                
                logger.info(f"Booking created: {booking.id} for seat {reservation.seat.seat_number}")

            # Clean up reservations since they're now in booking stage
            valid_reservations.delete()

            # Create payment intent
            try:
                payment_service = PaymentService()
                payment_intent = payment_service.create_payment_intent(
                    bookings[0], total_amount
                )
            except Exception as e:
                logger.error(f"Payment intent creation failed: {str(e)}")
                # Transaction will rollback, seats will be released
                raise

            # Create payment record
            payment = Payment.objects.create(
                booking=bookings[0],
                stripe_payment_intent_id=payment_intent.id,
                client_secret=payment_intent.client_secret,
                amount=total_amount,
                currency=settings.STRIPE_CURRENCY,
                status="pending"
            )

            # Critical: link ALL bookings to the same Stripe PaymentIntent id so webhook/cleanup can find them.
            Booking.objects.filter(id__in=[b.id for b in bookings]).update(
                payment_id=payment.stripe_payment_intent_id
            )

            logger.info(
                f"Booking with payment created: {len(bookings)} bookings, payment {payment.id}",
                extra={"user_id": user.id, "total_amount": total_amount}
            )

            return True, {
                "bookings": bookings,
                "payment": payment,
                "client_secret": payment_intent.client_secret,
                "amount": total_amount,
            }

    except Theater.DoesNotExist:
        logger.error(f"Theater {theater_id} not found during booking creation")
        return False, "Theater not found"
    except Exception as e:
        logger.error(
            f"Booking creation error: {str(e)}",
            extra={"user_id": user.id, "theater_id": theater_id},
            exc_info=True
        )
        return False, f"Error creating booking: {str(e)}"


def process_payment_webhook(event_data, signature):
    """
    Process Stripe webhook with idempotency
    """
    from .models import PaymentAttempt

    stripe.api_key = (settings.STRIPE_SECRET_KEY or "").strip()
    webhook_secret = (settings.STRIPE_WEBHOOK_SECRET or "").strip()

    try:
        # Verify webhook signature
        event = stripe.Webhook.construct_event(
            event_data, signature, webhook_secret
        )
    except ValueError:
        return False, "Invalid payload"
    except stripe.error.SignatureVerificationError:
        return False, "Invalid signature"

    event_id = event.id
    event_type = event.type

    # Find payment by intent ID
    payment_intent_id = event.data.object.id
    try:
        payment = Payment.objects.get(stripe_payment_intent_id=payment_intent_id)
    except Payment.DoesNotExist:
        return False, "Payment not found"

    # Check idempotency - prevent duplicate processing
    attempt, created = PaymentAttempt.objects.get_or_create(
        payment=payment,
        stripe_event_id=event_id,
        defaults={
            "event_type": event_type,
            "payload": event_data,
            "signature_verified": True,
        }
    )

    if not created:
        # Event already processed
        return True, "Event already processed"

    # Process based on event type
    if event_type == "payment_intent.succeeded":
        return _handle_payment_success(payment)
    elif event_type == "payment_intent.payment_failed":
        return _handle_payment_failure(payment)
    elif event_type == "payment_intent.canceled":
        return _handle_payment_canceled(payment)

    return True, f"Unhandled event type: {event_type}"


def _handle_payment_success(payment):
    """
    Handle successful payment - confirm booking and release payment hold.
    
    IDEMPOTENCY:
    - If called twice, second call finds existing confirmed bookings
    - Safe to retry without side effects
    """
    try:
        with transaction.atomic():
            payment = Payment.objects.select_for_update().get(id=payment.id)
            
            if payment.status == "succeeded":
                logger.info(f"Payment {payment.id} already marked as succeeded")
                return True, "Payment already confirmed"

            # If we already cancelled/failed this payment (e.g. timeout cleanup), don't resurrect it.
            if payment.status in ["cancelled", "failed"]:
                logger.info(f"Ignoring success for non-pending payment {payment.id} ({payment.status})")
                return True, f"Payment already {payment.status}"

            hold_deadline = payment.created_at + timedelta(minutes=getattr(settings, "SEAT_HOLD_MINUTES", 2))
            if timezone.now() > hold_deadline:
                # Payment completed after our seat-hold window; do not confirm bookings.
                # We attempt a refund to avoid charging the user for an expired hold.
                logger.warning(f"Payment {payment.id} succeeded after hold timeout; attempting refund")
                try:
                    PaymentService().refund_payment(payment.stripe_payment_intent_id)
                except Exception as e:
                    logger.warning(f"Refund attempt failed for {payment.id}: {str(e)}")

                payment.status = "cancelled"
                payment.save()

                booking_ids = Booking.objects.filter(
                    payment_id=payment.stripe_payment_intent_id
                ).values_list("id", flat=True)
                bookings = Booking.objects.filter(id__in=booking_ids)
                seat_ids = list(bookings.values_list("seat_id", flat=True))
                Seat.objects.filter(id__in=seat_ids).update(is_booked=False)
                Booking.objects.filter(id__in=booking_ids).update(status="cancelled")
                return True, "Payment succeeded too late; bookings cancelled and seats released"
            
            payment.status = "succeeded"
            payment.save()

            # Confirm all related bookings
            booking_ids = Booking.objects.filter(
                payment_id=payment.stripe_payment_intent_id
            ).values_list("id", flat=True)

            Booking.objects.filter(id__in=booking_ids).update(status="confirmed")

            # Send confirmation email right after a successful booking.
            # Use on_commit so we only send after the DB transaction is durable.
            booking_ids = list(booking_ids)
            transaction.on_commit(lambda: _send_confirmation_emails_now(booking_ids))

            logger.info(
                f"Payment confirmed: {payment.id}, {len(booking_ids)} bookings confirmed",
                extra={"payment_id": payment.id}
            )

            return True, "Payment confirmed"
    except Exception as e:
        logger.error(
            f"Payment success handling failed: {str(e)}",
            extra={"payment_id": payment.id},
            exc_info=True
        )
        return False, f"Confirmation failed: {str(e)}"


def _handle_payment_failure(payment):
    """
    Handle failed payment - release seats and cancel bookings.
    Ensures seats become available for other users.
    """
    try:
        with transaction.atomic():
            payment = Payment.objects.select_for_update().get(id=payment.id)
            
            if payment.status in ["failed", "cancelled"]:
                logger.info(f"Payment {payment.id} already handled as {payment.status}")
                return True, f"Payment already marked as {payment.status}"
            
            payment.status = "failed"
            payment.save()

            # Cancel bookings and release seats
            booking_ids = Booking.objects.filter(
                payment_id=payment.stripe_payment_intent_id
            ).values_list("id", flat=True)

            bookings = Booking.objects.filter(id__in=booking_ids)
            seat_ids = list(bookings.values_list("seat_id", flat=True))

            # Release seats - now available for other users
            Seat.objects.filter(id__in=seat_ids).update(is_booked=False)
            
            # Cancel bookings
            Booking.objects.filter(id__in=booking_ids).update(status="cancelled")

            logger.info(
                f"Payment failed handled: {payment.id}, released {len(seat_ids)} seats",
                extra={"payment_id": payment.id, "seat_count": len(seat_ids)}
            )

        return True, "Payment failed, bookings cancelled"
    except Exception as e:
        logger.error(
            f"Payment failure handling failed: {str(e)}",
            extra={"payment_id": payment.id},
            exc_info=True
        )
        return False, f"Failure handling failed: {str(e)}"


def _handle_payment_canceled(payment):
    """
    Handle canceled payment - same as failure.
    User canceled the payment, so release seats and cancel bookings.
    """
    try:
        with transaction.atomic():
            payment = Payment.objects.select_for_update().get(id=payment.id)
            
            if payment.status in ["cancelled"]:
                logger.info(f"Payment {payment.id} already marked as cancelled")
                return True, "Payment already cancelled"
            
            payment.status = "cancelled"
            payment.save()

            # Cancel bookings and release seats
            booking_ids = Booking.objects.filter(
                payment_id=payment.stripe_payment_intent_id
            ).values_list("id", flat=True)

            bookings = Booking.objects.filter(id__in=booking_ids)
            seat_ids = list(bookings.values_list("seat_id", flat=True))

            # Release seats
            Seat.objects.filter(id__in=seat_ids).update(is_booked=False)
            
            # Cancel bookings
            Booking.objects.filter(id__in=booking_ids).update(status="cancelled")

            logger.info(
                f"Payment cancelled handled: {payment.id}, released {len(seat_ids)} seats",
                extra={"payment_id": payment.id, "seat_count": len(seat_ids)}
            )

        return True, "Payment cancelled, bookings cancelled"
    except Exception as e:
        logger.error(
            f"Payment cancellation handling failed: {str(e)}",
            extra={"payment_id": payment.id},
            exc_info=True
        )
        return False, f"Cancellation handling failed: {str(e)}"


def _enqueue_confirmation_emails(booking_ids):
    """Enqueue confirmation emails for successful bookings"""
    from .models import EmailQueue
    from django.template.loader import render_to_string

    bookings = Booking.objects.select_related("user", "movie", "theater", "seat").filter(id__in=booking_ids)

    for booking in bookings:
        if booking.user.email:
            context = {
                "user": {
                    "username": booking.user.username,
                    "email": booking.user.email,
                },
                "movie": {
                    "name": booking.movie.name,
                },
                "theater": {
                    "name": booking.theater.name,
                },
                "seats": [booking.seat.seat_number],
                "payment_ids": [booking.payment_id],
                "show_time": booking.theater.time.isoformat(),
                "booked_at": booking.booked_at.isoformat(),
            }

            EmailQueue.objects.create(
                to_email=booking.user.email,
                subject=f"Your tickets for {booking.movie.name}",
                template="emails/booking_confirmation",
                context=context,
            )


def _send_confirmation_emails_now(booking_ids):
    """
    Send booking confirmation emails immediately (no worker required).

    If sending fails for any reason (SMTP/auth/etc), we enqueue the email so it
    can still be retried later with `manage.py process_email_queue`.
    """
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string

    bookings = list(
        Booking.objects.select_related("user", "movie", "theater", "seat").filter(id__in=booking_ids)
    )

    # Group into a single email per payment/user/movie/theater for a better UX.
    grouped = {}
    for booking in bookings:
        if not booking.user.email:
            continue
        key = (booking.user_id, booking.movie_id, booking.theater_id, booking.payment_id)
        grouped.setdefault(key, []).append(booking)

    for (_user_id, _movie_id, _theater_id, payment_id), group_bookings in grouped.items():
        first = group_bookings[0]
        seat_numbers = [b.seat.seat_number for b in group_bookings]
        context = {
            "user": {
                "username": first.user.username,
                "email": first.user.email,
            },
            "movie": {
                "name": first.movie.name,
            },
            "theater": {
                "name": first.theater.name,
            },
            "seats": seat_numbers,
            "payment_ids": [payment_id],
            "show_time": first.theater.time.isoformat(),
            "booked_at": min(b.booked_at for b in group_bookings).isoformat(),
        }

        subject = f"Your tickets for {first.movie.name}"
        try:
            txt_body = render_to_string("emails/booking_confirmation.txt", context)
            html_body = render_to_string("emails/booking_confirmation.html", context)
            msg = EmailMultiAlternatives(subject, txt_body, settings.DEFAULT_FROM_EMAIL, [first.user.email])
            msg.attach_alternative(html_body, "text/html")
            msg.send(fail_silently=False)
            logger.info("Sent booking confirmation email to %s (payment_id=%s)", first.user.email, payment_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Immediate booking email failed for %s (payment_id=%s): %s",
                first.user.email,
                payment_id,
                str(exc),
            )
            # Best-effort fallback for later retries.
            try:
                from .models import EmailQueue

                EmailQueue.objects.create(
                    to_email=first.user.email,
                    subject=subject,
                    template="emails/booking_confirmation",
                    context=context,
                )
            except Exception:
                logger.exception("Failed to enqueue booking email after send failure (payment_id=%s)", payment_id)

def cleanup_expired_reservations():
    """
    Clean up expired reservations and abandoned payments (run periodically via scheduler).
    
    AUTOMATIC CLEANUP RESPONSIBILITIES:
    1. Release expired SeatReservations (2-minute timeout)
    2. Cancel pending payments that have exceeded timeout (15 minutes)
    3. Release seats from timed-out bookings
    4. Ensure seats never remain locked indefinitely
    
    TRIGGERS FOR CLEANUP:
    - Scheduled to run every 5 minutes via APScheduler
    - Called manually when needed
    - Background task independent of user actions
    
    GUARANTEES:
    - Idempotent: Safe to call multiple times
    - Atomic per payment: Either cancels or doesn't
    - Non-blocking: Errors don't prevent other cleanups
    
    EDGE CASES HANDLED:
    - App closed during payment: Cleanup releases seats
    - Network timeout: Cleanup cancels orphaned payment intent
    - Multiple cleanup processes: Atomic updates prevent duplicates
    """
    now = timezone.now()
    # Keep payment timeout aligned to the actual seat hold window.
    payment_timeout = now - timedelta(minutes=getattr(settings, "SEAT_HOLD_MINUTES", 2))
    
    cleanup_stats = {
        "expired_reservations": 0,
        "released_seats": 0,
        "cancelled_payments": 0,
        "errors": []
    }

    try:
        # Clean up expired seat reservations (2-minute timeout)
        with transaction.atomic():
            expired_reservations = SeatReservation.objects.filter(expires_at__lt=now)
            cleanup_stats["expired_reservations"] = expired_reservations.count()
            expired_reservations.delete()
            logger.info(f"Cleaned up {cleanup_stats['expired_reservations']} expired seat reservations")
    except Exception as e:
        logger.error(f"Error cleaning up seat reservations: {str(e)}", exc_info=True)
        cleanup_stats["errors"].append(f"Seat reservation cleanup: {str(e)}")

    # Clean up abandoned payments (seat-hold timeout). Avoid select_for_update() outside a transaction.
    expired_payment_ids = list(
        Payment.objects.filter(status="pending", created_at__lt=payment_timeout).values_list("id", flat=True)
    )

    for payment_id in expired_payment_ids:
        try:
            with transaction.atomic():
                # Acquire lock on payment
                payment = Payment.objects.select_for_update().get(id=payment_id)
                
                # Skip if already processed
                if payment.status != "pending":
                    continue

                # Cancel payment intent on Stripe (idempotent, safe to retry)
                try:
                    payment_service = PaymentService()
                    payment_service.cancel_payment(payment.stripe_payment_intent_id)
                except Exception as e:
                    logger.warning(f"Failed to cancel Stripe intent {payment.stripe_payment_intent_id}: {str(e)}")

                # Find and cancel related bookings
                bookings = Booking.objects.filter(
                    payment_id=payment.stripe_payment_intent_id
                )
                seat_ids = list(bookings.values_list("seat_id", flat=True))

                # Release seats back for other users
                if seat_ids:
                    Seat.objects.filter(id__in=seat_ids).update(is_booked=False)
                    cleanup_stats["released_seats"] += len(seat_ids)

                # Mark all bookings as cancelled
                bookings.update(status="cancelled")
                
                # Mark payment as cancelled
                payment.status = "cancelled"
                payment.save()
                cleanup_stats["cancelled_payments"] += 1

                logger.info(
                    f"Cancelled abandoned payment {payment.id}: released {len(seat_ids)} seats",
                    extra={"payment_id": payment.id, "seat_count": len(seat_ids)}
                )

        except Exception as e:
            logger.error(
                f"Error processing expired payment {payment_id}: {str(e)}",
                extra={"payment_id": payment_id},
                exc_info=True
            )
            cleanup_stats["errors"].append(f"Payment {payment_id}: {str(e)}")

    # Log summary
    logger.info(
        "Cleanup complete",
        extra=cleanup_stats
    )

    return cleanup_stats["expired_reservations"], cleanup_stats["cancelled_payments"]
