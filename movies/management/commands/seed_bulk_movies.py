import random
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from movies.models import Genre, Language, Movie


class Command(BaseCommand):
    help = "Seed a large catalog (default 6000 movies) for performance testing. Safe to rerun; only missing titles are added."

    def add_arguments(self, parser):
        parser.add_argument(
            "--count",
            type=int,
            default=6000,
            help="How many movies to ensure exist (default: 6000)",
        )

    def handle(self, *args, **options):
        count = options["count"]

        # Ensure core vocabularies
        genres = [
            ("Action", "action"),
            ("Drama", "drama"),
            ("Comedy", "comedy"),
            ("Sci-Fi", "sci-fi"),
            ("Horror", "horror"),
            ("Animation", "animation"),
        ]
        languages = [("en", "English"), ("hi", "Hindi"), ("ta", "Tamil"), ("te", "Telugu"), ("ml", "Malayalam")]

        genre_objs = {name: Genre.objects.get_or_create(name=name, slug=slug)[0] for name, slug in genres}
        lang_objs = {code: Language.objects.get_or_create(code=code, name=name)[0] for code, name in languages}

        media_dir = Path(settings.MEDIA_ROOT) / "movies"
        media_dir.mkdir(parents=True, exist_ok=True)
        placeholder = media_dir / "placeholder.gif"
        if not placeholder.exists():
            placeholder.write_bytes(
                b"\x47\x49\x46\x38\x39\x61\x02\x00\x02\x00\x80\x00\x00\x00\x00\x00"
                b"\xFF\xFF\xFF\x21\xF9\x04\x00\x00\x00\x00\x00\x2C\x00\x00\x00\x00"
                b"\x02\x00\x02\x00\x00\x02\x02\x4C\x01\x00\x3B"
            )

        current = Movie.objects.count()
        to_create = max(count - current, 0)
        if to_create == 0:
            self.stdout.write(self.style.SUCCESS(f"Already have {current} movies; nothing to add."))
            return

        batch_size = 500
        created = 0
        for start in range(0, to_create, batch_size):
            batch = []
            for i in range(start, min(start + batch_size, to_create)):
                idx = current + i + 1
                title = f"Perf Movie {idx}"
                lang = random.choice(list(lang_objs.values()))
                rating = round(random.uniform(5.0, 9.5), 1)
                movie = Movie(
                    name=title,
                    rating=rating,
                    cast="TBD",
                    description="Synthetic performance seed",
                    language=lang,
                    image=f"movies/{placeholder.name}",
                )
                batch.append(movie)
            with transaction.atomic():
                Movie.objects.bulk_create(batch, batch_size=batch_size)
                created += len(batch)
        # Attach two random genres to each new movie via through table inserts
        new_movies = Movie.objects.order_by('-id')[:created]
        genre_list = list(genre_objs.values())
        through_model = Movie.genres.through
        through_batch = []
        for movie in new_movies:
            chosen = random.sample(genre_list, k=min(2, len(genre_list)))
            for genre in chosen:
                through_batch.append(through_model(movie_id=movie.id, genre_id=genre.id))
        through_model.objects.bulk_create(through_batch, batch_size=batch_size, ignore_conflicts=True)

        self.stdout.write(self.style.SUCCESS(f"Created {created} movies (total now {Movie.objects.count()})."))
