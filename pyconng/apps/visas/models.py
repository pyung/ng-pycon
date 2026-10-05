"""
Visa invitation letters: the request, the decision, and the letter itself.

Two things make this module different from every other one on the site.

**The lead time is external and unforgiving.** An embassy appointment takes weeks to
get and weeks to come back from, so a letter that arrives in good time for us is
often too late for the applicant. That is why the window opens earlier than anything
else, why the settings carry a recommended deadline derived from the conference dates
rather than typed in, and why an approval queue that is merely "kept on top of" is
not good enough -- the page says how long each request has been waiting.

**It holds the most sensitive data on the site.** A passport number is the one field
here that is genuinely dangerous to leak, and the honest position is that the site's
data-protection work (a privacy notice, consent, export and delete) does not exist
yet. So this module carries its own controls rather than waiting for them:

* passport details are readable only by a named Visa officer -- a granted role that
  nothing implies, not even a superuser, for the same reason the Code of Conduct
  team works that way;
* lists and queues show a masked number, never the whole one;
* every reading of a full passport number is written to the audit trail, so there
  is an answer to "who looked";
* the details are deleted automatically once the conference is over and cannot be
  needed again, while the letter's reference, its decision and its trail survive --
  which is data minimisation done by the clock rather than by good intentions.

None of that is a substitute for the privacy notice. It is what can be done inside
this module so that storing the data is defensible while that is outstanding.
"""

import secrets
from datetime import timedelta

from django.conf import settings as django_settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from wagtail.fields import RichTextField

from editions.validators import validate_edition_year

#: Reference alphabet without the characters people mishear or mistype. A consular
#: officer reads these down a phone line or types them from a printout, and O/0 and
#: I/1 are exactly the pairs that go wrong.
REFERENCE_ALPHABET = "ABCDEFGHJKMNPRSTUVWXY23479"

#: How long before the conference an embassy realistically needs. Twelve weeks is
#: the upper end of what applicants report, and planning to the upper end is the
#: point of having the number at all.
DEFAULT_LEAD_DAYS = 90

#: The character standing in for the middle of a passport number.
MASK = "•"


def generate_reference(year, existing=None):
    """
    A reference an embassy can quote back: ``PYNG-2027-4KTM9P``.

    Six characters from an unambiguous alphabet, so guessing one is not practical
    while reading one aloud is. ``existing`` lets the caller pass a set of taken
    references for a bulk backfill without a query each time.
    """
    while True:
        tail = "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(6))
        reference = f"PYNG-{year}-{tail}"
        if existing is not None:
            if reference not in existing:
                existing.add(reference)
                return reference
            continue
        if not VisaLetterRequest.objects.filter(reference=reference).exists():
            return reference


