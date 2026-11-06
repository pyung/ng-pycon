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

from .validators import HEX_COLOR, validate_hex_color


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
        max_length=7,
        default="#0E6B3D",
        validators=[validate_hex_color],
        help_text=(
            "Hex, e.g. #0E6B3D. Emitted as --theme-primary, so retuning the "
            "palette needs no deploy."
        ),
    )
    secondary_color = models.CharField(
        max_length=7,
        default="#3DDC84",
        validators=[validate_hex_color],
        help_text="Hex. Emitted as --theme-secondary. For dark grounds in the 2026 theme.",
    )
    accent_color = models.CharField(
        max_length=7,
        default="#8A4E06",
        validators=[validate_hex_color],
        help_text="Hex. Emitted as --theme-accent. Reserved for deadlines in the 2026 theme.",
    )

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

    #: Field name -> the CSS custom property it feeds.
    CSS_VARIABLE_MAP = (
        ("primary_color", "--theme-primary"),
        ("secondary_color", "--theme-secondary"),
        ("accent_color", "--theme-accent"),
    )

    def css_variables(self):
        """
        ``[(property, value)]`` for the palette, skipping anything unsafe.

        The point of this method is that the template never has to trust the
        database. The field validator stops a bad value being typed, but rows
        predate it and a data import bypasses it -- and these go into a
        ``<style>`` block, where Django's HTML autoescaping does not apply,
        because CSS is not an HTML context. A value containing ``}`` could close
        the rule and write its own; one containing ``</style>`` could leave CSS
        altogether. So the check is repeated at the moment of use: anything that
        is not a plain hex colour is left out and the stylesheet's own default
        stands, which is a dull page rather than an injected one.
        """
        out = []
        for field, prop in self.CSS_VARIABLE_MAP:
            value = (getattr(self, field, "") or "").strip()
            if HEX_COLOR.match(value):
                out.append((prop, value))
        return out

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
