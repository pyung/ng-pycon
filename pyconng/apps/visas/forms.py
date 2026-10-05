"""
The request form, and the officer's decision forms.

The passport fields carry an unusual amount of help text for a reason: the single
most common way an invitation letter fails is a name that does not match the
passport. A consular officer comparing "Mike Jesudare" on the letter with
"JESUDARE, Michael Oluwaseun" in the passport has grounds to refuse, and the
applicant finds out weeks later at an appointment they waited a month for. So the
form asks for the passport's own spelling, field by field, and says why.
"""

from datetime import timedelta

from django import forms
from django.utils import timezone

from .models import VisaLetterRequest

TAILWIND_INPUT = (
    "w-full px-4 py-2 border border-gray-300 rounded-lg "
    "focus:outline-none focus:ring-2 focus:ring-teal-500 focus:border-transparent"
)
TAILWIND_SELECT = (
    "w-full px-4 py-2 border border-gray-300 rounded-lg bg-white "
    "focus:outline-none focus:ring-2 focus:ring-teal-500 focus:border-transparent"
)
TAILWIND_TEXTAREA = (
    "w-full px-4 py-2 border border-gray-300 rounded-lg "
    "focus:outline-none focus:ring-2 focus:ring-teal-500 focus:border-transparent "
    "resize-y"
)

#: Most consulates want six months' passport validity beyond the trip. Warning at
#: submission is worth more than a refusal at the embassy.
VALIDITY_MONTHS = 6


class DateInput(forms.DateInput):
    input_type = "date"


