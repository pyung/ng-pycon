"""
Admin-editable overrides for transactional email.

Every email has a version-controlled default on disk. A row here overrides one,
so organizers can reword any message without a deploy while a fresh install
still sends sensible mail. That ordering matters: the previous design made the
database row the *only* source for CFP decisions, so with no rows configured,
decisions were announced to nobody.

Bodies are plain text with ``{placeholder}`` markers and blank lines between
paragraphs. Deliberately not HTML: the wrapper supplies the branding, and an
organizer cannot break the markup or inject anything into a message that goes
out under the conference's name.
"""

from django.db import models

from editions.validators import validate_edition_year

from .rendering import safe_format


class EmailTemplate(models.Model):
    """An override for one email, optionally scoped to one edition."""

    key = models.CharField(
        max_length=100,
        help_text=(
            'Which email this replaces, e.g. "grants/waitlisted". See the '
            "placeholder list below for what you can use in the text."
        ),
    )
    conference_year = models.IntegerField(
        null=True,
        blank=True,
        validators=[validate_edition_year],
        help_text="Leave blank to use this wording for every edition.",
    )
    subject = models.CharField(
        max_length=200,
        help_text="Placeholders work here too, e.g. Accepted: {proposal_title}",
    )
    body = models.TextField(
        help_text=(
            "Plain text. Leave a blank line between paragraphs. Use "
            "{placeholder} markers -- anything unrecognised is left alone "
            "rather than breaking the email."
        ),
    )
    available_placeholders = models.CharField(
        max_length=500,
        blank=True,
        help_text="For your own reference: which placeholders this email provides.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Untick to fall back to the built-in wording without deleting this.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key", "-conference_year"]
        verbose_name = "Email template"
        verbose_name_plural = "Email templates"
        constraints = [
            models.UniqueConstraint(
                fields=["key", "conference_year"],
                name="unique_email_template_per_key_per_year",
            ),
        ]
        indexes = [models.Index(fields=["key", "is_active"])]

    def __str__(self):
        scope = self.conference_year or "all editions"
        return f"{self.key} ({scope})"

    @classmethod
    def lookup(cls, key, conference_year=None):
        """
        The override for ``key``, preferring one scoped to this edition over a
        general one. Returns None when there is none, so the caller falls back
        to the template on disk.
        """
        candidates = cls.objects.filter(key=key, is_active=True)
        if conference_year is not None:
            scoped = candidates.filter(conference_year=conference_year).first()
            if scoped:
                return scoped
        return candidates.filter(conference_year__isnull=True).first()

    def render(self, context):
        """Return ``(subject, body)`` with placeholders filled in."""
        return safe_format(self.subject, context), safe_format(self.body, context)
