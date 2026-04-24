import time
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone
from django.conf import settings
from django.db import transaction

from movies.models import EmailQueue

MAX_ATTEMPTS = 5
BASE_BACKOFF = 60  # seconds


class Command(BaseCommand):
    help = "Process queued emails asynchronously with retry/backoff."

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=20, help='Maximum emails to send in one run')
        parser.add_argument('--dry-run', action='store_true', help='Render but do not send')
        parser.add_argument('--loop', action='store_true', help='Run continuously with pauses between batches')
        parser.add_argument('--sleep', type=int, default=30, help='Seconds to sleep between batches in loop mode')

    def handle(self, *args, **options):
        limit = options['limit']
        dry_run = options['dry_run']
        loop = options['loop']
        sleep_seconds = max(5, options['sleep'])

        while True:
            now = timezone.now()
            qs = EmailQueue.objects.filter(status='queued', scheduled_at__lte=now).order_by('scheduled_at', 'id')[:limit]
            if not qs:
                self.stdout.write(self.style.WARNING('No queued emails to process.'))

            sent_count = 0
            failed_count = 0
            for email_task in qs:
                with transaction.atomic():
                    task = EmailQueue.objects.select_for_update().get(pk=email_task.pk)
                    if task.status != 'queued' or task.scheduled_at > timezone.now():
                        continue
                    try:
                        subject = task.subject
                        ctx = task.context
                        txt_body = render_to_string(f"{task.template}.txt", ctx)
                        html_body = render_to_string(f"{task.template}.html", ctx)
                        msg = EmailMultiAlternatives(subject, txt_body, settings.DEFAULT_FROM_EMAIL, [task.to_email])
                        msg.attach_alternative(html_body, "text/html")
                        if dry_run:
                            self.stdout.write(f"[dry-run] would send to {task.to_email}: {subject}")
                        else:
                            msg.send(fail_silently=False)
                        task.status = 'sent'
                        task.last_error = ''
                        task.attempts += 1
                        task.save(update_fields=['status', 'last_error', 'attempts', 'updated_at'])
                        self.stdout.write(self.style.SUCCESS(f"Sent email to {task.to_email}"))
                        sent_count += 1
                    except Exception as exc:  # noqa: BLE001
                        task.attempts += 1
                        task.status = 'failed' if task.attempts >= MAX_ATTEMPTS else 'queued'
                        task.last_error = str(exc)
                        if task.status == 'queued':
                            backoff = BASE_BACKOFF * (2 ** (task.attempts - 1))
                            task.scheduled_at = timezone.now() + timedelta(seconds=backoff)
                        task.save(update_fields=['status', 'attempts', 'last_error', 'scheduled_at', 'updated_at'])
                        self.stderr.write(self.style.ERROR(f"Failed to send email to {task.to_email}: {exc}"))
                        failed_count += 1

            if qs:
                summary = f"Batch complete: sent={sent_count}, failed(or requeued)={failed_count}"
                if failed_count:
                    self.stderr.write(self.style.WARNING(summary))
                else:
                    self.stdout.write(summary)

            if not loop:
                break
            time.sleep(sleep_seconds)
