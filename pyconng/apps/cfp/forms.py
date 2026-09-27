import re

from django import forms
from django.core.validators import MinValueValidator, MaxValueValidator

from editions.current import current_year

from .models import Proposal, Review, Speaker, Track

# ---------------------------------------------------------------------------
# Shared Tailwind widget classes (consistent with the tickets app)
# ---------------------------------------------------------------------------

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
TAILWIND_CHECKBOX = (
    "w-4 h-4 text-teal-600 border-gray-300 rounded focus:ring-teal-500"
)


# ---------------------------------------------------------------------------
# Speaker info (collected alongside every submission)
# ---------------------------------------------------------------------------

class SpeakerForm(forms.Form):
    """
    Speaker details for one edition. The email address is not collected here --
    it comes from the signed-in account, so there is only one copy of it.
    """

    full_name = forms.CharField(
        max_length=200,
        widget=forms.TextInput(attrs={
            "class": TAILWIND_INPUT,
            "placeholder": "Full name",
        }),
    )
    bio = forms.CharField(
        widget=forms.Textarea(attrs={
            "class": TAILWIND_TEXTAREA,
            "rows": 4,
            "placeholder": "Tell us about yourself…",
        }),
    )
    organisation = forms.CharField(
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={
            "class": TAILWIND_INPUT,
            "placeholder": "Organisation (optional)",
        }),
    )
    country = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={
            "class": TAILWIND_INPUT,
            "placeholder": "Country",
        }),
    )
    photo = forms.ImageField(
        required=False,
        help_text="A headshot for the programme. JPEG or PNG, ideally square.",
        widget=forms.ClearableFileInput(attrs={"class": TAILWIND_INPUT, "accept": "image/*"}),
    )
    photo_alt = forms.CharField(
        max_length=200,
        required=False,
        help_text="Describe the photo for screen readers. Defaults to your name.",
        widget=forms.TextInput(attrs={"class": TAILWIND_INPUT}),
    )
    first_time_speaker = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": TAILWIND_CHECKBOX}),
        label="First-time speaker?",
    )


# ---------------------------------------------------------------------------
# Proposal form (used for both create and edit)
# ---------------------------------------------------------------------------

class ProposalForm(forms.ModelForm):
    class Meta:
        model = Proposal
        fields = [
            "title",
            "abstract",
            "description",
            "track",
            "format",
            "duration",
            "audience_level",
            "prior_delivery",
            "prior_delivery_link",
            "slides_url",
            "special_requirements",
            "notes_to_reviewers",
        ]
        widgets = {
            "title": forms.TextInput(attrs={
                "class": TAILWIND_INPUT,
                "placeholder": "Talk title",
            }),
            "abstract": forms.Textarea(attrs={
                "class": TAILWIND_TEXTAREA,
                "rows": 4,
                "placeholder": "Public abstract (shown to attendees)",
            }),
            "description": forms.Textarea(attrs={
                "class": TAILWIND_TEXTAREA,
                "rows": 6,
                "placeholder": "Detailed description (for reviewers only)",
            }),
            "notes_to_reviewers": forms.Textarea(attrs={
                "class": TAILWIND_TEXTAREA,
                "rows": 3,
                "placeholder": "Anything reviewers should know. Never shown publicly.",
            }),
            "track": forms.Select(attrs={"class": TAILWIND_SELECT}),
            "format": forms.Select(attrs={"class": TAILWIND_SELECT}),
            "audience_level": forms.Select(attrs={"class": TAILWIND_SELECT}),
            "prior_delivery": forms.CheckboxInput(attrs={"class": TAILWIND_CHECKBOX}),
            "prior_delivery_link": forms.URLInput(attrs={
                "class": TAILWIND_INPUT,
                "placeholder": "https://... (optional)",
            }),
            "slides_url": forms.URLInput(attrs={
                "class": TAILWIND_INPUT,
                "placeholder": "https://... (optional)",
            }),
            "special_requirements": forms.Textarea(attrs={
                "class": TAILWIND_TEXTAREA,
                "rows": 3,
                "placeholder": "Any special requirements (optional)",
            }),
        }

    def __init__(self, *args, cfp_settings=None, **kwargs):
        super().__init__(*args, **kwargs)

        # Limit tracks to active ones for the current year
        self.fields["track"].queryset = Track.objects.filter(
            conference_year=current_year(), is_active=True,
        ).order_by("display_order", "name")

        # Replace duration IntegerField with a select based on allowed values
        if cfp_settings and cfp_settings.allowed_durations:
            durations = cfp_settings.allowed_durations
        else:
            durations = [5, 20, 45, 90]

        self.fields["duration"] = forms.TypedChoiceField(
            coerce=int,
            choices=[(d, f"{d} minutes") for d in sorted(durations)],
            widget=forms.Select(attrs={"class": TAILWIND_SELECT}),
        )


