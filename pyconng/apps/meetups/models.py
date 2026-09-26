"""
Meetups: the road to the conference.

RSVPs stay on Meetup.com -- this module's job is to tie the city meetups to the
conference journey, so a visitor can see what is coming up, find their city's
chapter, and read a recap afterwards.

Two shapes, chosen on purpose:

* **Pages** for the copy people write -- the index and one chapter page per city.
* **Records** for the things people log -- a Meetup and the talks given at it,
  created from one short admin form rather than the page editor.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

from modelcluster.fields import ParentalKey
from modelcluster.models import ClusterableModel

from wagtail import blocks
from wagtail.admin.panels import FieldPanel, InlinePanel, MultiFieldPanel
from wagtail.fields import RichTextField, StreamField
from wagtail.images.blocks import ImageChooserBlock
from wagtail.models import Orderable, Page

from editions.current import current_year
from editions.validators import validate_edition_year


# ---------------------------------------------------------------------------
# Shared blocks
# ---------------------------------------------------------------------------

class OrganizerBlock(blocks.StructBlock):
    """A chapter organizer."""

    name = blocks.CharBlock(max_length=120)
    role = blocks.CharBlock(max_length=120, required=False, help_text="e.g. Lead organizer")
    photo = ImageChooserBlock(required=False)
    photo_alt = blocks.CharBlock(
        max_length=200, required=False,
        help_text="Describe the photo for screen readers.",
    )
    link = blocks.URLBlock(required=False, help_text="Their site, LinkedIn or GitHub")

    class Meta:
        icon = "user"
        label = "Organizer"


RECAP_BLOCKS = [
    ("paragraph", blocks.RichTextBlock()),
    ("photo", blocks.StructBlock([
        ("image", ImageChooserBlock()),
        ("alt", blocks.CharBlock(max_length=200, required=False,
                                 help_text="Describe the photo for screen readers.")),
        ("caption", blocks.CharBlock(max_length=200, required=False)),
    ], icon="image", label="Photo")),
    ("embed", blocks.URLBlock(
        label="Recording link",
        help_text="YouTube or other recording URL",
    )),
]


# ---------------------------------------------------------------------------
# Theme inheritance
# ---------------------------------------------------------------------------

class ThemedPageMixin:
    """
    Inherit the visual theme from the nearest HomePage above this page, the same
    way StandardPage and SponsorPage do.
    """

    def get_parent_homepage(self):
        from home.models import HomePage

        parent = self.get_parent()
        while parent:
            specific = parent.specific
            if isinstance(specific, HomePage):
                return specific
            parent = parent.get_parent()
        return None

    def themed_context(self, context):
        homepage = self.get_parent_homepage()
        if homepage:
            context["parent_homepage"] = homepage
            context["page_theme"] = homepage.theme
            context["page_conference_year"] = homepage.conference_year
        else:
            context["page_theme"] = "default"
            context["page_conference_year"] = None
        return context


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

class MeetupIndexPage(ThemedPageMixin, Page):
    """Landing page listing upcoming and past meetups across every city."""

    intro = RichTextField(
        blank=True,
        help_text="Shown above the meetup list.",
    )

    # Conversion hooks: turn a meetup visitor into a conference attendee.
    show_newsletter_signup = models.BooleanField(
        default=True,
        help_text="Show the mailing-list form on this page and its chapter pages.",
    )
    newsletter_heading = models.CharField(
        max_length=150,
        blank=True,
        default="Get early-bird access",
        help_text="Heading above the mailing-list form.",
    )
    newsletter_blurb = models.CharField(
        max_length=255,
        blank=True,
        default="Join the mailing list for conference announcements and early-bird tickets.",
    )

    parent_page_types = ["home.HomePage"]
    subpage_types = ["meetups.CityChapterPage"]
    max_count_per_parent = 1

    content_panels = Page.content_panels + [
        FieldPanel("intro"),
        MultiFieldPanel(
            [
                FieldPanel("show_newsletter_signup"),
                FieldPanel("newsletter_heading"),
                FieldPanel("newsletter_blurb"),
            ],
            heading="Mailing-list call to action",
        ),
    ]

    class Meta:
        verbose_name = "Meetup Index Page"

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        self.themed_context(context)

        chapters = (
            CityChapterPage.objects.child_of(self).live().order_by("title")
        )
        meetups = Meetup.objects.published().select_related("chapter").prefetch_related("talks")

        # Optional ?city=<slug> filter, so a chapter can be linked directly.
        city = request.GET.get("city") if request else None
        if city:
            meetups = meetups.filter(chapter__slug=city)

        context.update({
            "chapters": chapters,
            "selected_city": city,
            "upcoming_meetups": meetups.upcoming(),
            "past_meetups": meetups.past(),
            "index_page": self,
        })
        return context


class CityChapterPage(ThemedPageMixin, Page):
    """A city's chapter: who runs it, and what it has held."""

    city = models.CharField(
        max_length=100,
        help_text="City name, e.g. Lagos. Used in listings.",
    )
    intro = RichTextField(blank=True)
    hero_image = models.ForeignKey(
        "wagtailimages.Image",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    hero_image_alt = models.CharField(
        max_length=200,
        blank=True,
        help_text="Describe the image for screen readers.",
    )
    meetup_com_url = models.URLField(
        blank=True,
        help_text="The chapter's Meetup.com group page.",
    )
    organizers = StreamField(
        [("organizer", OrganizerBlock())],
        blank=True,
        use_json_field=True,
    )

    parent_page_types = ["meetups.MeetupIndexPage"]
    subpage_types = []

    content_panels = Page.content_panels + [
        FieldPanel("city"),
        FieldPanel("intro"),
        MultiFieldPanel(
            [FieldPanel("hero_image"), FieldPanel("hero_image_alt")],
            heading="Hero image",
        ),
        FieldPanel("meetup_com_url"),
        FieldPanel("organizers"),
    ]

    class Meta:
        verbose_name = "City Chapter Page"

    def save(self, *args, **kwargs):
        if not self.city:
            self.city = self.title
        super().save(*args, **kwargs)

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        self.themed_context(context)

        meetups = self.meetups.published().prefetch_related("talks")
        index_page = self.get_parent().specific
        context.update({
            "upcoming_meetups": meetups.upcoming(),
            "past_meetups": meetups.past(),
            "index_page": index_page if isinstance(index_page, MeetupIndexPage) else None,
        })
        return context


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

class MeetupQuerySet(models.QuerySet):
    def published(self):
        return self.filter(is_published=True)

    def upcoming(self):
        return self.filter(starts_at__gte=timezone.now()).order_by("starts_at")

    def past(self):
        return self.filter(starts_at__lt=timezone.now()).order_by("-starts_at")

    def for_year(self, year):
        return self.filter(conference_year=year)


class Meetup(ClusterableModel):
    """
    One meetup event. RSVPs live on Meetup.com, so ``meetup_url`` is the call to
    action rather than anything we host.
    """

    chapter = models.ForeignKey(
        CityChapterPage,
        on_delete=models.CASCADE,
        related_name="meetups",
        help_text="Which city chapter is holding this.",
    )
    title = models.CharField(
        max_length=200,
        help_text='e.g. "Lagos Python Meetup - October"',
    )
    starts_at = models.DateTimeField(help_text="Local date and time it starts.")
    ends_at = models.DateTimeField(null=True, blank=True)
    venue = models.CharField(max_length=200, blank=True)
    address = models.CharField(max_length=300, blank=True)
    summary = models.TextField(
        blank=True,
        help_text="A couple of lines for the listing.",
    )
    meetup_url = models.URLField(
        help_text="The Meetup.com event page where people RSVP.",
    )
    conference_year = models.IntegerField(
        default=current_year,
        validators=[validate_edition_year],
        help_text="The edition this meetup leads up to.",
    )
    is_published = models.BooleanField(
        default=True,
        help_text="Untick to hide from the public listing while preparing it.",
    )

    # Recap, added after the event (requirement 5).
    recap = StreamField(
        RECAP_BLOCKS,
        blank=True,
        use_json_field=True,
        help_text="Photos, notes and recording links, added after the meetup.",
    )

    # Conversion hook (requirement 4).
    attendee_coupon = models.ForeignKey(
        "tickets.Coupon",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="meetups",
        help_text="Optional discount code to offer this meetup's attendees.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = MeetupQuerySet.as_manager()

    panels = [
        MultiFieldPanel(
            [
                FieldPanel("chapter"),
                FieldPanel("title"),
                FieldPanel("meetup_url"),
            ],
            heading="What and where to RSVP",
        ),
        MultiFieldPanel(
            [
                FieldPanel("starts_at"),
                FieldPanel("ends_at"),
                FieldPanel("venue"),
                FieldPanel("address"),
            ],
            heading="When and where",
        ),
        FieldPanel("summary"),
        InlinePanel("talks", label="Speaker", heading="Speakers and talks"),
        MultiFieldPanel(
            [FieldPanel("conference_year"), FieldPanel("is_published")],
            heading="Edition and visibility",
        ),
        FieldPanel("recap", heading="Recap (add after the meetup)"),
        FieldPanel("attendee_coupon"),
    ]

    class Meta:
        ordering = ["-starts_at"]
        verbose_name = "Meetup"
        verbose_name_plural = "Meetups"
        indexes = [
            models.Index(fields=["starts_at"]),
            models.Index(fields=["conference_year", "is_published"]),
        ]

    def __str__(self):
        return f"{self.title} ({self.starts_at:%d %b %Y})"

    @property
    def city(self):
        return self.chapter.city if self.chapter_id else ""

    @property
    def is_upcoming(self):
        return self.starts_at >= timezone.now()

    @property
    def has_recap(self):
        return bool(self.recap)

    def generate_attendee_coupon(self, percentage=10, max_usage=100):
        """
        Create and attach a discount code for this meetup's attendees.

        The code reads back to the meetup that earned it, e.g. LAGOS-OCT26.
        Does nothing if one is already attached.
        """
        from tickets.models import Coupon

        if self.attendee_coupon_id:
            return self.attendee_coupon

        base = f"{self.city[:8].upper().replace(' ', '')}-{self.starts_at:%b%y}".upper()
        code = base
        suffix = 2
        while Coupon.objects.filter(code=code).exists():
            code = f"{base}-{suffix}"
            suffix += 1

        coupon = Coupon.objects.create(
            code=code,
            percentage=percentage,
            max_usage=max_usage,
            conference_year=self.conference_year,
        )
        self.attendee_coupon = coupon
        self.save(update_fields=["attendee_coupon", "updated_at"])
        return coupon


class MeetupTalk(Orderable):
    """
    A talk given at a meetup.

    ``user`` is optional because not every meetup speaker has an account, but
    promoting a talk into the conference CFP needs one -- proposals hang off an
    account, so there is nothing to attach a proposal to without it.
    """

    meetup = ParentalKey(
        Meetup,
        on_delete=models.CASCADE,
        related_name="talks",
    )
    speaker_name = models.CharField(max_length=200)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="meetup_talks",
        help_text="Link to their account if they have one. Required to promote this into the CFP.",
    )
    title = models.CharField(max_length=200)
    abstract = models.TextField(
        blank=True,
        help_text="Used to pre-fill a conference proposal if this talk is promoted.",
    )
    slides_url = models.URLField(blank=True)
    recording_url = models.URLField(blank=True)

    # Promotion into the conference CFP (requirement 3).
    promoted_proposal = models.ForeignKey(
        "cfp.Proposal",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="promoted_from_meetup_talks",
        help_text="The conference proposal created from this talk.",
    )
    promoted_at = models.DateTimeField(null=True, blank=True)
    promoted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="meetup_talks_promoted",
    )

    class Meta(Orderable.Meta):
        verbose_name = "Meetup talk"
        verbose_name_plural = "Meetup talks"

    def __str__(self):
        return f"{self.title} - {self.speaker_name}"

    @property
    def can_promote(self):
        """Promotable when it has an account to attach a proposal to, and has not been already."""
        return self.user_id is not None and self.promoted_proposal_id is None
