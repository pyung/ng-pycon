"""
The volunteer application form, and the coordinator's side of it.

Three answers are required: their name, which teams they would work in, and why.
Everything else is optional, because a form that interrogates somebody offering free
labour is a form they abandon. The fields that look like interrogation -- emergency
contact, accessibility needs -- are asked with the reason stated, which is the
difference between a form and a questionnaire.

Availability is built from the edition's own dates plus the set-up and pack-down
days on the settings, so moving the conference moves the question.
"""

from django import forms
from django.utils import timezone

from .models import (
    Shift,
    VolunteerApplication,
    VolunteerAvailability,
    VolunteerSettings,
    VolunteerTeam,
)

INPUT = (
    "w-full px-4 py-3 border border-gray-300 rounded-lg "
    "focus:outline-none focus:ring-2 focus:ring-teal-500 focus:border-transparent"
)
SELECT = INPUT + " bg-white"
CHECKBOX = "w-4 h-4 text-teal-600 border-gray-300 rounded focus:ring-teal-500"


class VolunteerApplicationForm(forms.ModelForm):
    """Applying, or editing an application that has not been decided yet."""

    class Meta:
        model = VolunteerApplication
        fields = [
            "full_name",
            "phone",
            "teams",
            "experience",
            "experience_detail",
            "motivation",
            "skills",
            "tshirt_size",
            "dietary_requirements",
            "accessibility_needs",
            "emergency_contact_name",
            "emergency_contact_phone",
        ]
        labels = {
            "full_name": "Your name",
            "phone": "Phone number",
            "teams": "Which teams would you be happy to work in?",
            "experience": "Have you volunteered at an event before?",
            "experience_detail": "Anything you want to add",
            "motivation": "Why would you like to help?",
            "skills": "Anything useful we should know about",
            "tshirt_size": "T-shirt size",
            "dietary_requirements": "Dietary requirements",
            "accessibility_needs": "Is there anything you need to work comfortably?",
            "emergency_contact_name": "Emergency contact name",
            "emergency_contact_phone": "Emergency contact phone",
        }
        help_texts = {
            "phone": "Only used on the day, so your team lead can reach you.",
            "motivation": "A few sentences. It is read by a person before any decision.",
            "skills": (
                "Languages you speak, first aid, AV, photography, sign language "
                "— anything that might change which team suits you."
            ),
            "tshirt_size": "Volunteers get a shirt. Blank means we will ask later.",
            "accessibility_needs": (
                "Asked so we can put you on the right shift rather than adjust one "
                "afterwards. Standing for long stretches, quiet spaces, anything at all."
            ),
            "emergency_contact_name": (
                "Asked because you will be on your feet in a building all day. Only "
                "used if something happens."
            ),
        }
        widgets = {
            "full_name": forms.TextInput(attrs={"class": INPUT, "autocomplete": "name"}),
            "phone": forms.TextInput(attrs={"class": INPUT, "autocomplete": "tel"}),
            "teams": forms.CheckboxSelectMultiple(attrs={"class": CHECKBOX}),
            "experience": forms.RadioSelect(attrs={"class": CHECKBOX}),
            "experience_detail": forms.Textarea(attrs={"class": INPUT, "rows": 2}),
            "motivation": forms.Textarea(attrs={"class": INPUT, "rows": 5}),
            "skills": forms.TextInput(attrs={"class": INPUT}),
            "tshirt_size": forms.Select(attrs={"class": SELECT}),
            "dietary_requirements": forms.TextInput(attrs={"class": INPUT}),
            "accessibility_needs": forms.Textarea(attrs={"class": INPUT, "rows": 3}),
            "emergency_contact_name": forms.TextInput(attrs={"class": INPUT}),
            "emergency_contact_phone": forms.TextInput(attrs={"class": INPUT, "autocomplete": "tel"}),
        }

    #: The availability grid: one boolean field per day and period, named
    #: avail_<isodate>_<period>. Built here rather than stored as a single
    #: multiple-choice field so each day can carry its own label in the template.
    AVAILABILITY_PREFIX = "avail"

    def __init__(self, *args, conference_year=None, settings_obj=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.conference_year = conference_year
        self.settings_obj = settings_obj or VolunteerSettings.for_year(conference_year)

        self.fields["teams"].queryset = VolunteerTeam.objects.filter(
            conference_year=conference_year, is_active=True
        )
        # Not required, so it cannot become a fourth thing standing between somebody
        # and offering to help. The model's default covers a blank answer.
        self.fields["experience"].required = False
        self.fields["teams"].help_text = (
            f"Pick up to {self.settings_obj.max_team_choices}. "
            f"We will put you on one of them."
        )

        self.availability_days = self.settings_obj.availability_days()
        for day, label in self.availability_days:
            for period, period_label in VolunteerAvailability.PERIOD_CHOICES:
                self.fields[self.availability_field(day, period)] = forms.BooleanField(
                    required=False,
                    label=f"{label} {day:%a %-d %b} {period_label}",
                    widget=forms.CheckboxInput(attrs={"class": CHECKBOX}),
                )

        if self.instance.pk:
            held = {
                (slot.day, slot.period) for slot in self.instance.availability.all()
            }
            for day, _ in self.availability_days:
                for period, _label in VolunteerAvailability.PERIOD_CHOICES:
                    if (day, period) in held:
                        self.initial[self.availability_field(day, period)] = True

    @classmethod
    def availability_field(cls, day, period):
        return f"{cls.AVAILABILITY_PREFIX}_{day.isoformat()}_{period}"

    def availability_grid(self):
        """``[(day, label, [bound fields])]`` so the template can lay out a table."""
        rows = []
        for day, label in self.availability_days:
            fields = [
                self[self.availability_field(day, period)]
                for period, _ in VolunteerAvailability.PERIOD_CHOICES
            ]
            rows.append((day, label, fields))
        return rows

    def selected_availability(self):
        """``[(day, period)]`` the applicant ticked."""
        chosen = []
        for day, _ in self.availability_days:
            for period, _label in VolunteerAvailability.PERIOD_CHOICES:
                if self.cleaned_data.get(self.availability_field(day, period)):
                    chosen.append((day, period))
        return chosen

    def clean_experience(self):
        return self.cleaned_data.get("experience") or VolunteerApplication.EXPERIENCE_NONE

    def clean_teams(self):
        teams = self.cleaned_data.get("teams")
        limit = self.settings_obj.max_team_choices
        if teams is not None and limit and len(teams) > limit:
            raise forms.ValidationError(
                f"Please pick at most {limit} team{'' if limit == 1 else 's'}, so we "
                f"can place you somewhere you actually want to be."
            )
        return teams

    def clean_motivation(self):
        motivation = (self.cleaned_data.get("motivation") or "").strip()
        if len(motivation) < 15:
            raise forms.ValidationError(
                "Please say a little more — a sentence or two is plenty."
            )
        return motivation

    def clean(self):
        cleaned = super().clean()
        # Only warned about, not enforced: somebody who genuinely has no free slots
        # should still be able to tell us, and a coordinator can then ask.
        if self.availability_days and not self.selected_availability():
            self.add_error(
                None,
                "Please tick at least one time you could work. If none of these "
                "suit, apply anyway and say so in the box above — we will ask.",
            )
        return cleaned

    def save(self, commit=True):
        application = super().save(commit=commit)
        if commit:
            self.save_availability(application)
        return application

    def save_availability(self, application):
        """
        Replace the availability rows with what was ticked.

        Replaced rather than merged: the form shows the complete picture, so an
        unticked box means "not any more", and merging would make it impossible to
        take a day back.
        """
        chosen = set(self.selected_availability())
        existing = {
            (slot.day, slot.period): slot for slot in application.availability.all()
        }
        for key, slot in existing.items():
            if key not in chosen:
                slot.delete()
        for day, period in chosen:
            if (day, period) not in existing:
                VolunteerAvailability.objects.create(
                    application=application, day=day, period=period
                )


class VolunteerDecisionForm(forms.Form):
    """The coordinator deciding one application, and placing them."""

    DECISION_CHOICES = [
        (VolunteerApplication.STATUS_ACCEPTED, "Accept"),
        (VolunteerApplication.STATUS_WAITLISTED, "Waitlist"),
        (VolunteerApplication.STATUS_NOT_SELECTED, "Not selected"),
        (VolunteerApplication.STATUS_UNDER_REVIEW, "Mark as under review"),
    ]

    decision = forms.ChoiceField(
        choices=DECISION_CHOICES, widget=forms.RadioSelect(attrs={"class": CHECKBOX})
    )
    team = forms.ModelChoiceField(
        queryset=VolunteerTeam.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": SELECT}),
        help_text="Required to accept somebody. Defaults to that team's lead.",
    )
    lead = forms.ModelChoiceField(
        queryset=None,
        required=False,
        widget=forms.Select(attrs={"class": SELECT}),
        label="Lead (if not the team's own)",
    )
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": INPUT, "rows": 3}),
        label="Note to the applicant",
        help_text=(
            "Sent with the decision. A rejection with a sentence in it is somebody "
            "you can ask again next year."
        ),
    )
    internal_note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": INPUT, "rows": 2}),
        label="Internal note",
        help_text="Never shown to the applicant.",
    )

    def __init__(self, *args, application=None, **kwargs):
        super().__init__(*args, **kwargs)
        from django.contrib.auth import get_user_model

        self.application = application
        year = application.conference_year if application else None
        self.fields["team"].queryset = VolunteerTeam.objects.filter(
            conference_year=year, is_active=True
        )
        self.fields["lead"].queryset = get_user_model().objects.filter(
            is_active=True
        ).order_by("email")
        if application is not None:
            self.fields["team"].initial = application.assigned_team_id
            self.fields["lead"].initial = application.assigned_lead_id

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("decision") == VolunteerApplication.STATUS_ACCEPTED:
            team = cleaned.get("team") or (
                self.application.assigned_team if self.application else None
            )
            if team is None:
                self.add_error(
                    "team",
                    "Pick a team. An accepted volunteer with no team has nobody to "
                    "report to and will ask you which one.",
                )
        return cleaned


class ShiftForm(forms.ModelForm):
    """Creating or editing one shift."""

    class Meta:
        model = Shift
        fields = ["team", "title", "starts_at", "ends_at", "location", "capacity", "notes"]
        widgets = {
            "team": forms.Select(attrs={"class": SELECT}),
            "title": forms.TextInput(attrs={"class": INPUT}),
            "starts_at": forms.DateTimeInput(
                attrs={"class": INPUT, "type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
            "ends_at": forms.DateTimeInput(
                attrs={"class": INPUT, "type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
            "location": forms.TextInput(attrs={"class": INPUT}),
            "capacity": forms.NumberInput(attrs={"class": INPUT, "min": 1}),
            "notes": forms.Textarea(attrs={"class": INPUT, "rows": 2}),
        }

    def __init__(self, *args, conference_year=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.conference_year = conference_year
        self.fields["team"].queryset = VolunteerTeam.objects.filter(
            conference_year=conference_year, is_active=True
        )

    def save(self, commit=True):
        shift = super().save(commit=False)
        if self.conference_year:
            shift.conference_year = self.conference_year
        if commit:
            shift.save()
        return shift
