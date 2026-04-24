# Concurrency-Safe Seat Reservation System with Auto Timeout

## Executive Summary

This document explains the complete implementation of a concurrency-safe seat reservation system that:
- **Temporarily locks** selected seats for **2 minutes** before payment completion
- **Prevents double-booking** under simultaneous requests (even millisecond-level concurrency)
- Uses **database-level atomic transactions** and **row-level locking**
- **Automatically releases** expired reservations via background scheduler
- Handles **edge cases** (app closure, network interruption, multi-device scenarios)

---

## 1. Concurrency Model: PESSIMISTIC LOCKING WITH ATOMIC TRANSACTIONS

### Core Principles

The system uses **pessimistic locking** combined with **atomic transactions** to guarantee consistency:

```
┌─────────────────────────────────────────────────────────────┐
│                    PESSIMISTIC LOCKING                       │
├─────────────────────────────────────────────────────────────┤
│ Database explicitly acquires locks before any modification  │
│ - SELECT FOR UPDATE: Row-level database lock               │
│ - Atomic Transaction: All-or-nothing semantics             │
│ - Guaranteed ordering: SQL execution queue                 │
└─────────────────────────────────────────────────────────────┘
```

### Why Not Optimistic Locking?

Optimistic locking (version checking) would cause too many conflicts:
- High throughput during booking rush = many version conflicts
- Retry overhead could exceed transaction time
- User experience: "Please try again" messages frustrate users

Pessimistic locking is preferred for seat booking because:
- Guarantees first lock = first success (fair)
- No retry loops needed
- Predictable latency

---

## 2. Race Condition Scenarios & Prevention

### Scenario 1: Two Users Booking the Same Seat Simultaneously

**Timeline (millisecond precision):**
```
T=0ms    | User A: Request POST /book_seats (seat #42)
T=1ms    | User B: Request POST /book_seats (seat #42)
T=10ms   | User A: SELECT FOR UPDATE seats WHERE id=42
         | → Database acquires EXCLUSIVE LOCK on row
T=12ms   | User B: SELECT FOR UPDATE seats WHERE id=42
         | → Database WAITS (queue A is first)
T=50ms   | User A: INSERT INTO SeatReservation (seat #42, 2-min timeout)
         | User A: CREATE Booking + Payment Intent
         | User A: COMMIT transaction
         | → Database releases lock on seat #42
T=51ms   | User B: SELECT FOR UPDATE seats WHERE id=42
         | → CONFLICT: Seat #42 now has active reservation (excluded)
         | User B: len(seats) != len(seat_ids)
         | → Return error: "Seat no longer available"
         | User B: ROLLBACK transaction
```

**Prevention Mechanism:**
1. `Seat.objects.select_for_update()` makes database queue requests
2. Database processes in order (User A first)
3. User A's reservation exclusion filter prevents User B from seeing that seat
4. User B gets clean error message

### Scenario 2: User Closes App During Reservation

**Timeline:**
```
T=0ms    | User A: POST /book_seats (reserves 2 minutes)
T=50ms   | Reservation created: expires_at = now + 2 min
T=51ms   | User closes app (no CANCEL signal)
T=100ms  | Payment timeout expires in Stripe
         | Booking still in pending_payment state
T=2min   | Cleanup scheduler runs:
         | - Finds expired SeatReservation
         | - DELETE FROM SeatReservation WHERE expires_at < now
         | - Seats automatically free (deleted reservation)
T=15min  | Next cleanup run:
         | - Finds pending Payment (created 14 min ago)
         | - Payment.status = 'cancelled'
         | - Booking.status = 'cancelled'
         | - Seat.is_booked = False (released)
```

**Prevention Mechanism:**
1. Reservations auto-expire after 2 mins
2. Cleanup scheduler runs every 5 minutes
3. Database queries use expiration index for fast lookup
4. No orphaned locks possible - time always wins

### Scenario 3: Network Interruption During Payment Creation

**Timeline:**
```
T=0ms    | reserve_seats() completes successfully
T=10ms   | create_booking_with_payment_from_reservations() starts
T=50ms   | Transaction atomic() begins
T=100ms  | INSERT Booking rows
T=150ms  | stripe.PaymentIntent.create() → NETWORK TIMEOUT
         | Connection hangs for 30 seconds
T=30s    | Network timeout error raised
         | Django: transaction.atomic() context manager catches error
         | Django: ROLLBACK all changes
         | SeatReservation still exists (not deleted in transaction)
         | Seat.is_booked reverts to False (no UPDATE succeeded)
T=30.1s  | User sees error: "Network error, please try again"
T=32s    | Next user retries: reserve_seats()
         | Existing reservation found, EXCLUDED from query
         | User gets: seats = [] (already reserved by User A)
T=4min   | Cleanup finds User A's reservation: expires_at < now
         | DELETE SeatReservation → seats freed
         | User B can now reserve
```

