"""
Sponsors, and what we owe them.

Sponsors used to live as StreamField blocks on the homepage, which was fine for
showing logos and useless for everything else: nothing recorded who the contact
was, whether the invoice had been paid, how many tickets they were owed, or
which promised benefits had actually been delivered.

Two models. Tiers are a small lookup so adding one needs no migration -- the
same reason editions stopped being a hard-coded dict. Sponsors are scoped to an
edition, because sponsorship is bought per year.

**A note on what is public.** Only ``name``, ``logo``, ``url`` and ``tier`` are
rendered publicly. Everything from ``contact_name`` down is pipeline and money,
including an email address -- it belongs in the admin and must never be put in a
template.
"""

from decimal import Decimal

from django.db import models

from wagtail import blocks
from wagtail.admin.panels import FieldPanel, MultiFieldPanel
from wagtail.fields import StreamField
from wagtail.images import get_image_model_string

from editions.current import current_year
from editions.validators import validate_edition_year


class SponsorTier(models.Model):
    """
    A sponsorship level -- Gold, Silver, Community Partner.

    A model rather than field choices so a new tier is an admin row, and so the
    public page can order tiers properly (Gold above Silver) rather than
    alphabetically.
    """

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional line shown under the tier heading.",
    )
    display_order = models.IntegerField(
        default=0,
        help_text="Lower numbers appear first. Gold before Silver, and so on.",
    )
    logo_max_width = models.IntegerField(
        default=200,
        help_text="Maximum logo width in pixels on the public page. Higher tiers get more room.",
    )
    is_published = models.BooleanField(
        default=True,
        help_text="Untick to hide this tier and its sponsors from public pages.",
    )

    class Meta:
        ordering = ["display_order", "name"]
        verbose_name = "Sponsor tier"
        verbose_name_plural = "Sponsor tiers"

    def __str__(self):
        return self.name


class SponsorQuerySet(models.QuerySet):
    def published(self):
        return self.filter(is_published=True, tier__is_published=True)

    def for_year(self, year):
        return self.filter(conference_year=year)

    def public(self, year=None):
        """What the website should show for an edition."""
        return (
            self.published()
            .for_year(year or current_year())
            .select_related("tier", "logo")
            .order_by("tier__display_order", "display_order", "name")
        )


DELIVERABLE_BLOCKS = [
    ("deliverable", blocks.StructBlock([
        ("item", blocks.CharBlock(max_length=200, help_text="e.g. Logo on the main stage banner")),
        ("done", blocks.BooleanBlock(required=False, default=False, help_text="Tick once delivered.")),
        ("note", blocks.CharBlock(max_length=200, required=False)),
    ], icon="tick", label="Deliverable")),
]


class Sponsor(models.Model):
    """One sponsor of one edition."""

    # --- Public -----------------------------------------------------------
    name = models.CharField(max_length=200)
    logo = models.ForeignKey(
        get_image_model_string(),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    logo_alt = models.CharField(
        max_length=200,
        blank=True,
        help_text="Alt text for the logo. Defaults to the sponsor's name.",
    )
    url = models.URLField(blank=True, help_text="Where the logo links to.")
    tier = models.ForeignKey(
        SponsorTier,
        on_delete=models.PROTECT,
        related_name="sponsors",
        help_text="Protected: a tier with sponsors attached cannot be deleted by accident.",
    )
    conference_year = models.IntegerField(
        default=current_year,
        validators=[validate_edition_year],
        help_text="The edition this sponsorship is for.",
    )
    display_order = models.IntegerField(
        default=0, help_text="Order within the tier.",
    )
    is_published = models.BooleanField(
        default=False,
        help_text="Off by default: a sponsor should not appear publicly before the deal is agreed.",
    )

    # --- Pipeline and money: admin only, never rendered publicly ----------
    contact_name = models.CharField(max_length=200, blank=True)
    contact_email = models.EmailField(blank=True)
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
        help_text="Agreed sponsorship amount.",
    )
    is_invoiced = models.BooleanField(default=False)
    is_paid = models.BooleanField(default=False)
    tickets_allocated = models.IntegerField(
        default=0, help_text="Complimentary tickets included in this package.",
    )
    tickets_claimed = models.IntegerField(
        default=0, help_text="How many of those have been taken up.",
    )
    deliverables = StreamField(
        DELIVERABLE_BLOCKS,
        blank=True,
        use_json_field=True,
        help_text="What was promised, and whether it has been delivered.",
    )
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = SponsorQuerySet.as_manager()

    class Meta:
        ordering = ["tier__display_order", "display_order", "name"]
        verbose_name = "Sponsor"
        verbose_name_plural = "Sponsors"
        constraints = [
            models.UniqueConstraint(
                fields=["name", "conference_year"],
                name="unique_sponsor_per_edition",
            ),
        ]
        indexes = [models.Index(fields=["conference_year", "is_published"])]

    panels = [
        MultiFieldPanel(
            [
                FieldPanel("name"),
                FieldPanel("tier"),
                FieldPanel("logo"),
                FieldPanel("logo_alt"),
                FieldPanel("url"),
            ],
            heading="Shown on the website",
        ),
        MultiFieldPanel(
            [
                FieldPanel("conference_year"),
                FieldPanel("display_order"),
                FieldPanel("is_published"),
            ],
            heading="Edition and visibility",
        ),
        MultiFieldPanel(
            [FieldPanel("contact_name"), FieldPanel("contact_email")],
            heading="Contact (internal)",
        ),
        MultiFieldPanel(
            [
                FieldPanel("amount"),
                FieldPanel("is_invoiced"),
                FieldPanel("is_paid"),
            ],
            heading="Money (internal)",
        ),
        MultiFieldPanel(
            [FieldPanel("tickets_allocated"), FieldPanel("tickets_claimed")],
            heading="Ticket allocation (internal)",
        ),
        FieldPanel("deliverables", heading="Benefits delivered (internal)"),
        FieldPanel("notes"),
    ]

    def __str__(self):
        return f"{self.name} - {self.tier.name} ({self.conference_year})"

    @property
    def alt_text(self):
        """Alt text for the logo, falling back to the sponsor's name."""
        return self.logo_alt or self.name

    # -- Deliverable tracking ---------------------------------------------

    @property
    def deliverable_total(self):
        return len(self.deliverables)

    @property
    def deliverables_done(self):
        return sum(1 for b in self.deliverables if b.value.get("done"))

    @property
    def deliverables_outstanding(self):
        return self.deliverable_total - self.deliverables_done

    @property
    def deliverables_summary(self):
        """"3/7 delivered", for the admin listing."""
        if not self.deliverable_total:
            return "—"
        return f"{self.deliverables_done}/{self.deliverable_total} delivered"

    @property
    def tickets_outstanding(self):
        return max(self.tickets_allocated - self.tickets_claimed, 0)

    @property
    def payment_state(self):
        """Where this sponsorship sits, for the admin listing."""
        if self.is_paid:
            return "Paid"
        if self.is_invoiced:
            return "Invoiced"
        return "Not invoiced"


def sponsors_by_tier(year=None):
    """
    Published sponsors for an edition as ``[(tier, [sponsor, ...]), ...]``,
    tiers in their configured order.

    Returns tier objects rather than bare names so a template can use the tier's
    description and logo width as well as its name.
    """
    grouped = []
    current_tier = None
    for sponsor in Sponsor.objects.public(year):
        if current_tier is None or sponsor.tier_id != current_tier.pk:
            current_tier = sponsor.tier
            grouped.append((current_tier, []))
        grouped[-1][1].append(sponsor)
    return grouped
