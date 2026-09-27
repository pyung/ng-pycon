from django.apps import AppConfig


class CfpConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "cfp"
    verbose_name = "Call for Proposals"

    def ready(self):
        from . import signals  # noqa: F401