# ---------------------------------------------------------------------------
# Review form (reviewer scores a proposal)
# ---------------------------------------------------------------------------

def _dimension_field(label, help_text):
    return forms.IntegerField(
        min_value=1,
        max_value=5,
        label=label,
        help_text=help_text,
        widget=forms.NumberInput(attrs={
            "class": TAILWIND_INPUT, "min": "1", "max": "5", "placeholder": "1-5",
        }),
    )


class ReviewForm(forms.Form):
    """
    Four dimensions rather than one mark, so two reviewers who both say "4" can
    be seen to disagree about why.
    """

    relevance = _dimension_field(
        "Relevance", "How much this audience wants this talk.",
    )
    clarity = _dimension_field(
        "Clarity", "How clearly the proposal is written and scoped.",
    )
    depth = _dimension_field(
        "Depth", "Substance: is there something real here.",
    )
    speaker_readiness = _dimension_field(
        "Speaker readiness", "Confidence they can deliver it well.",
    )
    comments = forms.CharField(
        widget=forms.Textarea(attrs={
            "class": TAILWIND_TEXTAREA,
            "rows": 5,
            "placeholder": "Your internal review comments…",
        }),
    )


# ---------------------------------------------------------------------------
# Chair: bulk decisions
# ---------------------------------------------------------------------------

class BulkDecisionForm(forms.Form):
    DECISION_CHOICES = [
        ("accept", "Accept"),
        ("reject", "Reject"),
        ("waitlist", "Waitlist"),
    ]

    proposal_ids = forms.CharField(
        widget=forms.HiddenInput(),
        help_text="Comma-separated proposal UUIDs",
    )
    decision = forms.ChoiceField(
        choices=DECISION_CHOICES,
        widget=forms.Select(attrs={"class": TAILWIND_SELECT}),
    )

    def clean_proposal_ids(self):
        raw = self.cleaned_data["proposal_ids"]
        ids = [pid.strip() for pid in raw.split(",") if pid.strip()]
        if not ids:
            raise forms.ValidationError("No proposals selected.")
        return ids


# ---------------------------------------------------------------------------
# Chair: assign reviewers
# ---------------------------------------------------------------------------

class AssignReviewerForm(forms.Form):
    """Used via the admin assign view to bulk-assign reviewers."""

    proposal_ids = forms.CharField(
        widget=forms.HiddenInput(),
        help_text="Comma-separated proposal UUIDs",
    )
    reviewer_ids = forms.CharField(
        widget=forms.HiddenInput(),
        help_text="Comma-separated reviewer profile IDs",
    )

    def clean_proposal_ids(self):
        raw = self.cleaned_data["proposal_ids"]
        return [pid.strip() for pid in raw.split(",") if pid.strip()]

    def clean_reviewer_ids(self):
        raw = self.cleaned_data["reviewer_ids"]
        return [int(rid.strip()) for rid in raw.split(",") if rid.strip()]


