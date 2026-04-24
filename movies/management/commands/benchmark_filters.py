import time
from django.core.management.base import BaseCommand
from django.db import connection, models
from movies.models import Movie, Genre, Language


class Command(BaseCommand):
    help = "Benchmark common movie filters and pagination paths. Run after seeding (e.g., 6000 rows)."

    def add_arguments(self, parser):
        parser.add_argument('--iterations', type=int, default=3, help='Repetitions per query')
        parser.add_argument('--page-size', type=int, default=12, help='Page size to test for offset pagination')

    def handle(self, *args, **options):
        iterations = options['iterations']
        page_size = options['page_size']

        if Movie.objects.count() == 0:
            self.stdout.write(self.style.ERROR('No movies to benchmark. Seed data first.'))
            return

        genre = Genre.objects.order_by('id').first()
        language = Language.objects.order_by('id').first()
        if not (genre and language):
            self.stdout.write(self.style.ERROR('Need at least one genre and one language.'))
            return

        cases = [
            ('name prefix search', lambda: list(Movie.objects.filter(name__istartswith='M').values_list('id', flat=True)[:page_size])),
            ('language filter + sort', lambda: list(Movie.objects.filter(language=language).order_by('-rating', 'id').values_list('id', flat=True)[:page_size])),
            ('genre filter + sort', lambda: list(Movie.objects.filter(genres=genre).order_by('name', 'id').values_list('id', flat=True)[:page_size])),
            ('facet counts (genres)', lambda: Genre.objects.annotate(filtered_count=models.Count('movies', distinct=True)).values_list('id', 'filtered_count')[:10]),
        ]

        self.stdout.write(self.style.WARNING(f'Benchmarking with {Movie.objects.count()} movies'))
        for label, fn in cases:
            times = []
            for _ in range(iterations):
                start = time.perf_counter()
                fn()
                times.append((time.perf_counter() - start) * 1000)
            avg = sum(times) / len(times)
            self.stdout.write(f'{label}: {avg:.2f} ms (avg of {iterations})')

        if connection.vendor == 'postgresql':
            self.stdout.write(self.style.SUCCESS('Tip: enable pg_stat_statements to watch index usage during these queries.'))
