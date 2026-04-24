"""
APScheduler configuration for background tasks in BookMyShow.

BACKGROUND SCHEDULER:
====================
Handles periodic cleanup of expired seat reservations, abandoned payments, and email queue processing.

CONCURRENCY SAFETY:
- APScheduler runs in a separate thread pool
- All database operations are atomic and use transaction locking
- Multiple scheduler instances don't conflict due to database-level atomicity
- Safe for multi-process deployments (each process runs its own scheduler)

TASKS SCHEDULED:
1. cleanup_expired_reservations
   - Interval: Every 30 seconds
   - Purpose: Release expired seat reservations and timeout payments
   - Duration: 10-30 seconds typically

2. process_email_queue
   - Interval: Every 15 seconds
   - Purpose: Send queued booking confirmation emails
   - Duration: 1-5 seconds typically
"""

import logging
import os
from apscheduler.schedulers.background import BackgroundScheduler
from django.core.management import call_command
from django.core.exceptions import ImproperlyConfigured
from django.conf import settings

logger = logging.getLogger(__name__)

# Global scheduler instance (one per process)
_scheduler = None


def get_scheduler():
    """Get or create the background scheduler instance."""
    global _scheduler
    if _scheduler is None:
        raise ImproperlyConfigured("Scheduler not initialized. Call start_scheduler() first.")
    return _scheduler


def start_scheduler():
    """
    Start the background scheduler.
    Called during Django app ready signal.
    """
    global _scheduler

    if not getattr(settings, "ENABLE_BACKGROUND_SCHEDULER", True):
        logger.info("Background scheduler disabled via settings")
        return

    # In DEBUG, Django's autoreloader imports apps twice. Only start in the reloader's main process.
    if settings.DEBUG and os.environ.get("RUN_MAIN") != "true":
        return
    
    if _scheduler is not None and _scheduler.running:
        logger.info("Scheduler already running")
        return
    
    _scheduler = BackgroundScheduler()
    
    # Add cleanup task - runs every 30 seconds
    _scheduler.add_job(
        func=_cleanup_expired_reservations_task,
        trigger="interval",
        seconds=getattr(settings, "RESERVATION_CLEANUP_INTERVAL_SECONDS", 30),
        id="cleanup_expired_reservations",
        name="Cleanup expired seat reservations and payments",
        replace_existing=True,
        max_instances=1,  # Prevent multiple concurrent runs
        coalesce=True,
    )
    
    # Add email queue processing task - runs every 15 seconds
    _scheduler.add_job(
        func=_process_email_queue_task,
        trigger="interval",
        seconds=getattr(settings, "EMAIL_QUEUE_INTERVAL_SECONDS", 15),
        id="process_email_queue",
        name="Process email queue",
        replace_existing=True,
        max_instances=1,  # Prevent multiple concurrent runs
        coalesce=True,
    )
    
    try:
        _scheduler.start()
        logger.info("Background scheduler started successfully")
    except Exception as e:
        logger.error(f"Failed to start background scheduler: {str(e)}", exc_info=True)
        raise


def stop_scheduler():
    """Stop the background scheduler."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown()
        _scheduler = None
        logger.info("Background scheduler stopped")


def _cleanup_expired_reservations_task():
    """
    Task function for APScheduler to cleanup expired reservations.
    Runs in background thread, handles its own exceptions.
    """
    try:
        from .payment_service import cleanup_expired_reservations
        
        expired_count, cancelled_payment_count = cleanup_expired_reservations()
        
        if expired_count > 0 or cancelled_payment_count > 0:
            logger.info(
                f"Scheduler cleanup completed: {expired_count} expired reservations, "
                f"{cancelled_payment_count} cancelled payments"
            )
    except Exception as e:
        # Log but don't raise - scheduler should continue despite errors
        logger.error(
            f"Error in cleanup task: {str(e)}",
            exc_info=True
        )


def _process_email_queue_task():
    """
    Task function for APScheduler to process email queue.
    Runs in background thread, handles its own exceptions.
    """
    try:
        call_command('process_email_queue', limit=20, dry_run=False, verbosity=0)
    except Exception as e:
        # Log but don't raise - scheduler should continue despite errors
        logger.error(
            f"Error in email queue processing task: {str(e)}",
            exc_info=True
        )


# Django app ready signal handler
def on_app_ready():
    """
    Called when Django app is ready (via apps.py AppConfig.ready()).
    Initializes the background scheduler.
    """
    try:
        start_scheduler()
    except Exception as e:
        logger.error(f"Failed to initialize scheduler during app ready: {str(e)}", exc_info=True)
