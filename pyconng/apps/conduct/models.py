"""
The Code of Conduct, and the reports made under it.

Two halves that have to stay apart. The Code of Conduct itself is a page anyone
can read and an organizer can reword without a deploy. A report made under it is
the most sensitive record this site holds: it may name an organizer, and the
person filing it may have nothing to gain and a great deal to lose.

So a report is never rendered on the public site, never carried in an email, and
never visible to the Organizer role. It is readable only by the Code of Conduct
team -- see wagtail_hooks.py -- and the person filing it need not have an account
or give a name.
"""

import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone
from modelcluster.fields import ParentalKey
from modelcluster.models import ClusterableModel
from wagtail.admin.panels import FieldPanel, InlinePanel, MultiFieldPanel
from wagtail.fields import RichTextField, StreamField
from wagtail.models import Orderable, Page
from wagtail import blocks

from editions.current import current_year
from editions.validators import validate_edition_year

#: One character from each pair people confuse when a reference is read aloud or
#: copied from a note: 0/O, 1/I/L, 5/S, 8/B, 2/Z, 6/G and 9/Q. 26 characters, so
#: five of them are nearly twelve million codes -- ample for one conference.
REFERENCE_ALPHABET = "ABCDEFGHJKMNPRSTUVWXY23479"


def make_reference(year=None, length=5):
    """
    A short code for one report, e.g. ``CoC-2026-H7KQ2``.

    Exists so an anonymous reporter has something to quote: with no account and
    no email there is otherwise no way for them to ask what happened, and "the
    thing I reported on Saturday" is not a usable handle for either side.
    """
    year = year or current_year()
    body = "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(length))
    return f"CoC-{year}-{body}"


class CodeOfConductContactBlock(blocks.StructBlock):
    """One person to reach during the event."""

    name = blocks.CharBlock(max_length=120)
    role = blocks.CharBlock(
        max_length=120,
        required=False,
        help_text="How they are introduced, e.g. 'Code of Conduct lead'.",
    )
    phone = blocks.CharBlock(
        max_length=40,
        required=False,
        help_text="Shown as a tel: link, so it is one tap on a phone.",
    )
    email = blocks.EmailBlock(required=False)

    class Meta:
        icon = "user"
        label = "Contact"


class CodeOfConductPage(Page):
    """
    The Code of Conduct, and the way in to reporting a breach.

    The text is a StreamField rather than one rich-text lump so a section can be
    reordered or reworded on its own, and so the reporting call to action sits in
    a fixed place the layout controls rather than wherever someone pasted it.
    """

    intro = RichTextField(
        blank=True,
        help_text="A short statement of intent, shown under the title.",
    )
    body = StreamField(
        [
            ("heading", blocks.CharBlock(max_length=200, form_classname="title")),
            ("paragraph", blocks.RichTextBlock()),
            (
                "expectations",
                blocks.ListBlock(
                    blocks.CharBlock(max_length=300),
                    label="Expected behaviour",
                    help_text="One expectation per line.",
                ),
            ),
            (
                "unacceptable",
                blocks.ListBlock(
                    blocks.CharBlock(max_length=300),
                    label="Unacceptable behaviour",
                    help_text="One item per line.",
                ),
            ),
            (
                "callout",
                blocks.StructBlock(
                    [
                        ("title", blocks.CharBlock(max_length=200, required=False)),
                        ("text", blocks.RichTextBlock()),
                    ],
                    label="Callout",
                ),
            ),
        ],
        blank=True,
        use_json_field=True,
    )

    show_report_form = models.BooleanField(
        default=True,
        help_text=(
            "Offer the reporting form from this page. Turning this off hides the "
            "link but does not close the form at /conduct/report/."
        ),
    )
    report_heading = models.CharField(
        max_length=200,
        default="Reporting a breach",
        help_text="Heading above the reporting call to action.",
    )
    report_intro = RichTextField(
        blank=True,
        help_text="What to expect if they report. Say who reads it and how soon.",
    )
    response_promise = models.CharField(
        max_length=300,
        blank=True,
        help_text=(
            "The commitment shown beside the form, e.g. 'A member of the Code of "
            "Conduct team will respond within 24 hours.' Leave blank to say nothing."
        ),
    )
    emergency_contacts = StreamField(
        [("contact", CodeOfConductContactBlock())],
        blank=True,
        use_json_field=True,
        help_text=(
            "People to reach during the event. Editable here so a phone number can "
            "be corrected on the day without a deploy."
        ),
    )

    parent_page_types = ["home.HomePage"]
    subpage_types = []

    content_panels = Page.content_panels + [
        FieldPanel("intro"),
        FieldPanel("body"),
        MultiFieldPanel(
            [
                FieldPanel("show_report_form"),
                FieldPanel("report_heading"),
                FieldPanel("report_intro"),
                FieldPanel("response_promise"),
            ],
            heading="Reporting",
        ),
        FieldPanel("emergency_contacts"),
    ]

    class Meta:
        verbose_name = "Code of Conduct page"

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        context["report_url"] = "/conduct/report/"
        return context


