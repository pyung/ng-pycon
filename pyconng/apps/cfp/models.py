import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils import timezone

from wagtail.fields import RichTextField
from editions.validators import validate_edition_year


# ---------------------------------------------------------------------------
# CFP Configuration
# ---------------------------------------------------------------------------

class CFPSettings(models.Model):
    """
    CFP configuration – one record per conference year.
    Managed via Wagtail admin (singleton-per-year).
    """

    STATUS_DRAFT = "draft"
    STATUS_OPEN = "open"
    STATUS_CLOSED = "closed"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_OPEN, "Open"),
        (STATUS_CLOSED, "Closed"),
    ]

    conference_year = models.IntegerField(unique=True, validators=[validate_edition_year])
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT,
    )
    submission_deadline = models.DateTimeField(
        help_text="CFP will automatically close after this deadline",
    )
    allowed_durations = models.JSONField(
        default=list,
        blank=True,
        help_text='Allowed talk durations in minutes, e.g. [5, 20, 45, 90]',
    )
    guidelines = RichTextField(
        blank=True,
        help_text="CFP guidelines shown on the landing page",
    )
    anonymise_review = models.BooleanField(
        default=True,
        help_text=(
            "Hide speaker names, organisations, countries and bios from reviewers, "
            "so proposals are judged on their content. Chairs always see identities."
        ),
    )
    confirmation_deadline = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Accepted speakers must confirm by this date. Run "
            "'manage.py expire_unconfirmed_talks' afterwards to release the slots."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "CFP Settings"
        verbose_name_plural = "CFP Settings"

    def __str__(self):
        return f"CFP {self.conference_year} ({self.get_status_display()})"

    @property
    def is_open(self):
        """True when status is Open **and** the deadline has not passed."""
        if self.status != self.STATUS_OPEN:
            return False
        return timezone.now() < self.submission_deadline

    @property
    def is_past_deadline(self):
        return timezone.now() >= self.submission_deadline

    def save(self, *args, **kwargs):
        # Provide sensible defaults for durations
        if not self.allowed_durations:
            self.allowed_durations = [5, 20, 45, 90]
        # Auto-close when past deadline
        if self.status == self.STATUS_OPEN and self.is_past_deadline:
            self.status = self.STATUS_CLOSED
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# Tracks (Wagtail-manageable)
# ---------------------------------------------------------------------------

class Track(models.Model):
    """Conference track – e.g. Python Core, AI, Web, Community, Beginner."""

    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    conference_year = models.IntegerField(validators=[validate_edition_year])
    is_active = models.BooleanField(default=True)
    display_order = models.IntegerField(default=0)

    class Meta:
        ordering = ["display_order", "name"]
        unique_together = ["name", "conference_year"]

    def __str__(self):
        return f"{self.name} ({self.conference_year})"


# ---------------------------------------------------------------------------
# Speaker
# ---------------------------------------------------------------------------