# ---------------------------------------------------------------------------
# Chair: send bulk emails
# ---------------------------------------------------------------------------

class BulkEmailForm(forms.Form):
    EMAIL_TYPE_CHOICES = [
        ("acceptance", "Acceptance"),
        ("rejection", "Rejection"),
        ("waitlist", "Waitlist"),
    ]

    template_type = forms.ChoiceField(
        choices=EMAIL_TYPE_CHOICES,
        widget=forms.Select(attrs={"class": TAILWIND_SELECT}),
    )
    proposal_ids = forms.CharField(
        widget=forms.HiddenInput(),
        help_text="Comma-separated proposal UUIDs",
    )

    def clean_proposal_ids(self):
        raw = self.cleaned_data["proposal_ids"]
        return [pid.strip() for pid in raw.split(",") if pid.strip()]


# ---------------------------------------------------------------------------
# Co-speakers
# ---------------------------------------------------------------------------

CO_SPEAKER_LINE = re.compile(r"^\s*(?P<name>[^<>]*?)\s*<\s*(?P<email>[^<>\s]+@[^<>\s]+)\s*>\s*$")


class CoSpeakerForm(forms.Form):
    """
    Co-speakers, one per line as ``Name <email@example.com>``.

    A textarea rather than a formset: submitters overwhelmingly have one or two
    co-speakers and already know this notation from their mail client, and it
    survives a page reload without JavaScript.
    """

    co_speakers = forms.CharField(
        required=False,
        label="Co-speakers",
        help_text=(
            "One per line, as Name <email@example.com>. They do not need an account "
            "yet — we will invite them, and it links up when they sign up."
        ),
        widget=forms.Textarea(attrs={
            "class": TAILWIND_TEXTAREA,
            "rows": 3,
            "placeholder": "Ada Obi <ada@example.com>",
        }),
    )

    def clean_co_speakers(self):
        raw = self.cleaned_data.get("co_speakers", "")
        entries, problems, seen = [], [], set()
        for number, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                continue
            match = CO_SPEAKER_LINE.match(line)
            if not match:
                problems.append(
                    f"Line {number}: write it as Name <email@example.com>."
                )
                continue
            email = match.group("email").strip().lower()
            if email in seen:
                problems.append(f"Line {number}: {email} is listed twice.")
                continue
            seen.add(email)
            entries.append((email, match.group("name").strip()))
        if problems:
            raise forms.ValidationError(problems)
        return entries

    @staticmethod
    def initial_for(proposal):
        """Render existing co-speakers back into the textarea notation."""
        return "\n".join(
            f"{c.display_name} <{c.email}>" for c in proposal.co_speakers.all()
        )


# ---------------------------------------------------------------------------
# Speaker onboarding (after a talk is confirmed)
# ---------------------------------------------------------------------------

class SpeakerOnboardingForm(forms.ModelForm):
    class Meta:
        model = Speaker
        fields = [
            "full_name", "bio", "organisation", "country",
            "photo", "photo_alt",
            "tshirt_size", "dietary_requirements",
            "travel_support_needed", "accessibility_needs",
        ]
        widgets = {
            "full_name": forms.TextInput(attrs={"class": TAILWIND_INPUT}),
            "bio": forms.Textarea(attrs={"class": TAILWIND_TEXTAREA, "rows": 4}),
            "organisation": forms.TextInput(attrs={"class": TAILWIND_INPUT}),
            "country": forms.TextInput(attrs={"class": TAILWIND_INPUT}),
            "photo_alt": forms.TextInput(attrs={"class": TAILWIND_INPUT}),
            "tshirt_size": forms.Select(attrs={"class": TAILWIND_SELECT}),
            "dietary_requirements": forms.TextInput(attrs={"class": TAILWIND_INPUT}),
            "accessibility_needs": forms.Textarea(attrs={"class": TAILWIND_TEXTAREA, "rows": 3}),
        }
