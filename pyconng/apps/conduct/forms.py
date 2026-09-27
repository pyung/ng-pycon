"""
The reporting form.

Only the description is required. Everything else is optional because the person
filling this in may be upset, may be in a corridor on their phone, and may be
reporting somebody with power over them. A form that insists on a name is a form
that gets abandoned, and an abandoned report is worse than a thin one.
"""

from django import forms

from .models import IncidentReport

#: Short enough to be a mis-click, long enough that a real report clears it easily.
MINIMUM_DESCRIPTION = 20

FIELD_CLASS = (
    "w-full px-4 py-3 border border-gray-300 rounded-lg "
    "focus:outline-none focus:ring-2 focus:ring-teal-500 focus:border-transparent"
)


class IncidentReportForm(forms.ModelForm):
    #: Bots fill in every field they find. A real person never sees this one, so
    #: anything in it means the submission is not a person. Named plausibly on
    #: purpose -- "honeypot" in the markup defeats the point.
    website = forms.CharField(
        required=False,
        label="Website",
        widget=forms.TextInput(
            attrs={"tabindex": "-1", "autocomplete": "off", "aria-hidden": "true"}
        ),
    )

    class Meta:
        model = IncidentReport
        fields = [
            "is_anonymous",
            "reporter_name",
            "reporter_email",
            "incident_when",
            "incident_where",
            "people_involved",
            "description",
            "witnesses",
            "desired_outcome",
            "reported_elsewhere",
        ]
        labels = {
            "is_anonymous": "Report anonymously",
            "reporter_name": "Your name",
            "reporter_email": "Your email address",
            "incident_when": "When did it happen?",
            "incident_where": "Where did it happen?",
            "people_involved": "Who was involved?",
            "description": "What happened?",
            "witnesses": "Was anyone else there?",
            "desired_outcome": "What would you like to happen?",
            "reported_elsewhere": "I have already told someone else about this",
        }
        help_texts = {
            "is_anonymous": (
                "We will not ask for your name or email, and nothing will link this "
                "report to your account. You will still get a reference number, but "
                "we will have no way to reach you."
            ),
            "reporter_email": "So we can tell you what we have done. We will not share it.",
            "incident_when": "A day and a rough time is plenty.",
            "incident_where": "A room, the venue, or an online channel.",
            "people_involved": "Names if you know them, a description if you do not.",
            "description": "In your own words. There is no wrong way to write this.",
            "witnesses": "Only if you are comfortable saying.",
            "desired_outcome": "Optional, and it will be read before anything is decided.",
        }
        widgets = {
            "reporter_name": forms.TextInput(attrs={"class": FIELD_CLASS, "autocomplete": "name"}),
            "reporter_email": forms.EmailInput(attrs={"class": FIELD_CLASS, "autocomplete": "email"}),
            "incident_when": forms.TextInput(attrs={"class": FIELD_CLASS}),
            "incident_where": forms.TextInput(attrs={"class": FIELD_CLASS}),
            "people_involved": forms.Textarea(attrs={"class": FIELD_CLASS, "rows": 3}),
            "description": forms.Textarea(attrs={"class": FIELD_CLASS, "rows": 8}),
            "witnesses": forms.Textarea(attrs={"class": FIELD_CLASS, "rows": 2}),
            "desired_outcome": forms.Textarea(attrs={"class": FIELD_CLASS, "rows": 3}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        # A signed-in reporter should not have to retype what we already know, but
        # the fields stay editable: they may be reporting on somebody else's behalf.
        if user is not None and getattr(user, "pk", None):
            self.fields["reporter_email"].initial = user.email
            full_name = (user.get_full_name() or "").strip()
            if full_name:
                self.fields["reporter_name"].initial = full_name

    def clean_description(self):
        description = (self.cleaned_data.get("description") or "").strip()
        if len(description) < MINIMUM_DESCRIPTION:
            raise forms.ValidationError(
                "Please say a little more, so the team has something to act on."
            )
        return description

    def clean_website(self):
        if (self.cleaned_data.get("website") or "").strip():
            # Deliberately vague: telling a bot which check it failed helps it.
            raise forms.ValidationError("This form could not be submitted.")
        return ""

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("is_anonymous"):
            # Honour it here as well as in the model, so a name typed before the
            # box was ticked is dropped rather than saved and then blanked.
            cleaned["reporter_name"] = ""
            cleaned["reporter_email"] = ""
        elif not (cleaned.get("reporter_email") or "").strip():
            self.add_error(
                "reporter_email",
                "Give us an email address, or tick “Report anonymously” above.",
            )
        return cleaned

    def report_data(self):
        """Cleaned data without the honeypot, ready for the model."""
        return {
            field: self.cleaned_data.get(field)
            for field in self.Meta.fields
        }