**Prevention Mechanism:**
1. Entire operation in atomic() block
2. Any exception triggers ROLLBACK
3. Database never left in inconsistent state
4. Reservations survive rollback (auto-cleanup recovers)

### Scenario 4: Multiple Devices - Same User Selecting Seats

**Timeline:**
```
Device A (Mobile) | Device B (Web)
───────────────────┼───────────────
T=0ms Reserve       | 
      Seat #42      |
      ✓ Reserved    |
                    | T=2ms Check availability
                    |      GET /api/seat-availability
                    |      Returns: [{"id": 42, "is_reserved": true}]
                    |      Reserve Seat #40 instead
                    |      ✓ Reserved
T=10ms Go to payment|
       for both     | T=11ms Go to payment
       seats        |       for both seats
```

**Prevention Mechanism:**
1. `seat_availability_api()` provides real-time status
2. Clients can check before confirming
3. Backend API returns reservation timeouts
4. Clients can implement retry logic

---

## 3. Database Consistency Model

### Transaction Isolation Level

**SQLite (Default):**
- Uses `transaction.atomic()` block
- Provides SERIALIZABLE isolation for explicit transactions
- Lower throughput but guaranteed ACID

**PostgreSQL (Recommended for Production):**
```python
# Settings: Use explicit transaction locking
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'ISOLATION_LEVEL': psycopg2.extensions.ISOLATION_LEVEL_SERIALIZABLE,
    }
}
```

### Key Consistency Guarantees

| Guarantee | Mechanism | Verification |
|-----------|-----------|--------------|
| **No double-booking** | SELECT FOR UPDATE locks | If 2 users book same seat, second gets error |
| **All-or-nothing** | atomic() transaction | Partial bookings never occur |
| **Ordered fairness** | Database lock queue | First requestor always wins |
| **Eventual consistency** | Cleanup scheduler | All locks released within 17 minutes max |
| **Idempotency** | Unique constraints | Webhook handlers safe to retry |

---

## 4. Implementation Details

### A. Seat Reservation Flow

```python
# movies/payment_service.py: reserve_seats()

def reserve_seats(user, seat_ids, theater_id, reservation_minutes=2):
    with transaction.atomic():
        # Step 1: ACQUIRE LOCKS
        theater = Theater.objects.select_for_update().get(id=theater_id)
        seats = Seat.objects.select_for_update().filter(
            id__in=seat_ids, 
            theater=theater, 
            is_booked=False
        ).exclude(
            reservation__expires_at__gt=timezone.now()  # Skip active reservations
        )
        
        # Step 2: VALIDATE
        if len(seats) != len(seat_ids):
            return False, "Some seats are no longer available"
        
        # Step 3: CREATE RESERVATIONS (all-or-nothing within transaction)
        expires_at = timezone.now() + timedelta(minutes=2)
        reservations = []
        for seat in seats:
            reservation = SeatReservation.objects.create(
                seat=seat,
                user=user,
                expires_at=expires_at
            )
            reservations.append(reservation)
        
        return True, reservations
        # If any exception here: ROLLBACK, all reservations deleted
```

**Concurrency Analysis:**
- `select_for_update()`: Database queues concurrent requests in order
- `.exclude(reservation__expires_at__gt=now())`: Prevents seeing active reservations
- `if len(seats) != len(seat_ids)`: Single check at end (no TOCTOU gaps)
- Loop is safe: All creations happen under lock

### B. Payment Creation with Atomic Rollback

```python
# movies/payment_service.py: create_booking_with_payment_from_reservations()

def create_booking_with_payment_from_reservations(user, reservations, theater_id):
    try:
        with transaction.atomic():
            # Locks theater and validates reservations still active
            theater = Theater.objects.select_for_update().get(id=theater_id)
            valid_reservations = SeatReservation.objects.select_for_update().filter(
                id__in=reservation_ids,
                user=user,
                expires_at__gt=timezone.now(),  # Expired? Fail here
                seat__theater=theater,
                seat__is_booked=False
            )
            
            if len(valid_reservations) != len(reservations):
                return False, "Some reservations have expired"
            
            # Create bookings
            for reservation in valid_reservations:
                booking = Booking.objects.create(...)
                reservation.seat.is_booked = True
                reservation.seat.save()
            
            # Create Stripe payment intent (may fail with network error)
            payment_intent = stripe.PaymentIntent.create(...)
            
            # Create payment record
            payment = Payment.objects.create(
                stripe_payment_intent_id=payment_intent.id,
                ...
            )
            
            return True, {...}
    
    except Exception as e:
        # ROLLBACK automatically executed here
        # - All bookings deleted
        # - Seats revert: is_booked = False
        # - Reservations still exist (not in transaction)
        # - User can retry after reservation times out
        logger.error(f"Error: {e}")
        return False, f"Error: {str(e)}"
```

