"""
Travel Grant models.

"""

import uuid

from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils import timezone

from decimal import Decimal
from editions.validators import validate_edition_year

class GrantSettings(models.Model):
    """
    Travel Grant configuration - one record per conference year.
    Controls open/close window and budget caps.
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
    application_deadline = models.DateTimeField(
        help_text="Applications will close after this deadline",
    )
    max_grant_budget = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
        help_text="Total budget cap for all grants",
    )
    max_per_applicant = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0"),
        help_text="Maximum grant amount per applicant",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Travel Grant Settings"
        verbose_name_plural = "Travel Grant Settings"

    def __str__(self):
        return f"Travel Grant {self.conference_year} ({self.get_status_display()})"

    @property
    def is_open(self):
        """True when status is Open **and** the deadline has not passed."""
        if self.status != self.STATUS_OPEN:
            return False
        return timezone.now() < self.application_deadline

    @property
    def is_past_deadline(self):
        return timezone.now() >= self.application_deadline

    def save(self, *args, **kwargs):
        if self.status == self.STATUS_OPEN and self.is_past_deadline:
            self.status = self.STATUS_CLOSED
        super().save(*args, **kwargs)

EMPLOYMENT_CHOICES = [
    ("employed", "Employed"),
    ("self_employed", "Self-employed"),
    ("unemployed", "Unemployed"),
    ("student", "Student"),
    ("other", "Other"),
]

class TravelGrantApplication(models.Model):
    """
    A user's travel grant application.
    """

    STATUS_NOT_APPLIED = "not_applied"
    STATUS_DRAFT = "draft"
    STATUS_SUBMITTED = "submitted"
    STATUS_UNDER_REVIEW = "under_review"
    STATUS_APPROVED = "approved"
    STATUS_WAITLISTED = "waitlisted"
    STATUS_NOT_SELECTED = "not_selected"
    STATUS_WITHDRAWN = "withdrawn"
    STATUS_PAID = "paid"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_SUBMITTED, "Submitted"),
        (STATUS_UNDER_REVIEW, "Under Review"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_WAITLISTED, "Waitlisted"),
        (STATUS_NOT_SELECTED, "Not Selected"),
        (STATUS_WITHDRAWN, "Withdrawn"),
        (STATUS_PAID, "Paid"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="travel_grant_applications",
    )
    conference_year = models.IntegerField()
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT,
    )

    country_of_residence = models.CharField(max_length=100)
    city = models.CharField(max_length=100)
    passport_required = models.BooleanField(default=False)

    first_time_pycon = models.BooleanField(default=False)
    community_involvement = models.TextField(blank=True)

    financial_need_reason = models.TextField()
    employment_status = models.CharField(
        max_length=30, choices=EMPLOYMENT_CHOICES,
    )
    is_student = models.BooleanField(default=False)

    estimated_transport_cost = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0"),
    )
    estimated_accommodation_cost = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0"),
    )

    other_funding_sources = models.BooleanField(default=False)
    other_funding_details = models.TextField(blank=True)

    community_impact = models.TextField()
    commit_to_share_learnings = models.BooleanField(default=False)

    confirm_accurate = models.BooleanField(default=False)
    agree_to_refund = models.BooleanField(default=False)

    submitted_at = models.DateTimeField(null=True, blank=True)
    decision_at = models.DateTimeField(null=True, blank=True)
    approved_amount = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = ["user", "conference_year"]

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.email} ({self.conference_year})"

    @property
    def total_requested(self):
        return (self.estimated_transport_cost or Decimal("0")) + (
            self.estimated_accommodation_cost or Decimal("0")
        )

    @property
    def is_editable(self):
        """Applicants can edit only drafts before deadline."""
        return self.status == self.STATUS_DRAFT

    @property
    def can_withdraw(self):
        return self.status in (
            self.STATUS_SUBMITTED,
            self.STATUS_UNDER_REVIEW,
        )

    @property
    def average_score(self):
        """Average weighted score across all reviewers."""
        reviews = TravelGrantReview.objects.filter(
            assignment__application=self,
        )
        if not reviews.exists():
            return None
        return reviews.aggregate(
            avg=models.Avg("weighted_score"),
        )["avg"]

    @property
    def review_count(self):
        return TravelGrantReview.objects.filter(
            assignment__application=self,
        ).count()

class GrantReviewerAssignment(models.Model):
    """
    Assignment of a reviewer to a travel grant application.

    Who counts as a grant reviewer is answered by ``accounts.roles`` -- the
    grant reviewer, grant chair and finance roles. Those stay separate from the
    CFP roles, so reviewing talks confers nothing over travel money.
    """

    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="grant_assignments",
    )
    application = models.ForeignKey(
        TravelGrantApplication,
        on_delete=models.CASCADE,
        related_name="assignments",
    )
    assigned_at = models.DateTimeField(auto_now_add=True)
    has_conflict = models.BooleanField(default=False)

    class Meta:
        unique_together = ["reviewer", "application"]
        ordering = ["-assigned_at"]

    def __str__(self):
        return f"{self.reviewer} -> {self.application}"


class TravelGrantReview(models.Model):
    """
    A reviewer's scores and comments for a travel grant application.
    Weighted score = average of the 4 dimension scores (equal weights by default).
    """

    assignment = models.OneToOneField(
        GrantReviewerAssignment,
        on_delete=models.CASCADE,
        related_name="review",
    )
    need_score = models.IntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="Financial need (1-5)",
    )
    impact_score = models.IntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="Community impact (1-5)",
    )
    contribution_score = models.IntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="Contribution (1-5)",
    )
    diversity_score = models.IntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="Diversity/representation (1-5)",
    )
    weighted_score = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=Decimal("0"),
        help_text="Auto-calculated average of dimension scores",
    )
    comments = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        self.weighted_score = Decimal(
            (self.need_score + self.impact_score
             + self.contribution_score + self.diversity_score) / 4
        ).quantize(Decimal("0.01"))
        super().save(*args, **kwargs)

class TravelGrantPayment(models.Model):
    """Payment record for an approved travel grant."""

    PAYMENT_TYPE_REIMBURSEMENT = "reimbursement"
    PAYMENT_TYPE_DIRECT = "direct"
    PAYMENT_TYPE_CHOICES = [
        (PAYMENT_TYPE_REIMBURSEMENT, "Reimbursement"),
        (PAYMENT_TYPE_DIRECT, "Direct payment"),
    ]

    STATUS_PENDING = "pending"
    STATUS_PAID = "paid"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_PAID, "Paid"),
    ]

    application = models.OneToOneField(
        TravelGrantApplication,
        on_delete=models.CASCADE,
        related_name="payment",
    )
    payment_type = models.CharField(
        max_length=20,
        choices=PAYMENT_TYPE_CHOICES,
        default=PAYMENT_TYPE_REIMBURSEMENT,
    )
    payment_status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )
    amount_paid = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    reference = models.CharField(max_length=200, blank=True)
    receipt = models.FileField(upload_to="grant_receipts/%Y/", blank=True, null=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