class Speaker(models.Model):
    """
    A person's speaker profile for one edition.

    Owned by a Django account, so proposals hang off the same identity that
    holds their ticket, grant application and visa request -- one person, one
    account, asked for their details once.

    The profile is per-edition rather than per-person because a bio,
    organisation and country legitimately change between one year and the next,
    and a past programme should keep the text as it was published.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="speaker_profiles",
    )
    full_name = models.CharField(
        max_length=200,
        help_text="Name as it should appear in the programme",
    )
    bio = RichTextField(help_text="Speaker biography")
    organisation = models.CharField(max_length=200, blank=True)
    country = models.CharField(max_length=100)
    first_time_speaker = models.BooleanField(default=False)
    photo = models.ForeignKey(
        "wagtailimages.Image",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Headshot for the programme and the speakers page.",
    )
    photo_alt = models.CharField(
        max_length=200,
        blank=True,
        help_text="Describe the photo for screen readers. Defaults to the speaker's name.",
    )

    # --- Onboarding, collected once a talk is confirmed -------------------
    TSHIRT_SIZES = [
        ("xs", "XS"), ("s", "S"), ("m", "M"), ("l", "L"),
        ("xl", "XL"), ("xxl", "2XL"), ("xxxl", "3XL"),
    ]
    tshirt_size = models.CharField(
        max_length=8, blank=True, choices=TSHIRT_SIZES,
    )
    dietary_requirements = models.CharField(
        max_length=255,
        blank=True,
        help_text="Anything the caterers need to know.",
    )
    travel_support_needed = models.BooleanField(
        default=False,
        help_text="Whether they need help getting to the conference.",
    )
    accessibility_needs = models.TextField(
        blank=True,
        help_text="Anything we should arrange so they can present comfortably.",
    )
    onboarding_completed_at = models.DateTimeField(null=True, blank=True)

    conference_year = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["user", "conference_year"]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.full_name} ({self.email})"

    @property
    def photo_alt_text(self):
        return self.photo_alt or self.full_name

    @property
    def onboarding_complete(self):
        return self.onboarding_completed_at is not None

    @property
    def email(self):
        """
        The account's email address -- the single source of truth.

        A property rather than a column so there is no second copy to drift.
        Query it through the relation (``speaker__user__email``), not directly.
        """
        return self.user.email


# ---------------------------------------------------------------------------
# Proposal
# ---------------------------------------------------------------------------

class Proposal(models.Model):
    """A talk / workshop / tutorial / lightning-talk proposal."""

    # -- Statuses --
    STATUS_DRAFT = "draft"
    STATUS_SUBMITTED = "submitted"
    STATUS_UNDER_REVIEW = "under_review"
    STATUS_ACCEPTED = "accepted"
    STATUS_REJECTED = "rejected"
    STATUS_WAITLISTED = "waitlisted"
    STATUS_WITHDRAWN = "withdrawn"
    STATUS_CONFIRMED = "confirmed"
    STATUS_LAPSED = "lapsed"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_SUBMITTED, "Submitted"),
        (STATUS_UNDER_REVIEW, "Under Review"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_WAITLISTED, "Waitlisted"),
        (STATUS_WITHDRAWN, "Withdrawn"),
        (STATUS_CONFIRMED, "Confirmed"),
        (STATUS_LAPSED, "Lapsed — not confirmed in time"),
    ]

    # -- Talk formats --
    FORMAT_TALK = "talk"
    FORMAT_WORKSHOP = "workshop"
    FORMAT_TUTORIAL = "tutorial"
    FORMAT_LIGHTNING = "lightning"

    FORMAT_CHOICES = [
        (FORMAT_TALK, "Talk"),
        (FORMAT_WORKSHOP, "Workshop"),
        (FORMAT_TUTORIAL, "Tutorial"),
        (FORMAT_LIGHTNING, "Lightning Talk"),
    ]

    # -- Audience levels --
    AUDIENCE_BEGINNER = "beginner"
    AUDIENCE_INTERMEDIATE = "intermediate"
    AUDIENCE_ADVANCED = "advanced"

    AUDIENCE_CHOICES = [
        (AUDIENCE_BEGINNER, "Beginner"),
        (AUDIENCE_INTERMEDIATE, "Intermediate"),
        (AUDIENCE_ADVANCED, "Advanced"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    speaker = models.ForeignKey(
        Speaker, on_delete=models.CASCADE, related_name="proposals",
    )
    title = models.CharField(max_length=200)
    abstract = RichTextField(help_text="Public abstract of the talk")
    description = RichTextField(
        help_text="Detailed description for reviewers",
    )
    track = models.ForeignKey(
        Track, on_delete=models.SET_NULL, null=True, related_name="proposals",
    )
    format = models.CharField(max_length=20, choices=FORMAT_CHOICES)
    duration = models.IntegerField(help_text="Duration in minutes")
    audience_level = models.CharField(max_length=20, choices=AUDIENCE_CHOICES)
    prior_delivery = models.BooleanField(
        default=False,
        help_text="Has this talk been delivered before?",
    )
    prior_delivery_link = models.URLField(
        blank=True,
        help_text="Link to previous delivery (optional)",
    )
    slides_url = models.URLField(
        blank=True, help_text="Link to slides or repo (optional)",
    )
    special_requirements = models.TextField(
        blank=True, help_text="Any special requirements",
    )
    notes_to_reviewers = models.TextField(
        blank=True,
        help_text=(
            "Anything reviewers should know that does not belong in the public "
            "abstract. Never shown publicly."
        ),
    )

    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT,
    )
    conference_year = models.IntegerField()
    submitted_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    # -- Convenience helpers --

    @property
    def is_editable(self):
        """Speakers can only edit while in Draft or Submitted status."""
        return self.status in (self.STATUS_DRAFT, self.STATUS_SUBMITTED)

    @property
    def can_withdraw(self):
        return self.status not in (
            self.STATUS_WITHDRAWN,
            self.STATUS_REJECTED,
            self.STATUS_CONFIRMED,
        )

    @property
    def average_score(self):
        reviews = Review.objects.filter(assignment__proposal=self)
        if not reviews.exists():
            return None
        return reviews.aggregate(avg=models.Avg("weighted_score"))["avg"]

    @property
    def review_count(self):
        return Review.objects.filter(assignment__proposal=self).count()


class ProposalCoSpeaker(models.Model):
    """
    Somebody invited to co-present a proposal.

    Recorded by email rather than requiring an account up front: a submitter
    should never be blocked near a deadline waiting for a colleague to register.
    The row links itself to an account the moment one exists for that address, so
    the co-speaker then gets the dashboard and their own onboarding form without
    anyone re-typing anything.
    """

    proposal = models.ForeignKey(
        Proposal, on_delete=models.CASCADE, related_name="co_speakers",
    )
    email = models.EmailField(help_text="The address the invitation went to.")
    display_name = models.CharField(
        max_length=200,
        help_text="How the name should read in the programme before they sign up.",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="cfp_co_speaker_invites",
        help_text="Linked automatically once an account exists for this address.",
    )
    invited_at = models.DateTimeField(auto_now_add=True)
    linked_at = models.DateTimeField(
        null=True, blank=True, help_text="When the invitation found an account.",
    )

    class Meta:
        ordering = ["invited_at"]
        verbose_name = "Co-speaker"
        verbose_name_plural = "Co-speakers"
        constraints = [
            models.UniqueConstraint(
                fields=["proposal", "email"], name="unique_co_speaker_per_proposal",
            ),
        ]
        indexes = [models.Index(fields=["email"])]

    def __str__(self):
        state = "linked" if self.user_id else "pending"
        return f"{self.display_name} <{self.email}> ({state})"

    @property
    def is_pending(self):
        return self.user_id is None

    @property
    def name(self):
        """
        Their name as it should read. Prefers the linked account's speaker
        profile, so a later correction there flows through.
        """
        if self.user_id:
            profile = Speaker.objects.filter(
                user_id=self.user_id, conference_year=self.proposal.conference_year,
            ).first()
            if profile and profile.full_name:
                return profile.full_name
            return self.user.get_full_name() or self.display_name
        return self.display_name

    def link_to(self, user):
        """Attach an account to this invitation."""
        from django.utils import timezone

        self.user = user
        self.linked_at = timezone.now()
        self.save(update_fields=["user", "linked_at"])
        return self


# ---------------------------------------------------------------------------
# Proposal Snapshots
#
# The audit trail lives in the shared ``audit`` app, so "who approved what" has
# one answer across proposals, grants, payments and roles.
# ---------------------------------------------------------------------------

class ProposalSnapshot(models.Model):
    """Immutable snapshot of a proposal at a point in time."""

    proposal = models.ForeignKey(
        Proposal, on_delete=models.CASCADE, related_name="snapshots",
    )
    data = models.JSONField()
    snapshot_type = models.CharField(
        max_length=50, help_text="e.g. 'submission', 'edit'",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Snapshot of '{self.proposal.title}' ({self.snapshot_type})"


# ---------------------------------------------------------------------------
# Review System
# ---------------------------------------------------------------------------

class ReviewerAssignment(models.Model):
    """
    Assignment of a reviewer to a specific proposal.

    Who counts as a reviewer is answered by ``accounts.roles`` -- the CFP
    reviewer and CFP chair roles -- not by a profile row here.
    """

    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="cfp_assignments",
    )
    proposal = models.ForeignKey(
        Proposal, on_delete=models.CASCADE, related_name="assignments",
    )
    assigned_at = models.DateTimeField(auto_now_add=True)
    has_conflict = models.BooleanField(
        default=False,
        help_text="Reviewer has declared a conflict of interest",
    )

    class Meta:
        unique_together = ["reviewer", "proposal"]
        ordering = ["-assigned_at"]

    def __str__(self):
        return f"{self.reviewer} -> {self.proposal.title}"


class Review(models.Model):
    """
    A reviewer's scores and internal comments for a proposal.

    Four dimensions rather than one overall mark, so two reviewers who both say
    "4" can be seen to disagree about *why* -- and so a chair can weigh a
    brilliant idea from a nervous first-timer against a safe talk from a
    practised speaker. Mirrors the rubric travel grants already use.
    """

    DIMENSIONS = ("relevance", "clarity", "depth", "speaker_readiness")

    assignment = models.OneToOneField(
        ReviewerAssignment, on_delete=models.CASCADE, related_name="review",
    )
    relevance = models.IntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="How much this audience wants this talk (1-5).",
    )
    clarity = models.IntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="How clearly the proposal is written and scoped (1-5).",
    )
    depth = models.IntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="Substance: is there something real here (1-5).",
    )
    speaker_readiness = models.IntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="Confidence they can deliver it well (1-5).",
    )
    weighted_score = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=Decimal("0"),
        help_text="Auto-calculated mean of the four dimensions.",
    )
    comments = models.TextField(help_text="Internal review comments")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        total = sum(getattr(self, d) or 0 for d in self.DIMENSIONS)
        self.weighted_score = (Decimal(total) / Decimal(len(self.DIMENSIONS))).quantize(
            Decimal("0.01"),
        )
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Review of '{self.assignment.proposal.title}' by {self.assignment.reviewer}"
