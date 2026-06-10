from movies.models import Theater, Seat

theaters = Theater.objects.all()
seat_numbers = ["A1", "A2", "A3", "A4", "A5"]

for theater in theaters:
    for seat_no in seat_numbers:
        Seat.objects.get_or_create(
            theater=theater,
            seat_number=seat_no
        )

print("Seats added successfully!")