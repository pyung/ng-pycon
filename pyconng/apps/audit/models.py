"""
One audit trail for the whole site.

Answers "who approved or rejected what, and when" in a single place, whatever
the subject is -- a proposal, a grant, a payment, a role grant, and refunds when
they arrive. A generic foreign key keeps it from needing a new table per module.

Entries are append-only by intent: nothing here updates or deletes them, and the
admin exposes them read-only. A trail you can quietly edit is not a trail.
"""

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class AuditEntry(models.Model):
    """A single recorded action against some object."""

    # --- Who ---------------------------------------------------------------
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_entries",
        help_text="The account that acted. Null for automated actions.",
    )
    actor_label = models.CharField(
        max_length=200,
        blank=True,
        help_text=(
            "How the actor was identified at the time -- an email address, or "
            '"system". Kept as written so the trail survives the account being '
            "renamed or deleted."
        ),
    )

    # --- What --------------------------------------------------------------
    action = models.CharField(
        max_length=120,
        help_text='Short description, e.g. "Approved grant" or "Rejected proposal".',
    )
    old_value = models.CharField(max_length=120, blank=True)
    new_value = models.CharField(max_length=120, blank=True)
    note = models.TextField(blank=True)

    # --- To what -----------------------------------------------------------
    target_type = models.ForeignKey(
        ContentType,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_entries",
    )
    target_id = models.CharField(
        max_length=64,
        blank=True,
        help_text="Primary key as text, because targets use integer and UUID keys.",
    )
    target = GenericForeignKey("target_type", "target_id")
    target_label = models.CharField(
        max_length=255,
        blank=True,
        help_text="How the target read at the time, so the trail stays legible if it is deleted.",
    )

    # --- When --------------------------------------------------------------
    conference_year = models.IntegerField(
        null=True,
        blank=True,
        help_text="Edition this action belongs to, where the target has one.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Audit entry"
        verbose_name_plural = "Audit trail"
        indexes = [
            models.Index(fields=["target_type", "target_id"]),
            models.Index(fields=["-created_at"]),
            models.Index(fields=["conference_year"]),
        ]

    def __str__(self):
        who = self.actor_label or (self.actor and self.actor.email) or "system"
        return f"{who}: {self.action} - {self.target_label or self.target_id}"

    @property
    def transition(self):
        """"draft to submitted", or empty when the action changed no status."""
        if self.old_value and self.new_value:
            return f"{self.old_value} to {self.new_value}"
        return self.new_value or ""
