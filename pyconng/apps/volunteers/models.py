"""
Call for Volunteers: applying, being accepted, and knowing when to turn up.

A conference runs on people who are not paid, which makes two things matter more
here than anywhere else on the site. The first is that a volunteer must never be
left wondering: they hear back on their application, they are told which team they
are on and who leads it, and they can see their own shifts without asking. The
second is that the organizers must be able to see the gaps -- an under-staffed
Saturday morning is not something to discover on Saturday morning.

Availability is stored as rows rather than free text, because it has to be
answerable by machine: "who is free on the setup day" is the question a
coordinator actually asks, and a paragraph saying "mostly weekends, maybe Friday"
cannot answer it.
"""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from wagtail.fields import RichTextField

from editions.validators import validate_edition_year

#: Shared with cfp.Speaker on purpose: the same person may appear in both, and
#: two different size scales on one order to the printer is a real annoyance.
TSHIRT_SIZES = [
    ("xs", "XS"), ("s", "S"), ("m", "M"), ("l", "L"),
    ("xl", "XL"), ("xxl", "2XL"), ("xxxl", "3XL"),
]


class VolunteerSettings(models.Model):
    """
    The call for volunteers for one edition: when it is open, and what it says.

    Follows GrantSettings and CFPSettings deliberately, down to the auto-close on
    save, so a coordinator who has run one of those recognises this one.
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
    application_deadline = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Applications close after this. Blank means they stay open while the status is Open.",
    )

    intro = RichTextField(
        blank=True, help_text="What volunteering at PyCon Nigeria involves."
    )
    what_we_ask = RichTextField(
        blank=True,
        help_text=(
            "The commitment, stated plainly: how many shifts, which days, whether "
            "training is required. People decline for good reasons, and finding out "
            "late costs everybody more."
        ),
    )
    what_you_get = RichTextField(
        blank=True,
        help_text="A ticket, a shirt, food on shift, a certificate. Say it explicitly.",
    )

    target_count = models.IntegerField(
        default=0,
        help_text="How many volunteers this edition needs in total. 0 means no target set.",
    )
    setup_days_before = models.IntegerField(
        default=1,
        help_text=(
            "Days before the conference that need volunteers, for set-up. Adds those "
            "days to the availability question on the form."
        ),
    )
    teardown_days_after = models.IntegerField(
        default=1, help_text="Days after the conference that need volunteers, for pack-down."
    )
    max_team_choices = models.IntegerField(
        default=3,
        help_text="How many teams an applicant may put themselves forward for.",
    )

    shifts_published = models.BooleanField(
        default=False,
        help_text=(
            "Until this is on, volunteers cannot see their shifts. Rosters get "
            "rebuilt several times before they settle, and a volunteer who reads a "
            "draft roster turns up at the wrong hour."
        ),
    )
    certificates_available_from = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Certificates appear for volunteers who worked a shift after this moment. "
            "Blank means never -- normally set to the day after the conference."
        ),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "volunteer settings"
        verbose_name_plural = "volunteer settings"
        ordering = ["-conference_year"]

    def __str__(self):
        return f"Volunteers {self.conference_year} ({self.get_status_display()})"

    def save(self, *args, **kwargs):
        # Same guard as the CFP and grants: a deadline that has passed closes the
        # call, so nobody applies into a window that is only nominally open.
        if self.status == self.STATUS_OPEN and self.is_past_deadline:
            self.status = self.STATUS_CLOSED
        super().save(*args, **kwargs)

    @classmethod
    def for_year(cls, year):
        """
        Settings for ``year``, created as a draft if absent.

        Never None, so no caller needs an "if settings is None" branch, and the
        default is the cautious one: a draft call is not open.
        """
        obj, _ = cls.objects.get_or_create(conference_year=year)
        return obj

    @property
    def is_past_deadline(self):
        return bool(self.application_deadline and timezone.now() >= self.application_deadline)

    @property
    def is_open(self):
        return self.status == self.STATUS_OPEN and not self.is_past_deadline

    @property
    def certificates_available(self):
        return bool(
            self.certificates_available_from
            and timezone.now() >= self.certificates_available_from
        )

    def availability_days(self):
        """
        The days an applicant is asked about: set-up, the conference, pack-down.

        Read from the Edition rather than typed again here, so moving the
        conference moves this. Returns an empty list when the edition has no dates
        yet, and the form says so rather than showing an empty question.
        """
        from datetime import timedelta

        from editions.current import edition_for_year

        edition = edition_for_year(self.conference_year)
        if edition is None or not edition.starts_on:
            return []
        end = edition.ends_on or edition.starts_on

        days = []
        first = edition.starts_on - timedelta(days=max(0, self.setup_days_before))
        last = end + timedelta(days=max(0, self.teardown_days_after))
        current = first
        while current <= last:
            if current < edition.starts_on:
                label = "Set-up"
            elif current > end:
                label = "Pack-down"
            else:
                label = "Conference"
            days.append((current, label))
            current += timedelta(days=1)
        return days


class VolunteerTeam(models.Model):
    """
    One team volunteers are assigned to, with a lead and a target headcount.

    Per edition rather than global: the teams change, and a team that existed once
    should stay on its own year's records rather than being renamed under them.
    """

    name = models.CharField(max_length=100, help_text="e.g. Registration, Room hosts, AV")
    slug = models.SlugField(max_length=100)
    conference_year = models.IntegerField(validators=[validate_edition_year])
    description = models.TextField(
        blank=True, help_text="What this team actually does, in the applicant's words."
    )
    lead = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="volunteer_teams_led",
        help_text="The named person a volunteer on this team reports to.",
    )
    target_count = models.IntegerField(
        default=0, help_text="How many volunteers this team needs. 0 means no target."
    )
    display_order = models.IntegerField(default=0)
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive teams keep their history but are not offered on the form.",
    )

    class Meta:
        ordering = ["display_order", "name"]
        unique_together = ["slug", "conference_year"]

    def __str__(self):
        return f"{self.name} ({self.conference_year})"

    @property
    def assigned_count(self):
        return self.assigned_volunteers.filter(
            status=VolunteerApplication.STATUS_ACCEPTED
        ).count()

    @property
    def interested_count(self):
        """How many applicants put this team down, whatever their status."""
        return self.interested_applications.count()

    @property
    def shortfall(self):
        """How many more this team needs. Zero when the target is met or unset."""
        if self.target_count <= 0:
            return 0
        return max(self.target_count - self.assigned_count, 0)

    @property
    def staffing_display(self):
        if self.target_count <= 0:
            return f"{self.assigned_count} assigned"
        return f"{self.assigned_count}/{self.target_count}"


class VolunteerApplication(models.Model):
    """
    One person offering to help.

    Most fields are optional. The ones that are not -- the teams they would work
    in, and something about why -- are the two a coordinator cannot do without.
    Everything else is asked because it is genuinely needed on the day: a shirt
    size for the printer, dietary needs for the caterer, an emergency contact
    because these people are on their feet in a building all day.
    """

    STATUS_DRAFT = "draft"
    STATUS_SUBMITTED = "submitted"
    STATUS_UNDER_REVIEW = "under_review"
    STATUS_ACCEPTED = "accepted"
    STATUS_WAITLISTED = "waitlisted"
    STATUS_NOT_SELECTED = "not_selected"
    STATUS_WITHDRAWN = "withdrawn"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_SUBMITTED, "Submitted"),
        (STATUS_UNDER_REVIEW, "Under review"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_WAITLISTED, "Waitlisted"),
        (STATUS_NOT_SELECTED, "Not selected"),
        (STATUS_WITHDRAWN, "Withdrawn"),
    ]

    #: Statuses a coordinator still has to act on.
    OPEN_STATUSES = (STATUS_SUBMITTED, STATUS_UNDER_REVIEW)
    #: Statuses where the applicant has been told the outcome.
    DECIDED_STATUSES = (STATUS_ACCEPTED, STATUS_WAITLISTED, STATUS_NOT_SELECTED)

    EXPERIENCE_NONE = "none"
    EXPERIENCE_SOME = "some"
    EXPERIENCE_LOTS = "lots"
    EXPERIENCE_CHOICES = [
        (EXPERIENCE_NONE, "This would be my first time"),
        (EXPERIENCE_SOME, "I have volunteered at an event before"),
        (EXPERIENCE_LOTS, "I have run or coordinated events"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="volunteer_applications",
    )
    conference_year = models.IntegerField(validators=[validate_edition_year])
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)

    # --- Who they are -------------------------------------------------------
    full_name = models.CharField(
        max_length=150,
        help_text="As they want it on a badge.",
    )
    phone = models.CharField(
        max_length=40,
        blank=True,
        help_text="For the day itself. A team lead with twelve people needs to reach them.",
    )

    # --- What they want to do ----------------------------------------------
    teams = models.ManyToManyField(
        VolunteerTeam,
        related_name="interested_applications",
        help_text="Teams they would be happy to work in.",
    )
    experience = models.CharField(
        max_length=20, choices=EXPERIENCE_CHOICES, default=EXPERIENCE_NONE
    )
    experience_detail = models.TextField(
        blank=True, help_text="Anything they want to add about it."
    )
    motivation = models.TextField(
        help_text="Why they want to help. Read before any decision is made."
    )
    skills = models.CharField(
        max_length=300,
        blank=True,
        help_text=(
            "Languages spoken, first aid, AV, photography, sign language -- whatever "
            "would change which team they are put on."
        ),
    )

    # --- What the day needs to know ----------------------------------------
    tshirt_size = models.CharField(max_length=8, blank=True, choices=TSHIRT_SIZES)
    dietary_requirements = models.CharField(
        max_length=255, blank=True, help_text="Anything the caterers need to know."
    )
    accessibility_needs = models.TextField(
        blank=True,
        help_text=(
            "What they need to work comfortably. Read it before assigning a shift, "
            "not after."
        ),
    )
    emergency_contact_name = models.CharField(max_length=150, blank=True)
    emergency_contact_phone = models.CharField(max_length=40, blank=True)

    # --- The decision -------------------------------------------------------
    assigned_team = models.ForeignKey(
        VolunteerTeam,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_volunteers",
    )
    assigned_lead = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="volunteers_led",
        help_text="Defaults to the team's lead, but can differ for a large team.",
    )
    decision_note = models.TextField(
        blank=True,
        help_text=(
            "Shown to the applicant with the decision. A rejection with a sentence "
            "is a person you can ask again next year."
        ),
    )
    internal_note = models.TextField(
        blank=True, help_text="Never shown to the applicant."
    )
    decided_at = models.DateTimeField(null=True, blank=True, editable=False)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="volunteer_decisions",
    )

    submitted_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-submitted_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "conference_year"],
                name="one_volunteer_application_per_user_per_year",
            )
        ]
        indexes = [models.Index(fields=["conference_year", "status"])]

    def __str__(self):
        return f"{self.full_name} ({self.conference_year}, {self.get_status_display()})"

    def save(self, *args, **kwargs):
        if self.status != self.STATUS_DRAFT and self.submitted_at is None:
            self.submitted_at = timezone.now()
        if self.status in self.DECIDED_STATUSES and self.decided_at is None:
            self.decided_at = timezone.now()
        if self.status not in self.DECIDED_STATUSES:
            # Reopening a decided application clears the stamp, so "decided" and
            # "has a decision date" can never disagree.
            self.decided_at = None
        super().save(*args, **kwargs)

    @property
    def is_accepted(self):
        return self.status == self.STATUS_ACCEPTED

    @property
    def is_decided(self):
        return self.status in self.DECIDED_STATUSES

    @property
    def is_editable(self):
        """
        Whether the applicant may still change it.

        Editable until it is decided: a volunteer whose availability changes in
        March should fix it themselves rather than emailing somebody.
        """
        return self.status in (self.STATUS_DRAFT,) + self.OPEN_STATUSES

    @property
    def lead_contact(self):
        return self.assigned_lead or (self.assigned_team.lead if self.assigned_team else None)

    def availability_summary(self):
        """``[(day, [period labels])]`` in date order, for a page or an export."""
        grouped = {}
        for slot in self.availability.all():
            grouped.setdefault(slot.day, []).append(slot.get_period_display())
        return sorted(grouped.items())

    def team_names(self):
        return ", ".join(team.name for team in self.teams.all())

    @property
    def shift_count(self):
        return self.shift_assignments.count()

    @property
    def worked_a_shift(self):
        """
        Whether they were actually there, which is what a certificate attests.

        Reads the check-in on the shift assignment rather than merely being
        assigned one: a certificate for somebody who did not come is worth nothing
        to the people who did.
        """
        return self.shift_assignments.filter(attended_at__isnull=False).exists()


class VolunteerAvailability(models.Model):
    """
    One day-and-period a volunteer says they can work.

    Rows rather than a text field, because the question a coordinator asks is "who
    is free on Friday morning" and only structured data answers it.
    """

    PERIOD_MORNING = "morning"
    PERIOD_AFTERNOON = "afternoon"
    PERIOD_EVENING = "evening"
    PERIOD_CHOICES = [
        (PERIOD_MORNING, "Morning"),
        (PERIOD_AFTERNOON, "Afternoon"),
        (PERIOD_EVENING, "Evening"),
    ]

    application = models.ForeignKey(
        VolunteerApplication, on_delete=models.CASCADE, related_name="availability"
    )
    day = models.DateField()
    period = models.CharField(max_length=20, choices=PERIOD_CHOICES)

    class Meta:
        ordering = ["day", "period"]
        constraints = [
            models.UniqueConstraint(
                fields=["application", "day", "period"], name="one_availability_slot"
            )
        ]

    def __str__(self):
        return f"{self.day} {self.get_period_display()}"


class Shift(models.Model):
    """
    A block of work at a place and a time, with a headcount.

    Kept separate from the team so one team can have many shifts and a volunteer
    can work more than one -- and so an under-staffed shift is a visible number
    rather than something noticed on the day.
    """

    conference_year = models.IntegerField(validators=[validate_edition_year])
    team = models.ForeignKey(
        VolunteerTeam, on_delete=models.CASCADE, related_name="shifts"
    )
    title = models.CharField(
        max_length=150, help_text="e.g. Registration desk, Saturday morning"
    )
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    location = models.CharField(
        max_length=200, blank=True, help_text="Where to physically stand."
    )
    capacity = models.IntegerField(
        default=1, help_text="How many volunteers this shift needs."
    )
    notes = models.TextField(
        blank=True, help_text="Shown to the volunteers on this shift."
    )

    class Meta:
        ordering = ["starts_at", "team__display_order", "title"]
        indexes = [models.Index(fields=["conference_year", "starts_at"])]

    def __str__(self):
        return f"{self.title} ({self.starts_at:%a %-d %b %H:%M})"

    def clean(self):
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "A shift has to end after it starts."})
        if self.capacity is not None and self.capacity < 1:
            raise ValidationError({"capacity": "A shift needs at least one volunteer."})

    @property
    def assigned_count(self):
        return self.assignments.count()

    @property
    def remaining(self):
        return max(self.capacity - self.assigned_count, 0)

    @property
    def is_full(self):
        return self.assigned_count >= self.capacity

    @property
    def is_understaffed(self):
        return self.assigned_count < self.capacity

    @property
    def duration_hours(self):
        return round((self.ends_at - self.starts_at).total_seconds() / 3600, 1)

    def overlaps(self, other):
        """Whether two shifts share any time. Touching end-to-start does not."""
        return self.starts_at < other.ends_at and other.starts_at < self.ends_at


class ShiftAssignment(models.Model):
    """
    One volunteer on one shift, and whether they turned up.

    ``attended_at`` is the thing a certificate rests on, and it is separate from
    the assignment on purpose: being rostered and being there are different facts,
    and only one of them is worth attesting to.
    """

    shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name="assignments")
    application = models.ForeignKey(
        VolunteerApplication,
        on_delete=models.CASCADE,
        related_name="shift_assignments",
    )
    assigned_at = models.DateTimeField(auto_now_add=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    attended_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set when the volunteer actually worked the shift.",
    )

    class Meta:
        ordering = ["shift__starts_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["shift", "application"], name="one_assignment_per_shift"
            )
        ]

    def __str__(self):
        return f"{self.application.full_name} on {self.shift.title}"

    @property
    def attended(self):
        return self.attended_at is not None
