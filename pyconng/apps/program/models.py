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


class Room(models.Model):
    """
    A room talks are scheduled into.

    A model rather than free text on Talk, because a schedule grid needs rooms
    in a stable left-to-right order and needs to know how many people fit.
    Scoped per edition: the venue, and therefore the rooms, change each year.
    """

    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=120)
    conference_year = models.IntegerField(
        default=current_year,
        validators=[validate_edition_year],
    )
    capacity = models.IntegerField(
        null=True, blank=True, help_text="Seats. Used to cap workshop sign-ups later.",
    )
    display_order = models.IntegerField(
        default=0, help_text="Left-to-right order in the schedule grid.",
    )
    is_published = models.BooleanField(default=True)

    class Meta:
        ordering = ["conference_year", "display_order", "name"]
        verbose_name = "Room"
        verbose_name_plural = "Rooms"
        constraints = [
            models.UniqueConstraint(
                fields=["slug", "conference_year"], name="unique_room_slug_per_edition",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.conference_year})"


class TalkQuerySet(models.QuerySet):
    def published(self):
        return self.filter(is_published=True)

    def for_year(self, year):
        return self.filter(conference_year=year)

    def with_video(self):
        return self.exclude(video_url="")

    def scheduled(self):
        """Talks placed in the grid: they have a time and a room."""
        return self.filter(starts_at__isnull=False, room__isnull=False)

    def unscheduled(self):
        """Accepted talks still waiting for a slot."""
        return self.filter(models.Q(starts_at__isnull=True) | models.Q(room__isnull=True))


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
    room = models.ForeignKey(
        Room,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="talks",
        help_text="Leave empty for an archived talk, or one not yet scheduled.",
    )
    starts_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When it starts. Empty means not yet scheduled.",
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
    def is_scheduled(self):
        return bool(self.starts_at and self.room_id)

    @property
    def effective_duration(self):
        """Minutes. Falls back to 30 so an unset duration still lays out."""
        return self.duration or 30

    @property
    def ends_at(self):
        import datetime

        if not self.starts_at:
            return None
        return self.starts_at + datetime.timedelta(minutes=self.effective_duration)

    @property
    def day(self):
        """The local date this talk runs on, for grouping the grid by day."""
        from django.utils import timezone

        if not self.starts_at:
            return None
        return timezone.localtime(self.starts_at).date()

    @property
    def has_video(self):
        return bool(self.video_url)

    @property
    def has_materials(self):
        return bool(self.slides_url or self.repo_url)


class SavedTalk(models.Model):
    """
    A talk someone added to their personal schedule.

    Login-required by design: the requirement asks for a personal schedule for
    signed-in users, and a server-side record is what lets it follow them from
    phone to laptop on the day.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="saved_talks",
    )
    talk = models.ForeignKey(
        "program.Talk",
        on_delete=models.CASCADE,
        related_name="saved_by",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["talk__starts_at", "talk__title"]
        verbose_name = "Saved talk"
        verbose_name_plural = "Saved talks"
        constraints = [
            models.UniqueConstraint(fields=["user", "talk"], name="unique_saved_talk_per_user"),
        ]
        indexes = [models.Index(fields=["user"])]

    def __str__(self):
        return f"{self.user} saved {self.talk}"


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


# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------

class ParentThemedPage(Page):
    """
    Base for programme pages that should look like the edition they belong to,
    inheriting the theme from the HomePage above them.
    """

    class Meta:
        abstract = True

    def parent_homepage(self):
        from home.models import HomePage

        parent = self.get_parent()
        while parent:
            specific = parent.specific
            if isinstance(specific, HomePage):
                return specific
            parent = parent.get_parent()
        return None

    def themed(self, context):
        homepage = self.parent_homepage()
        if homepage:
            context["parent_homepage"] = homepage
            context["page_theme"] = homepage.theme
            context["page_conference_year"] = homepage.conference_year
        else:
            context["page_theme"] = "default"
            context["page_conference_year"] = None
        return context

    def resolved_year(self):
        """The edition this page covers: its own field, else the parent's, else current."""
        from editions.current import current_year

        own = getattr(self, "conference_year", None)
        if own:
            return own
        homepage = self.parent_homepage()
        if homepage and homepage.conference_year:
            return homepage.conference_year
        return current_year()


class SchedulePage(ParentThemedPage):
    """
    The published schedule, generated from accepted CFP data.

    Signed-in visitors can save talks and view only those, which is the personal
    "My schedule" the requirement asks for.
    """

    conference_year = models.IntegerField(
        null=True,
        blank=True,
        validators=[validate_edition_year],
        help_text="Leave blank to follow the edition of the page above this one.",
    )
    intro = RichTextField(blank=True)
    empty_message = models.CharField(
        max_length=255,
        default="The schedule is not published yet. Check back soon.",
        help_text="Shown when nothing has been scheduled.",
    )
    allow_saving = models.BooleanField(
        default=True,
        help_text="Let signed-in visitors build a personal schedule.",
    )

    parent_page_types = ["home.HomePage"]
    subpage_types = []

    content_panels = Page.content_panels + [
        FieldPanel("conference_year"),
        FieldPanel("intro"),
        FieldPanel("empty_message"),
        FieldPanel("allow_saving"),
    ]

    class Meta:
        verbose_name = "Schedule Page"

    def get_context(self, request, *args, **kwargs):
        from .services import schedule_grid

        context = super().get_context(request, *args, **kwargs)
        self.themed(context)
        year = self.resolved_year()

        mine = bool(request and request.GET.get("mine")) and (
            request.user.is_authenticated if request else False
        )

        saved_ids = set()
        if request and request.user.is_authenticated:
            saved_ids = set(
                SavedTalk.objects.filter(user=request.user, talk__conference_year=year)
                .values_list("talk_id", flat=True)
            )

        rooms, grid = schedule_grid(year, only_talk_ids=saved_ids if mine else None)

        context.update({
            "schedule_year": year,
            "rooms": rooms,
            "schedule_days": grid,
            "saved_ids": saved_ids,
            "saved_count": len(saved_ids),
            "showing_mine": mine,
            "can_save": self.allow_saving,
        })
        return context


class SpeakersPage(ParentThemedPage):
    """Everyone speaking at an edition, generated from the programme."""

    conference_year = models.IntegerField(
        null=True,
        blank=True,
        validators=[validate_edition_year],
        help_text="Leave blank to follow the edition of the page above this one.",
    )
    intro = RichTextField(blank=True)
    empty_message = models.CharField(
        max_length=255,
        default="Speakers will be announced once talks are selected.",
    )

    parent_page_types = ["home.HomePage"]
    subpage_types = []

    content_panels = Page.content_panels + [
        FieldPanel("conference_year"),
        FieldPanel("intro"),
        FieldPanel("empty_message"),
    ]

    class Meta:
        verbose_name = "Speakers Page"

    def get_context(self, request, *args, **kwargs):
        from .services import speakers_for

        context = super().get_context(request, *args, **kwargs)
        self.themed(context)
        year = self.resolved_year()
        speakers = speakers_for(year)
        context.update({
            "speakers_year": year,
            "speakers": speakers,
            "speaker_count": len(speakers),
        })
        return context
