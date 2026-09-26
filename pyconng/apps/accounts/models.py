"""Stored role grants. See :mod:`accounts.roles` for how roles are resolved."""

from django.conf import settings
from django.db import models

from .roles import Role, grantable_choices


class RoleAssignment(models.Model):
    """
    A role an organizer conferred on someone, for one edition or for all of them.

    Only grantable roles live here. Attendee, Speaker and Grant applicant are
    derived from tickets, proposals and applications respectively, so there is
    nothing to store and nothing to keep in sync.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="role_assignments",
    )
    role = models.CharField(max_length=32, choices=grantable_choices())
    conference_year = models.IntegerField(
        null=True,
        blank=True,
        help_text="Edition this role applies to. Leave blank for every edition.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Uncheck to revoke without losing the record of who granted it.",
    )
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="roles_granted",
        help_text="Who conferred this role.",
    )
    granted_at = models.DateTimeField(auto_now_add=True)
    note = models.TextField(
        blank=True,
        help_text="Why this role was granted, for whoever reads this later.",
    )

    class Meta:
        verbose_name = "Role assignment"
        verbose_name_plural = "Role assignments"
        ordering = ["-granted_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "role", "conference_year"],
                name="unique_role_per_user_per_year",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["role", "conference_year"]),
        ]

    def __str__(self):
        who = self.user.get_full_name() or self.user.email or self.user.username
        when = self.conference_year or "all editions"
        return f"{who} - {Role(self.role).label} ({when})"

    def save(self, *args, **kwargs):
        """Record grants and revocations, so who conferred what is answerable."""
        from audit.services import record

        previous = None
        if self.pk:
            previous = RoleAssignment.objects.filter(pk=self.pk).values("is_active").first()

        super().save(*args, **kwargs)

        was_active = previous["is_active"] if previous else None
        if was_active is None:
            action = "Granted role"
        elif was_active != self.is_active:
            action = "Restored role" if self.is_active else "Revoked role"
        else:
            return  # nothing about the grant itself changed

        record(
            target=self,
            action=action,
            actor=self.granted_by,
            old_value="" if was_active is None else ("active" if was_active else "revoked"),
            new_value="active" if self.is_active else "revoked",
            note=f"{Role(self.role).label} for {self.user.email or self.user.username}",
            conference_year=self.conference_year,
        )
