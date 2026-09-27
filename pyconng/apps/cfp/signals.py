"""Link pending co-speaker invitations once an account appears."""

import logging

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

logger = logging.getLogger(__name__)


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def link_pending_co_speaker_invites(sender, instance, created, **kwargs):
    """
    When someone signs up, attach any co-speaker invitations sent to their address.

    This is what makes "invite by email" safe: the submitter is never blocked
    waiting for a colleague to register, and nobody has to re-enter anything once
    that colleague does.
    """
    if not instance.email:
        return

    from .models import ProposalCoSpeaker

    try:
        pending = ProposalCoSpeaker.objects.filter(
            email__iexact=instance.email, user__isnull=True,
        )
        count = pending.update(user=instance, linked_at=timezone.now())
        if count:
            logger.info(
                "Linked %s co-speaker invitation(s) to %s", count, instance.email,
            )
    except Exception:  # noqa: BLE001 - signing up must never fail over this
        logger.exception("Failed linking co-speaker invites for %s", instance.email)
