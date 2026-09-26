"""Keep the cached current year honest when editions change."""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .current import invalidate
from .models import Edition


@receiver(post_save, sender=Edition)
@receiver(post_delete, sender=Edition)
def _invalidate_edition_cache(sender, **kwargs):
    invalidate()
