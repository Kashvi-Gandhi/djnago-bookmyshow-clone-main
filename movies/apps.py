from django.apps import AppConfig


class MoviesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'movies'
    
    def ready(self):
        """
        Called when Django app is ready.
        Initializes the background scheduler for seat reservation cleanup.
        """
        try:
            from .scheduler import on_app_ready
            on_app_ready()
        except Exception as e:
            # Don't fail app startup if scheduler initialization fails
            # The app can still function, just without automatic cleanup
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Failed to initialize background scheduler: {str(e)}")