class VisaSettings(models.Model):
    """
    The letter window for one edition, and what the letterhead says.

    Everything an officer would otherwise have to ask a developer for lives here:
    when requests open and close, who signs, what the organisation is called in a
    formal document, and how long the details are kept afterwards. Follows
    GrantSettings and VolunteerSettings down to the auto-close on save.
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
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    opens_on = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When requests open. Earlier than every other call on the site: an "
            "embassy appointment can take a month to get and a month to come back."
        ),
    )
    request_deadline = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Last moment a request can be made. Blank means requests stay open "
            "while the status is Open."
        ),
    )
    embassy_lead_days = models.IntegerField(
        default=DEFAULT_LEAD_DAYS,
        help_text=(
            "How many days before the conference an applicant realistically needs "
            "their letter. Used to work out the recommended deadline and to warn "
            "when the real one is later than that."
        ),
    )
    processing_days = models.IntegerField(
        default=5,
        help_text=(
            "How long we tell applicants a decision takes. Shown on the form, so "
            "put the truth here rather than the ambition."
        ),
    )

    intro = RichTextField(
        blank=True,
        help_text="What the letter is, what it is not, and who can have one.",
    )
    requirements_note = RichTextField(
        blank=True,
        help_text=(
            "What an applicant should have ready: passport details as printed, the "
            "embassy they will apply at, their travel dates."
        ),
    )

    # -- letterhead -------------------------------------------------------
    organisation_name = models.CharField(
        max_length=200,
        default="PyCon Nigeria",
        help_text="The organisation's name as it should appear in a formal letter.",
    )
    organisation_address = models.TextField(
        blank=True,
        help_text="Postal address for the letterhead, one line per line.",
    )
    organisation_registration = models.CharField(
        max_length=100,
        blank=True,
        help_text=(
            "Registration or RC number, if there is one. Consular officers look for "
            "it, and a letter without one is weaker."
        ),
    )
    contact_email = models.EmailField(
        blank=True,
        help_text="Where an embassy should write to check the letter.",
    )
    contact_phone = models.CharField(max_length=50, blank=True)
    signatory_name = models.CharField(
        max_length=150,
        blank=True,
        help_text="Who signs the letters. Appears above the signature line.",
    )
    signatory_title = models.CharField(
        max_length=150,
        blank=True,
        help_text="Their role, e.g. Conference Chair.",
    )
    logo = models.ForeignKey(
        "wagtailimages.Image",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text=(
            "Logo for the letterhead. Converted to a JPEG for the PDF, so anything "
            "uploadable works."
        ),
    )

    # -- data protection --------------------------------------------------
    verification_enabled = models.BooleanField(
        default=True,
        help_text=(
            "Let an embassy confirm a reference number on a public page. It shows "
            "only whether the reference is genuine and who it was issued to -- "
            "never passport details."
        ),
    )
    retain_details_days = models.IntegerField(
        default=30,
        help_text=(
            "Days after the conference ends before passport details are deleted. "
            "The letter, its reference and the audit trail are kept; the passport "
            "number, date of birth and expiry are not. 0 deletes as soon as the "
            "conference is over."
        ),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "visa letter settings"
        verbose_name_plural = "visa letter settings"
        ordering = ["-conference_year"]

    def __str__(self):
        return f"Visa letters {self.conference_year} ({self.get_status_display()})"

    def save(self, *args, **kwargs):
        if self.status == self.STATUS_OPEN and self.is_past_deadline:
            self.status = self.STATUS_CLOSED
        super().save(*args, **kwargs)

    @classmethod
    def for_year(cls, year):
        """Settings for ``year``, created as a draft if absent. Never None."""
        obj, _ = cls.objects.get_or_create(conference_year=year)
        return obj

    @property
    def is_past_deadline(self):
        return bool(self.request_deadline and timezone.now() >= self.request_deadline)

    @property
    def has_opened(self):
        return not self.opens_on or timezone.now() >= self.opens_on

    @property
    def is_open(self):
        return (
            self.status == self.STATUS_OPEN
            and self.has_opened
            and not self.is_past_deadline
        )

    def _edition(self):
        from editions.current import edition_for_year

        return edition_for_year(self.conference_year)

    @property
    def recommended_deadline(self):
        """
        The date requests should close by, from the conference's own start date.

        Derived rather than typed so that moving the conference moves it. None when
        the edition has no dates yet, and the admin says so rather than implying a
        deadline it cannot work out.
        """
        edition = self._edition()
        if edition is None or not edition.starts_on:
            return None
        return edition.starts_on - timedelta(days=max(0, self.embassy_lead_days))

    @property
    def deadline_is_too_late(self):
        """
        True when the configured deadline leaves less time than an embassy needs.

        Worth surfacing rather than silently accepting: a deadline two weeks out
        reads as organised and is, for this one thing, useless.
        """
        recommended = self.recommended_deadline
        if recommended is None or not self.request_deadline:
            return False
        return timezone.localtime(self.request_deadline).date() > recommended

    @property
    def days_until_deadline(self):
        if not self.request_deadline:
            return None
        delta = timezone.localtime(self.request_deadline).date() - timezone.localdate()
        return delta.days

    def purge_on_or_after(self):
        """The date passport details for this edition become deletable."""
        edition = self._edition()
        if edition is None:
            return None
        end = edition.ends_on or edition.starts_on
        if not end:
            return None
        return end + timedelta(days=max(0, self.retain_details_days))


class VisaLetterRequestQuerySet(models.QuerySet):
    def for_year(self, year):
        return self.filter(conference_year=year)

    def pending(self):
        return self.filter(status=VisaLetterRequest.STATUS_SUBMITTED)

    def decided(self):
        return self.filter(
            status__in=(VisaLetterRequest.STATUS_APPROVED, VisaLetterRequest.STATUS_REJECTED)
        )

    def approved(self):
        return self.filter(status=VisaLetterRequest.STATUS_APPROVED)

    def holding_details(self):
        """Requests that still have passport data stored against them."""
        return self.filter(purged_at__isnull=True).exclude(passport_number="")


class VisaLetterRequest(models.Model):
    """
    One person's request for an invitation letter, and the letter once approved.

    The request and the letter are one record rather than two. A letter only ever
    exists because a request was approved, it is regenerated from these fields
    rather than stored as a file, and splitting them would mean keeping the name on
    the letter in step with the name on the request -- which is the kind of pair
    that drifts.
    """

    STATUS_DRAFT = "draft"
    STATUS_SUBMITTED = "submitted"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_WITHDRAWN = "withdrawn"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_SUBMITTED, "Submitted"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_WITHDRAWN, "Withdrawn"),
    ]

    #: Why this person is coming. Set from their records rather than chosen, so a
    #: letter cannot describe somebody as a speaker because they typed it.
    BASIS_ATTENDEE = "attendee"
    BASIS_SPEAKER = "speaker"
    BASIS_GRANTEE = "grantee"
    BASIS_ORGANIZER = "organizer"
    BASIS_CHOICES = [
        (BASIS_ATTENDEE, "Ticket holder"),
        (BASIS_SPEAKER, "Speaker"),
        (BASIS_GRANTEE, "Travel grant recipient"),
        (BASIS_ORGANIZER, "Organizing team"),
    ]

    BASIS_DESCRIPTIONS = {
        BASIS_ATTENDEE: "a registered delegate",
        BASIS_SPEAKER: "an accepted speaker",
        BASIS_GRANTEE: "a travel grant recipient and delegate",
        BASIS_ORGANIZER: "a member of the organizing team",
    }

    user = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="visa_requests",
    )
    conference_year = models.IntegerField(validators=[validate_edition_year])
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    basis = models.CharField(
        max_length=20,
        choices=BASIS_CHOICES,
        blank=True,
        help_text="Recorded at submission from the applicant's own records.",
    )

    # -- as printed in the passport ---------------------------------------
    # Split rather than one "full name", because a consular officer matches the
    # letter against the passport's machine-readable line, which is surname-first.
    passport_surname = models.CharField(
        max_length=100,
        blank=True,
        help_text="Surname exactly as printed in the passport.",
    )
    passport_given_names = models.CharField(
        max_length=150,
        blank=True,
        help_text="Given names exactly as printed in the passport.",
    )
    nationality = models.CharField(max_length=100, blank=True)
    passport_number = models.CharField(max_length=40, blank=True)
    passport_expiry = models.DateField(null=True, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)

    # -- the application --------------------------------------------------
    embassy_city = models.CharField(
        max_length=120,
        blank=True,
        help_text="Where they will apply: the city of the embassy or consulate.",
    )
    embassy_country = models.CharField(max_length=100, blank=True)
    arrival_on = models.DateField(null=True, blank=True)
    departure_on = models.DateField(null=True, blank=True)
    accommodation = models.TextField(
        blank=True,
        help_text="Where they will stay, if known. Consulates often ask.",
    )
    notes = models.TextField(
        blank=True,
        help_text="Anything the officer should know, e.g. a name that differs from the account.",
    )

    # -- decision ---------------------------------------------------------
    reference = models.CharField(max_length=32, blank=True, unique=True, null=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="visa_decisions",
    )
    decision_note = models.TextField(
        blank=True,
        help_text="Shown to the applicant. Required when rejecting.",
    )
    letter_issued_at = models.DateTimeField(null=True, blank=True)
    letter_downloads = models.IntegerField(default=0)

    # -- retention --------------------------------------------------------
    purged_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When the passport details were deleted. The letter and its reference "
            "outlive them, so verification keeps working."
        ),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = VisaLetterRequestQuerySet.as_manager()

    class Meta:
        ordering = ["-submitted_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "conference_year"],
                name="one_visa_request_per_user_per_year",
            ),
        ]
        indexes = [
            models.Index(fields=["conference_year", "status"]),
            models.Index(fields=["reference"]),
        ]

    def __str__(self):
        return f"Visa letter {self.reference or '(unreferenced)'} for {self.user}"

    def clean(self):
        if self.arrival_on and self.departure_on and self.departure_on < self.arrival_on:
            raise ValidationError(
                {"departure_on": "Departure cannot be before arrival."}
            )
        if self.passport_expiry and self.arrival_on:
            if self.passport_expiry <= self.arrival_on:
                raise ValidationError(
                    {
                        "passport_expiry": (
                            "This passport expires before the conference. Most "
                            "consulates require six months' validity beyond the "
                            "trip, so renew it before applying."
                        )
                    }
                )

    # -- names ------------------------------------------------------------

    @property
    def passport_name(self):
        """Given names then surname, as a letter addresses somebody."""
        parts = [self.passport_given_names.strip(), self.passport_surname.strip()]
        return " ".join(p for p in parts if p)

    @property
    def passport_name_formal(self):
        """Surname first, in capitals, as the passport's own data page has it."""
        if not self.passport_surname:
            return self.passport_name
        given = self.passport_given_names.strip()
        return f"{self.passport_surname.strip().upper()}, {given}".rstrip(", ")

    @property
    def display_name(self):
        """The best name available, falling back to the account once purged."""
        return (
            self.passport_name
            or self.user.get_full_name()
            or self.user.email
        )

    @property
    def embassy(self):
        parts = [self.embassy_city.strip(), self.embassy_country.strip()]
        return ", ".join(p for p in parts if p)

    # -- the sensitive field ----------------------------------------------

    @property
    def passport_number_masked(self):
        """
        Enough to recognise a record, not enough to use it.

        Queues and lists show this. The whole number is read through
        :func:`visas.services.reveal_passport_number`, which writes to the audit
        trail first.
        """
        number = (self.passport_number or "").strip()
        if not number:
            return "—"
        if len(number) <= 4:
            return MASK * len(number)
        return number[0] + MASK * (len(number) - 4) + number[-3:]

    @property
    def details_retained(self):
        return self.purged_at is None and bool(self.passport_number)

    # -- state ------------------------------------------------------------

    @property
    def is_pending(self):
        return self.status == self.STATUS_SUBMITTED

    @property
    def is_approved(self):
        return self.status == self.STATUS_APPROVED

    @property
    def can_edit(self):
        """Editable until it is decided. A typo in a passport number voids a letter."""
        return self.status in (self.STATUS_DRAFT, self.STATUS_SUBMITTED)

    @property
    def can_withdraw(self):
        return self.status in (self.STATUS_DRAFT, self.STATUS_SUBMITTED)

    @property
    def has_letter(self):
        return bool(self.is_approved and self.reference)

    @property
    def days_waiting(self):
        """
        How long this request has been sitting, in whole days.

        On the queue rather than in a report, because the cost of a slow decision
        here is somebody missing an embassy appointment.
        """
        if not self.submitted_at or self.decided_at:
            return None
        return (timezone.now() - self.submitted_at).days

    def purge_details(self, *, save=True):
        """
        Delete the passport details, keeping the letter and its trail.

        Idempotent, and deliberately not a model delete: the reference has to keep
        resolving for an embassy that checks it later, and the audit trail of who
        decided what has its own reasons to survive.
        """
        if self.purged_at is not None:
            return False
        self.passport_number = ""
        self.date_of_birth = None
        self.passport_expiry = None
        self.purged_at = timezone.now()
        if save:
            self.save(update_fields=[
                "passport_number", "date_of_birth", "passport_expiry",
                "purged_at", "updated_at",
            ])
        return True
