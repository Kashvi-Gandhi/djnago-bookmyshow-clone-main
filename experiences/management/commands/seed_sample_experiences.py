from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from experiences.models import Experience, ExperienceType, Seat, Session, Venue


class Command(BaseCommand):
    help = "Seed sample Events/Premieres/Music Studios with sessions and seats."

    def add_arguments(self, parser):
        parser.add_argument("--wipe", action="store_true", help="Delete existing experiences data first")

    def handle(self, *args, **options):
        if options["wipe"]:
            Seat.objects.all().delete()
            Session.objects.all().delete()
            Experience.objects.all().delete()
            Venue.objects.all().delete()

        venues = [
            ("City Arena", "Delhi", "Connaught Place"),
            ("Laugh Club", "Mumbai", "Bandra West"),
            ("Studio 7", "Bengaluru", "Indiranagar"),
        ]
        venue_objs = []
        for name, city, address in venues:
            venue, _ = Venue.objects.get_or_create(name=name, city=city, defaults={"address": address})
            venue_objs.append(venue)

        samples = [
            (ExperienceType.EVENT, "Standup Night", 8.6, "A comedy night with top performers", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
            (ExperienceType.EVENT, "Rock Concert", 9.1, "Live rock concert", ""),
            (ExperienceType.PREMIERE, "Indie Film Premiere", 8.2, "Premiere screening + Q&A", ""),
            (ExperienceType.MUSIC_STUDIO, "Rehearsal Slot (2 hours)", 8.0, "Book a rehearsal slot with instruments", ""),
        ]

        now = timezone.now()
        for idx, (etype, name, rating, desc, trailer) in enumerate(samples):
            exp, _ = Experience.objects.get_or_create(
                name=name,
                type=etype,
                defaults={"rating": rating, "description": desc, "trailer_url": trailer or None},
            )

            for offset_days in [0, 1, 3]:
                session, _ = Session.objects.get_or_create(
                    experience=exp,
                    venue=venue_objs[idx % len(venue_objs)],
                    start_time=now + timedelta(days=offset_days, hours=2 + idx),
                    defaults={"ticket_price": 299 + idx * 50, "seat_map_enabled": True},
                )
                if not session.seats.exists():
                    # Create a simple 40-seat map: A1-A20, B1-B20
                    seats = []
                    for row in ["A", "B"]:
                        for n in range(1, 21):
                            seats.append(Seat(session=session, seat_number=f"{row}{n}"))
                    Seat.objects.bulk_create(seats)

        self.stdout.write(self.style.SUCCESS("Seeded sample experiences."))

