import time
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.management.base import BaseCommand
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from experiences.models import EmailQueue

MAX_ATTEMPTS = 5
BASE_BACKOFF = 60


class Command(BaseCommand):
    help = "Process queued experience emails asynchronously with retry/backoff."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=20)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--loop", action="store_true")
        parser.add_argument("--sleep", type=int, default=30)

    def handle(self, *args, **options):
        limit = options["limit"]
        dry_run = options["dry_run"]
        loop = options["loop"]
        sleep_seconds = max(5, options["sleep"])

        while True:
            now = timezone.now()
            qs = (
                EmailQueue.objects.filter(status="queued", scheduled_at__lte=now)
                .order_by("scheduled_at", "id")[:limit]
            )
            if not qs:
                self.stdout.write(self.style.WARNING("No queued emails to process."))

            sent_count = 0
            failed_count = 0
            for email_task in qs:
                with transaction.atomic():
                    task = EmailQueue.objects.select_for_update().get(pk=email_task.pk)
                    if task.status != "queued" or task.scheduled_at > timezone.now():
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
                        task.status = "sent"
                        task.last_error = ""
                        task.attempts += 1
                        task.save(update_fields=["status", "last_error", "attempts", "updated_at"])
                        sent_count += 1
                    except Exception as exc:  # noqa: BLE001
                        task.attempts += 1
                        task.status = "failed" if task.attempts >= MAX_ATTEMPTS else "queued"
                        task.last_error = str(exc)
                        if task.status == "queued":
                            backoff = BASE_BACKOFF * (2 ** (task.attempts - 1))
                            task.scheduled_at = timezone.now() + timedelta(seconds=backoff)
                        task.save(update_fields=["status", "attempts", "last_error", "scheduled_at", "updated_at"])
                        failed_count += 1

            if qs:
                self.stdout.write(f"Batch complete: sent={sent_count}, failed(or requeued)={failed_count}")

            if not loop:
                break
            time.sleep(sleep_seconds)

