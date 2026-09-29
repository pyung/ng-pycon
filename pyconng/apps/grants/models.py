"""
Travel grant models.

Two ideas hold this module together. The first is that an award is an *offer* until
somebody accepts it: money set aside for a recipient who never replies is money that
could have gone to the next person on the list, so an offer has a deadline, lapses,
and releases its budget when it does. The second is that the budget is a hard limit
rather than a figure on a dashboard -- the requirement was explicitly "so you can't
overspend", and a cap that is only displayed is not a cap.

A note on the bank details here. They are the most sensitive data this module holds,
and the data-protection work (consent, a privacy policy, export and delete) is still
unbuilt -- it is the first gap in the readiness audit. Collecting an account number
raises what is at stake if that stays unbuilt, which is worth saying out loud rather
than discovering later.
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
        help_text=(
            "Maximum grant amount per applicant. Enforced when a decision is made, "
            "not merely displayed. 0 means no per-applicant limit."
        ),
    )
    acceptance_days = models.IntegerField(
        default=14,
        help_text=(
            "How many days a recipient has to accept an offer before it lapses. "
            "An offer nobody answers holds budget that could go to the next person "
            "on the waitlist. 0 means offers never lapse."
        ),
    )
    auto_promote_waitlist = models.BooleanField(
        default=True,
        help_text=(
            "When a grant is declined or lapses, offer the freed money to the "
            "highest-scoring waitlisted applicant it covers."
        ),
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

    @property
    def has_budget_cap(self):
        return (self.max_grant_budget or Decimal("0")) > 0

    @property
    def has_per_applicant_cap(self):
        return (self.max_per_applicant or Decimal("0")) > 0

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
    STATUS_ACCEPTED = "accepted"
    STATUS_DECLINED = "declined"
    STATUS_LAPSED = "lapsed"
    STATUS_PAID = "paid"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_SUBMITTED, "Submitted"),
        (STATUS_UNDER_REVIEW, "Under Review"),
        (STATUS_APPROVED, "Approved, awaiting acceptance"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_DECLINED, "Declined by applicant"),
        (STATUS_LAPSED, "Lapsed, not accepted in time"),
        (STATUS_WAITLISTED, "Waitlisted"),
        (STATUS_NOT_SELECTED, "Not Selected"),
        (STATUS_WITHDRAWN, "Withdrawn"),
        (STATUS_PAID, "Paid"),
    ]

    #: Statuses that hold budget. An approved offer holds it just as firmly as an
    #: accepted one, because the money cannot be offered to anybody else until the
    #: recipient answers -- which is the whole reason offers have a deadline.
    COMMITTED_STATUSES = (STATUS_APPROVED, STATUS_ACCEPTED, STATUS_PAID)

    #: Statuses where the applicant still has an offer to answer.
    AWAITING_ACCEPTANCE_STATUSES = (STATUS_APPROVED,)

    #: Statuses that release budget once they are reached.
    RELEASED_STATUSES = (STATUS_DECLINED, STATUS_LAPSED, STATUS_WITHDRAWN, STATUS_NOT_SELECTED)

    #: Statuses finance should be looking at. Deliberately *not* approved: an offer
    #: nobody has answered may still lapse, and paying it out before then risks
    #: sending money to somebody who never comes. Accepted is the signal to pay.
    PAYABLE_STATUSES = (STATUS_ACCEPTED, STATUS_PAID)

    GRANT_TYPE_TRAVEL = "travel"
    GRANT_TYPE_ACCOMMODATION = "accommodation"
    GRANT_TYPE_BOTH = "both"
    GRANT_TYPE_CHOICES = [
        (GRANT_TYPE_TRAVEL, "Travel only"),
        (GRANT_TYPE_ACCOMMODATION, "Accommodation only"),
        (GRANT_TYPE_BOTH, "Travel and accommodation"),
    ]

    #: Asked because the sponsor and PSF reports require it, optional because
    #: nobody should have to answer it to get a grant, and self-described because a
    #: fixed list is always wrong for somebody.
    GENDER_WOMAN = "woman"
    GENDER_MAN = "man"
    GENDER_NON_BINARY = "non_binary"
    GENDER_SELF_DESCRIBE = "self_describe"
    GENDER_UNDISCLOSED = "undisclosed"
    GENDER_CHOICES = [
        (GENDER_WOMAN, "Woman"),
        (GENDER_MAN, "Man"),
        (GENDER_NON_BINARY, "Non-binary"),
        (GENDER_SELF_DESCRIBE, "Prefer to self-describe"),
        (GENDER_UNDISCLOSED, "Prefer not to say"),
    ]

    PAYOUT_BANK_TRANSFER = "bank_transfer"
    PAYOUT_OTHER = "other"
    PAYOUT_CHOICES = [
        (PAYOUT_BANK_TRANSFER, "Bank transfer"),
        (PAYOUT_OTHER, "Something else (tell us below)"),
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

    grant_type = models.CharField(
        max_length=20,
        choices=GRANT_TYPE_CHOICES,
        default=GRANT_TYPE_BOTH,
        help_text=(
            "What they are asking for. Asked explicitly rather than inferred from "
            "which cost fields happen to be filled in, so the review queue can be "
            "filtered by it and the reports can report on it."
        ),
    )
    is_speaking = models.BooleanField(
        default=False,
        help_text=(
            "Declared by the applicant. Stored rather than derived from the CFP at "
            "read time, so the answer they gave survives a proposal being withdrawn "
            "and a reviewer sees what was said when it was said."
        ),
    )

    country_of_residence = models.CharField(max_length=100)
    city = models.CharField(max_length=100)
    passport_required = models.BooleanField(default=False)

    gender = models.CharField(
        max_length=20,
        choices=GENDER_CHOICES,
        blank=True,
        default="",
        help_text=(
            "Optional. Collected only because sponsor and PSF reports ask for the "
            "breakdown; never shown to reviewers and never a factor in a decision."
        ),
    )
    gender_self_described = models.CharField(
        max_length=100,
        blank=True,
        help_text="Used when they chose to self-describe.",
    )

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

    # --- The offer, and what the recipient does about it -------------------
    acceptance_deadline = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Set when the grant is approved, from the edition's acceptance window. "
            "After this the offer lapses and its money returns to the budget."
        ),
    )
    accepted_at = models.DateTimeField(null=True, blank=True)
    declined_at = models.DateTimeField(null=True, blank=True)
    decline_reason = models.TextField(
        blank=True,
        help_text=(
            "Why they turned it down, in their words. Worth reading: 'the amount "
            "would not cover the flight' is a different problem from 'I can no "
            "longer come'."
        ),
    )
    promoted_from_waitlist_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set when this application was moved off the waitlist into an offer.",
    )
    decision_note = models.TextField(
        blank=True,
        help_text="Shown to the applicant with the decision.",
    )

    # --- Where the money should go ----------------------------------------
    payout_method = models.CharField(
        max_length=20, choices=PAYOUT_CHOICES, blank=True, default=""
    )
    bank_name = models.CharField(max_length=120, blank=True)
    account_name = models.CharField(
        max_length=150,
        blank=True,
        help_text="Exactly as the bank holds it, or the transfer bounces.",
    )
    account_number = models.CharField(max_length=40, blank=True)
    payout_notes = models.TextField(
        blank=True, help_text="Anything finance needs to know to pay them."
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

    # --- The offer ---------------------------------------------------------

    @property
    def committed_amount(self):
        """
        What this application is holding against the budget right now.

        An approved-but-unanswered offer counts in full: until the recipient
        replies, the money cannot be promised to anybody else.
        """
        if self.status not in self.COMMITTED_STATUSES:
            return Decimal("0")
        return Decimal(self.approved_amount or 0)

    @property
    def awaiting_acceptance(self):
        return self.status in self.AWAITING_ACCEPTANCE_STATUSES

    @property
    def can_accept(self):
        """Whether the recipient may still accept. A passed deadline cannot."""
        return self.awaiting_acceptance and not self.offer_has_expired

    @property
    def can_decline(self):
        """
        Declining stays open after the deadline, deliberately.

        Somebody telling us late that they cannot come is doing us a favour, and
        refusing the message because a timer ran out would be perverse.
        """
        return self.status in (self.STATUS_APPROVED, self.STATUS_ACCEPTED)

    @property
    def offer_has_expired(self):
        return bool(
            self.acceptance_deadline
            and self.status in self.AWAITING_ACCEPTANCE_STATUSES
            and timezone.now() > self.acceptance_deadline
        )

    @property
    def days_left_to_accept(self):
        """Whole days remaining, or None when there is no live deadline."""
        if not self.acceptance_deadline or not self.awaiting_acceptance:
            return None
        remaining = self.acceptance_deadline - timezone.now()
        return max(remaining.days, 0)

    @property
    def is_awarded(self):
        """Accepted or already paid: the applicant is coming on our money."""
        return self.status in (self.STATUS_ACCEPTED, self.STATUS_PAID)

    @property
    def has_payout_details(self):
        return bool(self.account_number and self.account_name)

    @property
    def gender_display(self):
        """What to show in a report, honouring a self-description."""
        if self.gender == self.GENDER_SELF_DESCRIBE and self.gender_self_described:
            return self.gender_self_described
        if not self.gender:
            return "Not answered"
        return self.get_gender_display()

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
    STATUS_VERIFIED = "verified"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_PAID, "Paid"),
        (STATUS_VERIFIED, "Receipt verified"),
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
    receipt = models.FileField(
        upload_to="grant_receipts/%Y/",
        blank=True,
        null=True,
        help_text=(
            "Uploaded by the recipient or by finance. A reimbursement needs one "
            "before it can be verified."
        ),
    )
    receipt_uploaded_at = models.DateTimeField(null=True, blank=True, editable=False)
    receipt_verified_at = models.DateTimeField(null=True, blank=True, editable=False)
    receipt_verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="grant_receipts_verified",
        help_text="Who checked the receipt against the amount paid.",
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.application} - {self.get_payment_status_display()}"

    @property
    def is_paid(self):
        return self.payment_status in (self.STATUS_PAID, self.STATUS_VERIFIED)

    @property
    def is_verified(self):
        return self.payment_status == self.STATUS_VERIFIED

    @property
    def needs_receipt(self):
        """
        Whether this payment is still waiting on a receipt.

        Only reimbursements do: a direct payment to a hotel or an airline has its own
        paper trail on our side, and asking the recipient for one would be asking
        them to evidence a transaction they never handled.
        """
        return (
            self.payment_type == self.PAYMENT_TYPE_REIMBURSEMENT
            and self.is_paid
            and not self.receipt
        )

    @property
    def can_verify(self):
        return self.is_paid and bool(self.receipt) and not self.is_verified
