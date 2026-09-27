"""
The programme, and the record of past editions.

One ``Talk`` model rather than separate "archived talk" and "session" models.
A talk is a talk whether it happened in 2024 or is scheduled for 2027, so the
schedule builder should extend this rather than introduce a competing table --
otherwise "where do I edit this talk?" gets two answers and each year needs a
copy step into the archive.

``room`` and ``starts_at`` are here and deliberately unused for now: they are
what the schedule builder will fill in. Everything else already serves the
archive.

Speakers are recorded twice on purpose. ``speaker_names`` is free text, because
the people who spoke in 2024 and 2025 have no accounts here and never will.
``speakers`` links accounts where they exist, so a future speakers page can list
someone's talks across editions.
"""

from django.conf import settings
from django.db import models

from wagtail.admin.panels import FieldPanel, MultiFieldPanel
from wagtail.fields import RichTextField
from wagtail.images import get_image_model_string
from wagtail.models import Page

from editions.current import current_year
from editions.validators import validate_edition_year


class TalkQuerySet(models.QuerySet):
    def published(self):
        return self.filter(is_published=True)

    def for_year(self, year):
        return self.filter(conference_year=year)

    def with_video(self):
        return self.exclude(video_url="")


class Talk(models.Model):
    """One talk, given or scheduled."""

    conference_year = models.IntegerField(
        default=current_year,
        validators=[validate_edition_year],
        help_text="The edition this talk belongs to.",
    )
    title = models.CharField(max_length=250)
    abstract = models.TextField(
        blank=True,
        help_text="What the talk was about. Shown on the archive listing.",
    )

    speaker_names = models.CharField(
        max_length=300,
        blank=True,
        help_text=(
            "Speaker names as they should read, comma separated. Use this for "
            "past editions whose speakers have no account here."
        ),
    )
    speakers = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="talks",
        help_text="Link accounts where they exist, so a speaker's talks can be listed together.",
    )
    proposal = models.ForeignKey(
        "cfp.Proposal",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="talks",
        help_text="The proposal this came from, when it went through the CFP.",
    )

    track_name = models.CharField(max_length=120, blank=True)
    duration = models.IntegerField(
        null=True, blank=True, help_text="Minutes.",
    )

    video_url = models.URLField(
        blank=True, help_text="YouTube or other recording.",
    )
    slides_url = models.URLField(blank=True)
    repo_url = models.URLField(blank=True, help_text="Code or materials.")

    # --- Filled in by the schedule builder; unused by the archive ---
    room = models.CharField(
        max_length=120,
        blank=True,
        help_text="Left for the schedule builder. Not used by the archive.",
    )
    starts_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Left for the schedule builder. Not used by the archive.",
    )

    is_published = models.BooleanField(default=True)
    display_order = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TalkQuerySet.as_manager()

    class Meta:
        ordering = ["-conference_year", "display_order", "title"]
        verbose_name = "Talk"
        verbose_name_plural = "Talks"
        indexes = [models.Index(fields=["conference_year", "is_published"])]

    panels = [
        MultiFieldPanel(
            [
                FieldPanel("conference_year"),
                FieldPanel("title"),
                FieldPanel("abstract"),
            ],
            heading="The talk",
        ),
        MultiFieldPanel(
            [FieldPanel("speaker_names"), FieldPanel("speakers")],
            heading="Speakers",
        ),
        MultiFieldPanel(
            [
                FieldPanel("track_name"),
                FieldPanel("duration"),
                FieldPanel("proposal"),
            ],
            heading="Details",
        ),
        MultiFieldPanel(
            [
                FieldPanel("video_url"),
                FieldPanel("slides_url"),
                FieldPanel("repo_url"),
            ],
            heading="Recording and materials",
        ),
        MultiFieldPanel(
            [FieldPanel("is_published"), FieldPanel("display_order")],
            heading="Visibility",
        ),
    ]

    def __str__(self):
        return f"{self.title} ({self.conference_year})"

    @property
    def speaker_display(self):
        """
        Who gave this talk. Prefers linked accounts, falling back to the free-text
        names, so pre-system editions read correctly.
        """
        linked = [
            (u.get_full_name() or u.email or u.username)
            for u in self.speakers.all()
        ]
        if linked:
            return ", ".join(linked)
        return self.speaker_names

    @property
    def has_video(self):
        return bool(self.video_url)

    @property
    def has_materials(self):
        return bool(self.slides_url or self.repo_url)


