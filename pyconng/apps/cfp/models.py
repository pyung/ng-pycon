import uuid

from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils import timezone

from wagtail.fields import RichTextField


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

    conference_year = models.IntegerField(unique=True)
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
    conference_year = models.IntegerField()
    is_active = models.BooleanField(default=True)
    display_order = models.IntegerField(default=0)

    class Meta:
        ordering = ["display_order", "name"]
        unique_together = ["name", "conference_year"]

    def __str__(self):
        return f"{self.name} ({self.conference_year})"


# ---------------------------------------------------------------------------
# Speaker (token-based light auth)
# ---------------------------------------------------------------------------

class Speaker(models.Model):
    """
    Speaker profile.  Access is via a unique UUID token sent by email –
    no Django account required.
    """

    email = models.EmailField()
    full_name = models.CharField(max_length=200)
    bio = RichTextField(help_text="Speaker biography")
    organisation = models.CharField(max_length=200, blank=True)
    country = models.CharField(max_length=100)
    first_time_speaker = models.BooleanField(default=False)
    access_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    conference_year = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["email", "conference_year"]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.full_name} ({self.email})"


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

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_SUBMITTED, "Submitted"),
        (STATUS_UNDER_REVIEW, "Under Review"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_WAITLISTED, "Waitlisted"),
        (STATUS_WITHDRAWN, "Withdrawn"),
        (STATUS_CONFIRMED, "Confirmed"),
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

    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT,
    )
    conference_year = models.IntegerField()
    submitted_at = models.DateTimeField(null=True, blank=True)
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
        return reviews.aggregate(avg=models.Avg("score"))["avg"]

    @property
    def review_count(self):
        return Review.objects.filter(assignment__proposal=self).count()


# ---------------------------------------------------------------------------
# Proposal Snapshots & Audit Trail
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


class ProposalAuditLog(models.Model):
    """Timestamped audit trail for every proposal action."""

    proposal = models.ForeignKey(
        Proposal, on_delete=models.CASCADE, related_name="audit_logs",
    )
    action = models.CharField(max_length=100)
    old_status = models.CharField(max_length=20, blank=True)
    new_status = models.CharField(max_length=20, blank=True)
    actor = models.CharField(
        max_length=200,
        help_text="Email or username of the person who performed the action",
    )
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.proposal.title}: {self.action}"


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
    """A reviewer's score and internal comments for a proposal."""

    assignment = models.OneToOneField(
        ReviewerAssignment, on_delete=models.CASCADE, related_name="review",
    )
    score = models.IntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="Score from 1 (weak) to 5 (strong)",
    )
    comments = models.TextField(help_text="Internal review comments")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Review of '{self.assignment.proposal.title}' by {self.assignment.reviewer}"


# ---------------------------------------------------------------------------
# Email Templates
# ---------------------------------------------------------------------------

class EmailTemplate(models.Model):
    """Customisable email templates for acceptance / rejection / waitlist."""

    TYPE_ACCEPTANCE = "acceptance"
    TYPE_REJECTION = "rejection"
    TYPE_WAITLIST = "waitlist"

    TYPE_CHOICES = [
        (TYPE_ACCEPTANCE, "Acceptance"),
        (TYPE_REJECTION, "Rejection"),
        (TYPE_WAITLIST, "Waitlist"),
    ]

    name = models.CharField(max_length=100)
    subject = models.CharField(max_length=200)
    body = models.TextField(
        help_text=(
            "Use {speaker_name}, {proposal_title}, {conference_year} "
            "as placeholders"
        ),
    )
    template_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    conference_year = models.IntegerField()

    class Meta:
        unique_together = ["template_type", "conference_year"]

    def __str__(self):
        return f"{self.name} ({self.conference_year})"

    def render(self, context):
        """Replace placeholders with context values and return (subject, body)."""
        subject = self.subject.format(**context)
        body = self.body.format(**context)
        return subject, body
