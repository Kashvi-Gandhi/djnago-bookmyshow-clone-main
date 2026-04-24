import random
from datetime import timedelta

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.utils import timezone

from movies.models import Genre, Language, Movie, Theater


class Command(BaseCommand):
    help = "Seed sample movies, genres, languages, and theaters for manual testing."

    def handle(self, *args, **options):
        genres = [
            ("Action", "action"),
            ("Drama", "drama"),
            ("Comedy", "comedy"),
            ("Sci-Fi", "sci-fi"),
            ("Horror", "horror"),
            ("Animation", "animation"),
        ]
        languages = [("en", "English"), ("hi", "Hindi"), ("ta", "Tamil"), ("te", "Telugu")]

        genre_objs = {}
        for name, slug in genres:
            genre, _ = Genre.objects.get_or_create(name=name, slug=slug)
            genre_objs[name] = genre

        lang_objs = {}
        for code, name in languages:
            lang, _ = Language.objects.get_or_create(code=code, name=name)
            lang_objs[code] = lang

        dummy_image = ContentFile(
            b"\x47\x49\x46\x38\x39\x61\x02\x00\x02\x00\x80\x00\x00\x00\x00\x00"
            b"\xFF\xFF\xFF\x21\xF9\x04\x00\x00\x00\x00\x00\x2C\x00\x00\x00\x00"
            b"\x02\x00\x02\x00\x00\x02\x02\x4C\x01\x00\x3B",
            name="placeholder.gif",
        )

        movie_templates = [
            ("Skyfall", ["Action", "Drama"], "en", "https://www.youtube.com/watch?v=eIoaqYvEz-0"),
            ("Interstellar", ["Sci-Fi", "Drama"], "en", "https://www.youtube.com/watch?v=zSpeKfwh1QM"),
            ("Dune", ["Sci-Fi"], "en", "https://www.youtube.com/watch?v=n9xhJsagTKo"),
            ("RRR", ["Action", "Drama"], "te", "https://www.youtube.com/watch?v=KfnOrqHeGCo"),
            ("Baahubali", ["Action", "Drama"], "te", "https://www.youtube.com/watch?v=9vPTnBwXbpc"),
            ("Kantara", ["Drama"], "hi", "https://www.youtube.com/watch?v=9zYzUe5Hczo"),
            ("3 Idiots", ["Comedy", "Drama"], "hi", "https://www.youtube.com/watch?v=R-Tg1z1zj4M"),
            ("Inception", ["Action", "Sci-Fi"], "en", "https://www.youtube.com/watch?v=YoHD_XwNDdY"),
            ("Joker", ["Drama"], "en", "https://www.youtube.com/watch?v=zAGVQLHvwOY"),
            ("The Dark Knight", ["Action"], "en", "https://www.youtube.com/watch?v=EXeTwQWrcwY"),
            ("Avatar", ["Sci-Fi"], "en", "https://www.youtube.com/watch?v=6ZfuNTqWZm0"),
            ("Spirited Away", ["Animation", "Drama"], "en", "https://www.youtube.com/watch?v=ByXuk9QAchA"),
            ("Toy Story", ["Animation", "Comedy"], "en", "https://www.youtube.com/watch?v=KYz2wyBy3kc"),
            ("Parasite", ["Drama"], "en", "https://www.youtube.com/watch?v=5xH0HfJHsYw"),
            ("KGF", ["Action"], "hi", "https://www.youtube.com/watch?v=dD--AYAh0zU"),
            ("Pathaan", ["Action"], "hi", "https://www.youtube.com/watch?v=e9T9VVcRc0g"),
            ("Leo", ["Action"], "ta", "https://www.youtube.com/watch?v=zOGs0s8pKhA"),
            ("Vikram", ["Action"], "ta", "https://www.youtube.com/watch?v=G2rMpYUj72A"),
            ("The Nun", ["Horror"], "en", "https://www.youtube.com/watch?v=K87UKJqzpXI"),
            ("Conjuring", ["Horror"], "en", "https://www.youtube.com/watch?v=k10ETZ41T5U"),
            ("IT", ["Horror"], "en", "https://www.youtube.com/watch?v=9Eo7cNenXF0"),
            ("MIB", ["Sci-Fi", "Comedy"], "en", "https://www.youtube.com/watch?v=wW3p3K1Atnw"),
            ("Ghostbusters", ["Comedy", "Sci-Fi"], "en", "https://www.youtube.com/watch?v=X01QNIr0G5U"),
            ("Wall-E", ["Animation", "Sci-Fi"], "en", "https://www.youtube.com/watch?v=CKM_d1q5eec"),
            ("Moana", ["Animation", "Drama"], "en", "https://www.youtube.com/watch?v=7fDKqS5vKsE"),
            ("Up", ["Animation", "Comedy"], "en", "https://www.youtube.com/watch?v=pkqHpqB7nvY"),
            ("Inside Out", ["Animation", "Comedy"], "en", "https://www.youtube.com/watch?v=yRUAzGQ3nSY"),
            ("Tenet", ["Action", "Sci-Fi"], "en", "https://www.youtube.com/watch?v=L3pk_TBkiLc"),
            ("Predator", ["Action", "Sci-Fi"], "en", "https://www.youtube.com/watch?v=RfiQYRn7fBg"),
            ("Gravity", ["Sci-Fi", "Drama"], "en", "https://www.youtube.com/watch?v=OiTiKOy59o4"),
        ]

        created = 0
        for name, genre_list, lang_code, trailer_url in movie_templates:
            lang = lang_objs.get(lang_code, lang_objs["en"])
            movie, new = Movie.objects.get_or_create(
                name=name,
                defaults={
                    "rating": round(random.uniform(6.0, 9.5), 1),
                    "cast": "TBD",
                    "description": f"{name} description",
                    "language": lang,
                    "trailer_url": trailer_url,
                },
            )
            if new or not movie.image:
                movie.image.save(f"{name.lower().replace(' ', '_')}.gif", dummy_image, save=False)
            # Update trailer_url if it's missing
            if not movie.trailer_url and trailer_url:
                movie.trailer_url = trailer_url
            movie.save()
            movie.genres.set([genre_objs[g] for g in genre_list if g in genre_objs])
            created += int(new)

            # Seed 2 theaters per movie with staggered times
            for idx in range(2):
                Theater.objects.get_or_create(
                    name=f"Theater {idx + 1} - {name}",
                    movie=movie,
                    defaults={"time": timezone.now() + timedelta(days=idx, hours=random.randint(1, 6))},
                )

        self.stdout.write(self.style.SUCCESS(f"Seeded/updated {len(movie_templates)} movies ({created} new)."))