class EditionPhotoQuerySet(models.QuerySet):
    def published(self):
        return self.filter(is_published=True)

    def for_year(self, year):
        return self.filter(conference_year=year)


class EditionPhoto(models.Model):
    """A photo from one edition, for the archive gallery."""

    conference_year = models.IntegerField(
        default=current_year,
        validators=[validate_edition_year],
    )
    image = models.ForeignKey(
        get_image_model_string(),
        on_delete=models.CASCADE,
        related_name="+",
    )
    alt = models.CharField(
        max_length=200,
        help_text="Describe the photo for screen readers. Required.",
    )
    caption = models.CharField(max_length=200, blank=True)
    display_order = models.IntegerField(default=0)
    is_published = models.BooleanField(default=True)

    objects = EditionPhotoQuerySet.as_manager()

    class Meta:
        ordering = ["-conference_year", "display_order", "pk"]
        verbose_name = "Edition photo"
        verbose_name_plural = "Edition photos"

    def __str__(self):
        return f"{self.conference_year}: {self.alt[:60]}"


class YearArchivePage(Page):
    """
    A past edition's landing page: what was talked about, and what it looked like.

    Served at ``/<year>/`` by ``pyconng.views.year_page_serve``, which prefers
    this over a full HomePage for an archived year.
    """

    conference_year = models.IntegerField(
        validators=[validate_edition_year],
        help_text="Which past edition this page archives.",
    )
    intro = RichTextField(
        blank=True,
        help_text="A short look back. Shown above the talks.",
    )
    hero_image = models.ForeignKey(
        get_image_model_string(),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    hero_image_alt = models.CharField(max_length=200, blank=True)

    talks_title = models.CharField(max_length=200, default="Talks")
    photos_title = models.CharField(max_length=200, default="Photos")
    show_talks = models.BooleanField(default=True)
    show_photos = models.BooleanField(default=True)

    parent_page_types = ["home.HomePage"]
    subpage_types = []

    content_panels = Page.content_panels + [
        FieldPanel("conference_year"),
        FieldPanel("intro"),
        MultiFieldPanel(
            [FieldPanel("hero_image"), FieldPanel("hero_image_alt")],
            heading="Hero image",
        ),
        MultiFieldPanel(
            [
                FieldPanel("show_talks"),
                FieldPanel("talks_title"),
                FieldPanel("show_photos"),
                FieldPanel("photos_title"),
            ],
            heading="Sections",
        ),
    ]

    class Meta:
        verbose_name = "Year Archive Page"

    def get_context(self, request, *args, **kwargs):
        from editions.current import edition_for_year

        context = super().get_context(request, *args, **kwargs)

        talks = (
            Talk.objects.published().for_year(self.conference_year)
            .prefetch_related("speakers")
            if self.show_talks else Talk.objects.none()
        )
        photos = (
            EditionPhoto.objects.published().for_year(self.conference_year)
            .select_related("image")
            if self.show_photos else EditionPhoto.objects.none()
        )

        context.update({
            "edition": edition_for_year(self.conference_year),
            "talks": talks,
            "photos": photos,
            "talk_count": talks.count(),
            "video_count": talks.with_video().count() if self.show_talks else 0,
            # Archived years render with their own edition's theme, not the current one.
            "page_theme": self._archive_theme(),
            "page_conference_year": self.conference_year,
        })
        return context

    def _archive_theme(self):
        """The theme that edition used, so an archive looks like the year it records."""
        from editions.current import edition_for_year

        edition = edition_for_year(self.conference_year)
        if edition and edition.theme:
            return f"theme_{edition.theme}"
        return "default"
