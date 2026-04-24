import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)

_scheduler = None


def get_scheduler():
    global _scheduler
    if _scheduler is None:
        raise ImproperlyConfigured("Scheduler not initialized. Call start_scheduler() first.")
    return _scheduler


def start_scheduler():
    global _scheduler

    if not getattr(settings, "ENABLE_BACKGROUND_SCHEDULER", True):
        return

    if settings.DEBUG and os.environ.get("RUN_MAIN") != "true":
        return

    if _scheduler is not None and _scheduler.running:
        return

    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        func=_cleanup_task,
        trigger="interval",
        seconds=getattr(settings, "RESERVATION_CLEANUP_INTERVAL_SECONDS", 30),
        id="experiences_cleanup_expired",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    _scheduler.start()


def _cleanup_task():
    try:
        from .payment_service import cleanup_expired_reservations

        cleanup_expired_reservations()
    except Exception:
        logger.exception("Experiences cleanup task failed")


def on_app_ready():
    start_scheduler()

