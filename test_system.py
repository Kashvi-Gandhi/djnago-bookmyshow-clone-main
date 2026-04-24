def main():
    import os

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bookmyseat.settings")

    import django

    django.setup()

    from movies.models import Movie, Theater, Seat, Booking, Payment
    from django.db import connection

    print("=" * 60)
    print("COMPREHENSIVE TESTING SCRIPT")
    print("=" * 60)

    # TEST 1: Check movie data
    print("\n1. CHECKING MOVIE DATA")
    print("-" * 60)
    sample_movies = ["Skyfall", "Interstellar", "Conjuring", "RRR", "Inception"]
    movies_ok = 0
    for name in sample_movies:
        try:
            m = Movie.objects.get(name=name)
            has_image = bool(m.image)
            has_trailer = bool(m.trailer_url)
            if has_image and has_trailer:
                print(f"  [OK] {name}: image & trailer present")
                movies_ok += 1
            else:
                print(f"  [FAIL] {name}: image={has_image}, trailer={has_trailer}")
        except Movie.DoesNotExist:
            print(f"  [FAIL] {name}: NOT FOUND")

    print(f"\nResult: {movies_ok}/{len(sample_movies)} movies have complete data")

    # TEST 2: Check theaters and seats
    print("\n2. CHECKING THEATERS AND SEATS")
    print("-" * 60)
    theaters_with_seats = Theater.objects.filter(seats__isnull=False).distinct().count()
    total_seats = Seat.objects.count()
    print(f"  Theaters with seats: {theaters_with_seats}")
    print(f"  Total seats in system: {total_seats}")

    # Check a specific theater
    if theaters_with_seats > 0:
        theater = Theater.objects.filter(seats__isnull=False).first()
        seats = Seat.objects.filter(theater=theater)
        print(f"  Sample theater: {theater.name}")
        print(f"  Number of seats: {seats.count()}")
        booked_count = seats.filter(is_booked=True).count()
        print(f"  Booked seats: {booked_count}")
        print(f"  Available seats: {seats.count() - booked_count}")

    # TEST 3: Check bookings and payments
    print("\n3. CHECKING BOOKINGS AND PAYMENTS")
    print("-" * 60)
    total_bookings = Booking.objects.count()
    total_payments = Payment.objects.count()
    booking_statuses = Booking.objects.values("status").distinct()
    payment_statuses = Payment.objects.values("status").distinct()

    print(f"  Total bookings: {total_bookings}")
    print("  Booking statuses:")
    for bs in booking_statuses:
        count = Booking.objects.filter(status=bs["status"]).count()
        print(f"    - {bs['status']}: {count}")

    print(f"\n  Total payments: {total_payments}")
    print("  Payment statuses:")
    for ps in payment_statuses:
        count = Payment.objects.filter(status=ps["status"]).count()
        print(f"    - {ps['status']}: {count}")

    # TEST 4: Check for data consistency issues
    print("\n4. CHECKING DATA CONSISTENCY")
    print("-" * 60)

    pending_payments = Payment.objects.filter(status="pending").select_related("booking__seat")
    print(f"  Pending payments: {pending_payments.count()}")
    if pending_payments.exists():
        for p in pending_payments[:3]:
            print(f"    - Payment {p.id}: booking {p.booking.id}, seat {p.booking.seat.seat_number}")

    cursor = connection.cursor()
    cursor.execute(
        """
        SELECT COUNT(*) FROM movies_seat s
        WHERE s.is_booked = TRUE
        AND NOT EXISTS (
            SELECT 1 FROM movies_booking b
            WHERE b.seat_id = s.id AND b.status = 'pending_payment'
        )
        """
    )
    orphaned_seats = cursor.fetchone()[0]
    print(f"  Seats marked booked without pending payment: {orphaned_seats}")
    if orphaned_seats > 0:
        print("    [WARNING] Found orphaned booked seats!")

    # TEST 5: Database integrity
    print("\n5. DATABASE INTEGRITY")
    print("-" * 60)
    cursor.execute("SELECT COUNT(*) FROM movies_movie")
    total_movies = cursor.fetchone()[0]
    print(f"  Total movies in database: {total_movies}")

    movies_with_trailers = Movie.objects.exclude(trailer_url="").exclude(trailer_url__isnull=True).count()
    print(f"  Movies with trailer URLs: {movies_with_trailers}")

    movies_with_images = Movie.objects.exclude(image="").count()
    print(f"  Movies with images: {movies_with_images}")

    # TEST 6: Sample seat availability check
    print("\n6. SEAT AVAILABILITY SAMPLE CHECK")
    print("-" * 60)
    if Theater.objects.exists():
        sample_theater = Theater.objects.first()
        seats_sample = Seat.objects.filter(theater=sample_theater)[:10]
        print(f"  Theater: {sample_theater.name}")
        print("  Sample seats:")
        for seat in seats_sample:
            status = "BOOKED" if seat.is_booked else "AVAILABLE"
            reservation = getattr(seat, "reservation", None)
            res_status = ""
            if reservation and not getattr(reservation, "is_expired", False):
                res_status = f" (reserved until {reservation.expires_at})"
            print(f"    - {seat.seat_number}: {status}{res_status}")

    print("\n" + "=" * 60)
    print("TESTING COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
