from django.apps import AppConfig


class EditionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "editions"
    verbose_name = "Conference Editions"

    def ready(self):
        from . import signals  # noqa: F401
