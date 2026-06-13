from django.core.management.base import BaseCommand
from movies.models import Theater, Seat, Movie

class Command(BaseCommand):
    help = 'Add seats to all existing theaters'

    def handle(self, *args, **kwargs):
        theaters = Theater.objects.all()
        seat_numbers = ["A1", "A2", "A3", "A4", "A5", "B1", "B2", "B3", "B4", "B5"]

        for theater in theaters:
            for seat_no in seat_numbers:
                Seat.objects.get_or_create(
                    theater=theater,
                    seat_number=seat_no
                )

        self.stdout.write(self.style.SUCCESS("Seats added successfully to all theaters!"))