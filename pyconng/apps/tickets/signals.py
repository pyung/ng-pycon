"""Attach places named by email once their owner has an account."""

import logging

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def link_unclaimed_ticket_places(sender, instance, created, **kwargs):
    """
    When someone signs up, attach any ticket bought for their address.

    This is the other half of fixing group purchases. A buyer can name five
    colleagues by email before any of them has an account; without this, the place
    stays unclaimed for ever and the colleague sees no ticket when they do sign up.

    Only unclaimed places are touched, so a transfer is never undone by somebody
    changing their email back.
    """
    if not instance.email:
        return

    from .models import TicketSale

    try:
        count = TicketSale.objects.filter(
            attendee_email__iexact=instance.email, user__isnull=True
        ).update(user=instance)
        if count:
            logger.info("Linked %s ticket place(s) to %s", count, instance.email)
    except Exception:  # noqa: BLE001 - signing up must never fail over this
        logger.exception("Failed linking ticket places for %s", instance.email)