**Why reservation survives rollback:**
- Reservation created in `reserve_seats()` → different atomic() block
- Booking created in `create_booking_with_payment()` → different atomic() block
- If payment fails, booking deleted but reservation remains
- After 2 minutes: cleanup deletes reservation, seat freed

### C. Background Cleanup Scheduler

```python
# movies/scheduler.py: Runs every 5 minutes

def cleanup_expired_reservations():
    """
    1. Find reservations where expires_at < now (2-min window)
    2. Find payments where created_at < (now - 15 min) and status='pending'
    3. Cancel Stripe payment intents
    4. Release all seats
    5. Mark bookings as cancelled
    """
    
    # Atomic per payment: either all succeeds or none
    for payment in expired_payments:
        with transaction.atomic():
            # Acquire lock to prevent race
            payment = Payment.objects.select_for_update().get(id=payment.id)
            
            # Cancel Stripe (idempotent - safe to retry)
            payment_service.cancel_payment(payment.stripe_payment_intent_id)
            
            # Release seats
            bookings = Booking.objects.filter(...)
            seat_ids = [b.seat_id for b in bookings]
            Seat.objects.filter(id__in=seat_ids).update(is_booked=False)
            
            # Mark as cancelled
            payment.status = 'cancelled'
            payment.save()
```

**Guarantees:**
- If scheduler crashes mid-cleanup: Partial update OK (next run retries)
- If scheduler slower than expected: OK, payment already cancelled
- If multiple schedulers run: Database serialization prevents duplicate work

---

## 5. Edge Cases & How They're Handled

### Case 1: User Closes App Without Completing Payment

**Sequence:**
1. User: Reserve seats → Reserved (expires 2 min)
2. User: Close app (no clean close signal sent)
3. Browser: Stops sending signals
4. Server: Doesn't know user closed app
5. After 2 minutes: Scheduler cleanup runs
6. Cleanup: Deletes expired SeatReservation → Seat freed

**Time to recovery:** 2 minutes (reservation timeout) + 5 minutes (cleanup interval) = **max 7 minutes**

### Case 2: Payment Fails Due to Network Interruption

**Sequence:**
1. User: Reserve seats → OK
2. User: Complete form, submit payment
3. Server: Create booking → OK
4. Server: Call stripe.PaymentIntent.create() → TIMEOUT after 30s
5. Exception raised → transaction.atomic() rolls back
6. Booking deleted, seat is_booked reverted
7. SeatReservation unchanged (2-min lease still active)
8. User: Receives error message: "Network error, please retry"
9. User: Can retry payment or re-reserve

**Advantage:** No orphaned bookings in inconsistent state

### Case 3: Stripe Webhook Never Arrives (Network Failure)

**Sequence:**
1. User: Completes payment successfully on Stripe
2. Stripe: Tries to send webhook → Network timeout
3. Server: Never receives payment_intent.succeeded event
4. Payment: Stuck in "pending" status
5. After 15 minutes: Cleanup scheduler runs
6. Scheduler: Finds payment older than 15 min with status='pending'
7. Cleanup: Cancels payment intent (idempotent), releases seats, marks as cancelled
8. User: Booking cancelled despite successful payment

**User Recovery:**
- User sees: "Booking cancelled due to timeout"
- User can retry booking
- Stripe has received payment (refund process manual)

**Prevention:** Implement webhook retry in Stripe dashboard settings

### Case 4: Multiple Device Bookings (User on Mobile & Web)

**Sequence:**
1. Device A (Mobile): Reserve seat #42, #43
2. Device B (Web): GET /api/seat-availability
3. API Response: `{"seats": [...], "reserved_seats": 2, "available_seats": 8}`
4. Device B: Shows User: "2 seats reserved elsewhere (expires in 1 min 45 sec)"
5. Device B: User chooses seat #44, #45 instead
6. Device B: POST /book_seats for seats #44, #45
7. Server: Reserve #44, #45 → OK (different seats)
8. Device A: Complete payment → seats #42, #43 confirmed
9. Device B: Complete payment → seats #44, #45 confirmed
10. Result: User accidentally booked 4 seats!

**Mitigation:** Application-level check:
```javascript
// Client-side before booking:
const maxSeats = 2; // Theater policy
if (userBookedSeatsCount > maxSeats) {
    alert("Maximum 2 seats per booking");
}
```

---

## 6. Database Schema for Concurrency

### Indices for Performance

```python
# movies/models.py

class SeatReservation(models.Model):
    seat = models.OneToOneField(Seat, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    
    class Meta:
        # CRITICAL: Index on expires_at for fast cleanup queries
        indexes = [
            models.Index(fields=["expires_at"]),  # For cleanup: WHERE expires_at < now
            models.Index(fields=["seat", "expires_at"]),  # For seat availability check
        ]
```

