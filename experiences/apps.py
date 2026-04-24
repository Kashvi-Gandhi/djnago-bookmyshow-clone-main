from django.apps import AppConfig


class ExperiencesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'experiences'

    def ready(self):
        try:
            from .scheduler import on_app_ready

            on_app_ready()
        except Exception:
            import logging

            logging.getLogger(__name__).warning("Failed to init experiences scheduler", exc_info=True)