class IncidentReport(ClusterableModel):
    """
    One report made under the Code of Conduct.

    Every field beyond the description is optional on purpose. A reporter under
    stress should not be stopped by a form, and demanding a name or an email from
    someone reporting an organizer is a good way to be told nothing at all.
    """

    STATUS_NEW = "new"
    STATUS_ACKNOWLEDGED = "acknowledged"
    STATUS_INVESTIGATING = "investigating"
    STATUS_RESOLVED = "resolved"
    STATUS_NO_ACTION = "no_action"
    STATUS_CHOICES = [
        (STATUS_NEW, "New"),
        (STATUS_ACKNOWLEDGED, "Acknowledged"),
        (STATUS_INVESTIGATING, "Investigating"),
        (STATUS_RESOLVED, "Resolved"),
        (STATUS_NO_ACTION, "Closed, no action"),
    ]

    #: Statuses that mean nobody needs to look at this again.
    CLOSED_STATUSES = frozenset({STATUS_RESOLVED, STATUS_NO_ACTION})

    reference = models.CharField(
        max_length=32,
        unique=True,
        editable=False,
        help_text="Quoted by the reporter and by the team. Generated, never typed.",
    )
    conference_year = models.IntegerField(
        validators=[validate_edition_year],
        help_text="Which edition this report belongs to.",
    )
    submitted_at = models.DateTimeField(auto_now_add=True)

    # --- Who, if they chose to say ---
    is_anonymous = models.BooleanField(
        default=False,
        help_text="Set by the reporter. When true, no name or email was collected.",
    )
    reporter_name = models.CharField(max_length=200, blank=True)
    reporter_email = models.EmailField(blank=True)
    reporter_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="conduct_reports",
        help_text=(
            "Recorded only when the reporter was signed in and did not ask to be "
            "anonymous. An anonymous report is never linked to an account."
        ),
    )

    # --- What happened ---
    incident_when = models.CharField(
        max_length=200,
        blank=True,
        help_text="In the reporter's words, e.g. 'Saturday, during the lightning talks'.",
    )
    incident_where = models.CharField(
        max_length=200,
        blank=True,
        help_text="Room, venue, or the online channel.",
    )
    people_involved = models.TextField(
        blank=True,
        help_text="Names or descriptions, as given. May be empty.",
    )
    description = models.TextField(help_text="What happened, in the reporter's words.")
    witnesses = models.TextField(blank=True)
    desired_outcome = models.TextField(
        blank=True,
        help_text="What the reporter asked for. Read this before deciding anything.",
    )
    reported_elsewhere = models.BooleanField(
        default=False,
        help_text="The reporter says they have also told someone else, on-site or off.",
    )

    # --- What the team did ---
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_NEW)
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="conduct_reports_handled",
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True, editable=False)
    closed_at = models.DateTimeField(null=True, blank=True, editable=False)
    resolution_note = models.TextField(
        blank=True,
        help_text="What was decided and done. Internal.",
    )

    class Meta:
        ordering = ["-submitted_at"]
        verbose_name = "Code of Conduct report"
        verbose_name_plural = "Code of Conduct reports"
        indexes = [
            models.Index(fields=["conference_year", "status"]),
        ]

    def __str__(self):
        return f"{self.reference} ({self.get_status_display()})"

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = self._unique_reference()
        if self.is_anonymous:
            # Enforced here rather than only in the form: an anonymous report must
            # stay anonymous however it was created, including from the admin.
            self.reporter_name = ""
            self.reporter_email = ""
            self.reporter_user = None
        self._stamp_status()
        super().save(*args, **kwargs)

    def _unique_reference(self):
        for _ in range(10):
            candidate = make_reference(self.conference_year)
            if not IncidentReport.objects.filter(reference=candidate).exists():
                return candidate
        # 27^5 is 14 million; ten collisions means something is very wrong, and a
        # report must still be saved. Fall back to a longer code.
        return make_reference(self.conference_year, length=10)

    def _stamp_status(self):
        now = timezone.now()
        if self.status != self.STATUS_NEW and self.acknowledged_at is None:
            self.acknowledged_at = now
        if self.status in self.CLOSED_STATUSES:
            if self.closed_at is None:
                self.closed_at = now
        else:
            self.closed_at = None

    @property
    def is_open(self):
        return self.status not in self.CLOSED_STATUSES

    @property
    def reporter_label(self):
        """How to refer to the reporter without leaking what they withheld."""
        if self.is_anonymous:
            return "Anonymous"
        return self.reporter_name or self.reporter_email or "Not given"

    @property
    def can_reply(self):
        return bool(self.reporter_email)

    def summary_line(self):
        """
        A one-line description safe to put in a notification.

        Deliberately excludes the description: a notification email lands in an
        inbox, is backed up, and is read on a phone on a train. The details belong
        behind a login.
        """
        parts = [self.reference]
        if self.incident_when:
            parts.append(self.incident_when)
        if self.incident_where:
            parts.append(self.incident_where)
        return " — ".join(parts)


class IncidentReportNote(Orderable):
    """
    A dated note the team adds while handling a report.

    Separate rows rather than one growing text field so each entry keeps its own
    author and time, which is what makes the record usable months later.
    """

    report = ParentalKey(IncidentReport, related_name="notes", on_delete=models.CASCADE)
    written_at = models.DateTimeField(auto_now_add=True)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    author_label = models.CharField(
        max_length=200,
        blank=True,
        help_text="Kept as written, so the note stays legible after an account is renamed.",
    )
    note = models.TextField()

    panels = [FieldPanel("note")]

    class Meta(Orderable.Meta):
        verbose_name = "note"

    def __str__(self):
        return f"{self.written_at:%Y-%m-%d}: {self.note[:60]}"