### Why `OneToOne` Not `ForeignKey`?

| Aspect | OneToOne | ForeignKey |
|--------|----------|-----------|
| Multiple users reserving same seat | Prevented | Possible (bad!) |
| Database constraint | Unique seat_id | Not unique |
| Query efficiency | Faster (unique lookup) | Slower (multiple rows) |
| Concurrency safety | Better (unique constraint) | Worse (multiple rows) |

---

## 7. Testing for Race Conditions

### Test 1: Simultaneous Booking Attempt

```python
# movies/tests.py

from concurrent.futures import ThreadPoolExecutor

def test_concurrent_seat_booking():
    """Test that only one user can book a seat when both try simultaneously"""
    theater = Theater.objects.create(...)
    seat = Seat.objects.create(theater=theater, seat_number="A1")
    user1 = User.objects.create(username="user1")
    user2 = User.objects.create(username="user2")
    
    results = []
    
    def book_seat(user):
        success, result = reserve_seats(user, [seat.id], theater.id)
        results.append((user.username, success, result))
    
    # Execute both bookings in parallel threads
    with ThreadPoolExecutor(max_workers=2) as executor:
        executor.submit(book_seat, user1)
        executor.submit(book_seat, user2)
    
    # Assertion: Only one should succeed
    successes = sum(1 for _, success, _ in results if success)
    assert successes == 1, f"Expected 1 success, got {successes}"
```

### Test 2: Reservation Expiry

```python
def test_reservation_expiry_and_cleanup():
    """Test that expired reservations are cleaned up"""
    theater = Theater.objects.create(...)
    seat = Seat.objects.create(theater=theater, seat_number="A1")
    user = User.objects.create(username="user1")
    
    # Create reservation expiring in 1 second
    reservation = SeatReservation.objects.create(
        seat=seat,
        user=user,
        expires_at=timezone.now() + timedelta(seconds=1)
    )
    
    assert SeatReservation.objects.count() == 1
    
    time.sleep(2)  # Wait for expiry
    
    cleanup_count, _ = cleanup_expired_reservations()
    
    assert cleanup_count == 1
    assert SeatReservation.objects.count() == 0
    assert not seat.is_booked  # Seat should be free
```

---

## 8. Monitoring & Observability

### Key Metrics to Monitor

```python
# In logging and APM
logger.info(
    "Seat reserved",
    extra={
        "user_id": user.id,
        "theater_id": theater_id,
        "seat_count": len(reservations),
        "expires_at": expires_at.isoformat(),
        "duration_ms": (timezone.now() - start_time).total_seconds() * 1000,
    }
)

# Alerts to set up:
# 1. Cleanup tasks failing > 2x consecutive runs
# 2. Lock wait time > 5 seconds
# 3. Expired payments accumulating (not being cleaned)
# 4. Payment webhook failure rate > 1%
```

---

## 9. Summary: Consistency Model

| Aspect | Implementation | Guarantee |
|--------|---|---|
| **Double-booking Prevention** | SELECT FOR UPDATE + atomic transaction | 0 double-bookings possible |
| **Reservation Timeout** | 2-minute timeout in database | Seats auto-free after 2 min |
| **Payment Timeout** | 15-minute timeout, cleanup scheduler | Max 17 min to free seats on fail |
| **App Closure** | Reservation survives, cleanup after timeout | Seats free automatically |
| **Network Issues** | Atomic rollback, reservation survives | No orphaned bookings |
| **Webhook Timeout** | Cleanup finds unpaired payment after 15 min | Seats freed even if payment lost |
| **Multiple Devices** | Real-time availability API | User sees current reservations |
| **Concurrency Order** | Database lock queue | First request always wins |

---

## 10. Deployment Checklist

- [ ] Set `DEBUG = False` in production
- [ ] Enable database connection pooling (pgbouncer for PostgreSQL)
- [ ] Set up APScheduler with persistent storage (optional: Redis for multi-process)
- [ ] Configure Stripe webhook retry settings (exponential backoff)
- [ ] Enable application-level logging (Sentry/DataDog)
- [ ] Monitor cleanup task execution
- [ ] Test payment timeout scenarios before production
- [ ] Load test with 100+ concurrent users booking
- [ ] Verify database indices exist (`EXPLAIN ANALYZE`)

---

## References

- Django Transactions: https://docs.djangoproject.com/en/3.2/topics/db/transactions/
- Stripe Webhook Idempotency: https://stripe.com/docs/webhooks#best-practices
- APScheduler: https://apscheduler.readthedocs.io/
- PostgreSQL Row-Level Locking: https://www.postgresql.org/docs/current/sql-select.html#SQL-FOR-UPDATE-SHARE
