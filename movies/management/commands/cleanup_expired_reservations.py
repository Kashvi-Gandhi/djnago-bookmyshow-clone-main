from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from movies.payment_service import cleanup_expired_reservations


class Command(BaseCommand):
    help = "Clean up expired payment reservations and release seats"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be cleaned up without actually doing it'
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        if dry_run:
            self.stdout.write('DRY RUN - Would clean up expired reservations')
            # We could implement a dry run version, but for now just note it
            return

        self.stdout.write('Cleaning up expired payment reservations...')

        try:
            expired_count, payment_count = cleanup_expired_reservations()
            self.stdout.write(
                self.style.SUCCESS(f'Successfully cleaned up {expired_count} expired seat reservations and {payment_count} expired payments')
            )
        except Exception as e:
            self.stderr.write(
                self.style.ERROR(f'Error cleaning up reservations: {e}')
            )