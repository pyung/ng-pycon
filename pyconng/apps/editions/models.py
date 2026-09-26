"""
The conference editions.

Each year of PyCon Nigeria is a row here rather than a constant in code, so
opening 2027 is something an organizer does in the admin instead of something
that needs a developer and a deploy.

Records elsewhere still carry a plain ``conference_year`` integer. This model is
the authority on which years exist, which one is current, and how each looks.
"""

from django.core.exceptions import ValidationError
from django.db import models, transaction


class EditionQuerySet(models.QuerySet):
    def published(self):
        return self.filter(is_published=True)

    def archived(self):
        """Published editions that are not the current one."""
        return self.published().filter(is_current=False)


class Edition(models.Model):
    """One year of the conference."""

    year = models.IntegerField(
        unique=True,
        help_text="The edition year, e.g. 2027. Used to scope every record.",
    )
    name = models.CharField(
        max_length=100,
        help_text='Theme name shown on the site, e.g. "Future Forward".',
    )
    description = models.CharField(
        max_length=255,
        blank=True,
        help_text="One line describing this edition's design or focus.",
    )

    # --- Look and feel -----------------------------------------------------
    theme = models.SlugField(
        max_length=50,
        help_text=(
            "Theme slug. Selects the stylesheet (static/css/<theme>.css), the "
            "theme script and the base template. Free text, so a new theme "
            "needs no migration."
        ),
    )
    primary_color = models.CharField(
        max_length=7, default="#14b8a6", help_text="Hex, e.g. #14b8a6. Used for theme-color.",
    )
    secondary_color = models.CharField(max_length=7, default="#3b82f6", help_text="Hex.")
    accent_color = models.CharField(max_length=7, default="#f97316", help_text="Hex.")

    # --- Dates and place ---------------------------------------------------
    starts_on = models.DateField(null=True, blank=True)
    ends_on = models.DateField(null=True, blank=True)
    venue = models.CharField(max_length=200, blank=True)

    # --- State -------------------------------------------------------------
    is_current = models.BooleanField(
        default=False,
        help_text=(
            "The edition the site presents at its root URL. Exactly one "
            "edition is current; ticking this unticks the others."
        ),
    )
    is_published = models.BooleanField(
        default=True,
        help_text="Untick to keep an edition out of the year navigation while preparing it.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = EditionQuerySet.as_manager()

    class Meta:
        ordering = ["-year"]
        verbose_name = "Edition"
        verbose_name_plural = "Editions"

    def __str__(self):
        suffix = " (current)" if self.is_current else ""
        return f"PyCon Nigeria {self.year}{suffix}"

    def clean(self):
        if self.starts_on and self.ends_on and self.ends_on < self.starts_on:
            raise ValidationError({"ends_on": "The end date cannot be before the start date."})

    @transaction.atomic
    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Exactly one current edition: adopting the flag releases it elsewhere.
        if self.is_current:
            Edition.objects.exclude(pk=self.pk).filter(is_current=True).update(is_current=False)
        invalidate_edition_cache()

    def delete(self, *args, **kwargs):
        result = super().delete(*args, **kwargs)
        invalidate_edition_cache()
        return result

    # -- Template compatibility --------------------------------------------

    @property
    def colors(self):
        """
        Palette as a list, so templates can read ``colors.0`` -- the shape the
        old hard-coded CONFERENCE_YEARS dict exposed.
        """
        return [self.primary_color, self.secondary_color, self.accent_color]

    @property
    def dates_display(self):
        """Human-readable date range, or empty when dates are not set yet."""
        if not self.starts_on:
            return ""
        if not self.ends_on:
            return self.starts_on.strftime("%B %-d, %Y")
        if (self.starts_on.year, self.starts_on.month) == (self.ends_on.year, self.ends_on.month):
            return (
                f"{self.starts_on.strftime('%B %-d')}–{self.ends_on.strftime('%-d, %Y')}"
            )
        return (
            f"{self.starts_on.strftime('%B %-d')} – {self.ends_on.strftime('%B %-d, %Y')}"
        )


def invalidate_edition_cache():
    """Imported lazily to avoid a circular import at module load."""
    from .current import invalidate
    invalidate()