class VisaLetterRequestForm(forms.ModelForm):
    """Everything the letter needs, and nothing it does not."""

    confirm_matches_passport = forms.BooleanField(
        required=True,
        label="I have copied these details from my passport exactly as printed",
        help_text=(
            "Including the order of names, hyphens and spellings. A mismatch between "
            "the letter and the passport is the usual reason a letter is rejected at "
            "the embassy."
        ),
        widget=forms.CheckboxInput(
            attrs={"class": "w-4 h-4 text-teal-600 border-gray-300 rounded focus:ring-teal-500"}
        ),
    )

    class Meta:
        model = VisaLetterRequest
        fields = [
            "passport_surname",
            "passport_given_names",
            "nationality",
            "passport_number",
            "passport_expiry",
            "date_of_birth",
            "embassy_city",
            "embassy_country",
            "arrival_on",
            "departure_on",
            "accommodation",
            "notes",
        ]
        widgets = {
            "passport_surname": forms.TextInput(attrs={"class": TAILWIND_INPUT}),
            "passport_given_names": forms.TextInput(attrs={"class": TAILWIND_INPUT}),
            "nationality": forms.TextInput(attrs={"class": TAILWIND_INPUT}),
            "passport_number": forms.TextInput(
                attrs={"class": TAILWIND_INPUT, "autocomplete": "off", "spellcheck": "false"}
            ),
            "passport_expiry": DateInput(attrs={"class": TAILWIND_INPUT}),
            "date_of_birth": DateInput(attrs={"class": TAILWIND_INPUT}),
            "embassy_city": forms.TextInput(attrs={"class": TAILWIND_INPUT}),
            "embassy_country": forms.TextInput(attrs={"class": TAILWIND_INPUT}),
            "arrival_on": DateInput(attrs={"class": TAILWIND_INPUT}),
            "departure_on": DateInput(attrs={"class": TAILWIND_INPUT}),
            "accommodation": forms.Textarea(attrs={"class": TAILWIND_TEXTAREA, "rows": 3}),
            "notes": forms.Textarea(attrs={"class": TAILWIND_TEXTAREA, "rows": 3}),
        }
        labels = {
            "passport_surname": "Surname, as in your passport",
            "passport_given_names": "Given names, as in your passport",
            "passport_number": "Passport number",
            "passport_expiry": "Passport expiry date",
            "date_of_birth": "Date of birth",
            "embassy_city": "City of the embassy or high commission you will apply at",
            "embassy_country": "Country",
            "arrival_on": "Expected arrival in Nigeria",
            "departure_on": "Expected departure",
            "accommodation": "Where you expect to stay",
            "notes": "Anything else we should know",
        }
        help_texts = {
            "passport_surname": "The surname line of the passport, not the name you go by.",
            "passport_given_names": "All given names, in the order the passport prints them.",
            "nationality": "As shown in the passport.",
            "passport_number": (
                "Stored only until shortly after the conference, then deleted "
                "automatically. Only the visa officer can read it."
            ),
            "accommodation": (
                "A hotel name and city is enough. Leave blank if you have not booked "
                "yet — it is not required, though consulates often ask."
            ),
            "notes": (
                "For example, a name on your passport that differs from your account, "
                "or an appointment date you are working towards."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        required = [
            "passport_surname", "passport_given_names", "nationality",
            "passport_number", "passport_expiry", "date_of_birth",
            "embassy_city", "embassy_country", "arrival_on", "departure_on",
        ]
        for name in required:
            self.fields[name].required = True
        # An already-submitted request does not re-ask the confirmation: it was
        # given once, and re-asking on every correction reads as distrust.
        if self.instance.pk and self.instance.status != VisaLetterRequest.STATUS_DRAFT:
            self.fields["confirm_matches_passport"].required = False
            self.fields["confirm_matches_passport"].initial = True

    def clean_passport_number(self):
        """
        Squash spaces and upper-case it. A passport number is printed in groups and
        typed inconsistently, and an embassy matching it character by character does
        not care that we stored the spaces faithfully.
        """
        value = (self.cleaned_data.get("passport_number") or "").strip()
        return "".join(value.split()).upper()

    def clean_date_of_birth(self):
        value = self.cleaned_data.get("date_of_birth")
        if value and value >= timezone.localdate():
            raise forms.ValidationError("That date is in the future.")
        return value

    def clean(self):
        cleaned = super().clean()
        arrival = cleaned.get("arrival_on")
        departure = cleaned.get("departure_on")
        expiry = cleaned.get("passport_expiry")

        if arrival and departure and departure < arrival:
            self.add_error("departure_on", "Departure cannot be before arrival.")

        if expiry and expiry <= timezone.localdate():
            self.add_error("passport_expiry", "This passport has already expired.")
        elif expiry and departure:
            # Six months beyond the trip is the usual requirement. Said as a warning
            # on the field rather than a refusal, because the rule varies by mission
            # and we should not be the ones who block a valid application.
            needed = departure + timedelta(days=VALIDITY_MONTHS * 30)
            if expiry < needed:
                self.add_error(
                    "passport_expiry",
                    (
                        f"Most consulates ask for {VALIDITY_MONTHS} months' validity "
                        "beyond your return date, and yours has less. Renew the "
                        "passport first — a letter with an old number is no use "
                        "once you have a new one."
                    ),
                )
        return cleaned


class VisaDecisionForm(forms.Form):
    """Approve or reject one request."""

    DECISION_APPROVE = "approve"
    DECISION_REJECT = "reject"
    DECISION_CHOICES = [
        (DECISION_APPROVE, "Approve and issue the letter"),
        (DECISION_REJECT, "Reject"),
    ]

    decision = forms.ChoiceField(
        choices=DECISION_CHOICES,
        widget=forms.RadioSelect,
    )
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": TAILWIND_TEXTAREA, "rows": 3}),
        label="Message to the applicant",
        help_text=(
            "Required when rejecting. Most rejections are fixable — say what to fix."
        ),
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("decision") == self.DECISION_REJECT and not (
            cleaned.get("note") or ""
        ).strip():
            self.add_error(
                "note",
                "Say why. The applicant can usually fix it, and a bare refusal "
                "leaves them nothing to act on.",
            )
        return cleaned


class VerificationForm(forms.Form):
    """
    The public check: reference plus surname.

    Two fields rather than one, so the endpoint cannot be walked to enumerate who
    has been issued a letter. A consular officer holding the letter has both.
    """

    reference = forms.CharField(
        max_length=32,
        label="Reference on the letter",
        widget=forms.TextInput(
            attrs={
                "class": TAILWIND_INPUT,
                "placeholder": "PYNG-2027-XXXXXX",
                "autocomplete": "off",
                "spellcheck": "false",
            }
        ),
    )
    surname = forms.CharField(
        max_length=100,
        label="Surname of the person named on the letter",
        widget=forms.TextInput(attrs={"class": TAILWIND_INPUT, "autocomplete": "off"}),
    )

    def clean_reference(self):
        return (self.cleaned_data.get("reference") or "").strip().upper()
