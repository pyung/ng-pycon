"""
Travel Grant application form.
"""

from decimal import Decimal

from django import forms

from .models import TravelGrantApplication, TravelGrantPayment, EMPLOYMENT_CHOICES

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
TAILWIND_NUMBER = TAILWIND_INPUT


class TravelGrantApplicationForm(forms.ModelForm):
    """Full application form for Sections A-F."""

    class Meta:
        model = TravelGrantApplication
        fields = [
            "grant_type",
            "is_speaking",
            "country_of_residence",
            "city",
            "gender",
            "gender_self_described",
            "passport_required",
            "first_time_pycon",
            "community_involvement",
            "financial_need_reason",
            "employment_status",
            "is_student",
            "estimated_transport_cost",
            "estimated_accommodation_cost",
            "other_funding_sources",
            "other_funding_details",
            "community_impact",
            "commit_to_share_learnings",
            "confirm_accurate",
            "agree_to_refund",
        ]
        labels = {
            "grant_type": "What are you asking for?",
            "is_speaking": "I am also speaking at this conference",
            "gender": "Gender (optional)",
            "gender_self_described": "How would you describe it?",
        }
        help_texts = {
            "grant_type": (
                "Pick the one that matches your costs below. It is used to filter the "
                "review queue and to report how the money was spent."
            ),
            "is_speaking": (
                "Tick if you have a talk accepted or submitted. It does not decide "
                "anything on its own — it tells the panel what else you have on."
            ),
            "gender": (
                "Optional, and it plays no part in any decision. We are asked for the "
                "breakdown by the sponsors and the Python Software Foundation who fund "
                "these grants, and we cannot report what we never asked."
            ),
        }
        widgets = {
            "grant_type": forms.Select(attrs={"class": TAILWIND_SELECT}),
            "is_speaking": forms.CheckboxInput(attrs={"class": TAILWIND_CHECKBOX}),
            "gender": forms.Select(attrs={"class": TAILWIND_SELECT}),
            "gender_self_described": forms.TextInput(attrs={
                "class": TAILWIND_INPUT,
                "placeholder": "In your own words",
            }),
            "country_of_residence": forms.TextInput(attrs={
                "class": TAILWIND_INPUT,
                "placeholder": "e.g. Nigeria",
            }),
            "city": forms.TextInput(attrs={
                "class": TAILWIND_INPUT,
                "placeholder": "e.g. Lagos",
            }),
            "passport_required": forms.CheckboxInput(attrs={"class": TAILWIND_CHECKBOX}),
            "first_time_pycon": forms.CheckboxInput(attrs={"class": TAILWIND_CHECKBOX}),
            "community_involvement": forms.Textarea(attrs={
                "class": TAILWIND_TEXTAREA,
                "rows": 4,
                "placeholder": "Describe your involvement in the Python community...",
            }),
            "financial_need_reason": forms.Textarea(attrs={
                "class": TAILWIND_TEXTAREA,
                "rows": 5,
                "placeholder": "Why do you need financial assistance to attend?",
            }),
            "employment_status": forms.Select(
                choices=EMPLOYMENT_CHOICES,
                attrs={"class": TAILWIND_SELECT},
            ),
            "is_student": forms.CheckboxInput(attrs={"class": TAILWIND_CHECKBOX}),
            "estimated_transport_cost": forms.NumberInput(attrs={
                "class": TAILWIND_NUMBER,
                "min": "0",
                "step": "0.01",
                "placeholder": "0.00",
            }),
            "estimated_accommodation_cost": forms.NumberInput(attrs={
                "class": TAILWIND_NUMBER,
                "min": "0",
                "step": "0.01",
                "placeholder": "0.00",
            }),
            "other_funding_sources": forms.CheckboxInput(attrs={"class": TAILWIND_CHECKBOX}),
            "other_funding_details": forms.Textarea(attrs={
                "class": TAILWIND_TEXTAREA,
                "rows": 3,
                "placeholder": "Details of other funding (optional)",
            }),
            "community_impact": forms.Textarea(attrs={
                "class": TAILWIND_TEXTAREA,
                "rows": 5,
                "placeholder": "How will attending PyCon benefit your community?",
            }),
            "commit_to_share_learnings": forms.CheckboxInput(attrs={"class": TAILWIND_CHECKBOX}),
            "confirm_accurate": forms.CheckboxInput(attrs={"class": TAILWIND_CHECKBOX}),
            "agree_to_refund": forms.CheckboxInput(attrs={"class": TAILWIND_CHECKBOX}),
        }

    def __init__(self, *args, submit_action=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.submit_action = submit_action
        # Nobody should have to state a gender to be considered for a grant.
        self.fields["gender"].required = False
        self.fields["gender"].choices = [("", "Prefer not to say")] + [
            choice
            for choice in TravelGrantApplication.GENDER_CHOICES
            if choice[0] != TravelGrantApplication.GENDER_UNDISCLOSED
        ]

    def clean(self):
        data = super().clean()
        if self.submit_action:
            if not data.get("confirm_accurate"):
                self.add_error("confirm_accurate", "You must confirm the information is accurate.")
            if not data.get("agree_to_refund"):
                self.add_error("agree_to_refund", "You must agree to refund if information is misrepresented.")

        # The costs and the grant type have to agree, because the type is what the
        # review queue filters on and the reports report. Caught here rather than
        # left to a reviewer noticing.
        grant_type = data.get("grant_type")
        transport = data.get("estimated_transport_cost") or 0
        accommodation = data.get("estimated_accommodation_cost") or 0
        if self.submit_action and grant_type:
            if grant_type == TravelGrantApplication.GRANT_TYPE_TRAVEL and not transport:
                self.add_error(
                    "estimated_transport_cost",
                    "You asked for travel only, so please give a transport estimate.",
                )
            if (
                grant_type == TravelGrantApplication.GRANT_TYPE_ACCOMMODATION
                and not accommodation
            ):
                self.add_error(
                    "estimated_accommodation_cost",
                    "You asked for accommodation only, so please give an accommodation estimate.",
                )
            if grant_type == TravelGrantApplication.GRANT_TYPE_BOTH and not (
                transport and accommodation
            ):
                self.add_error(
                    "grant_type",
                    "You asked for both, but only one estimate is filled in. Change the "
                    "type or add the other estimate.",
                )

        if (
            data.get("gender") == TravelGrantApplication.GENDER_SELF_DESCRIBE
            and not (data.get("gender_self_described") or "").strip()
        ):
            self.add_error(
                "gender_self_described",
                "Add a description, or choose one of the other options.",
            )
        return data

class GrantReviewForm(forms.Form):
    """Review scores and comments."""

    need_score = forms.IntegerField(
        min_value=1, max_value=5,
        widget=forms.NumberInput(attrs={
            "class": TAILWIND_INPUT, "min": "1", "max": "5",
        }),
    )
    impact_score = forms.IntegerField(
        min_value=1, max_value=5,
        widget=forms.NumberInput(attrs={
            "class": TAILWIND_INPUT, "min": "1", "max": "5",
        }),
    )
    contribution_score = forms.IntegerField(
        min_value=1, max_value=5,
        widget=forms.NumberInput(attrs={
            "class": TAILWIND_INPUT, "min": "1", "max": "5",
        }),
    )
    diversity_score = forms.IntegerField(
        min_value=1, max_value=5,
        widget=forms.NumberInput(attrs={
            "class": TAILWIND_INPUT, "min": "1", "max": "5",
        }),
    )
    comments = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": TAILWIND_TEXTAREA, "rows": 4,
        }),
    )

class GrantAssignReviewerForm(forms.Form):
    application_ids = forms.CharField(widget=forms.HiddenInput())
    reviewer_ids = forms.CharField(widget=forms.HiddenInput())

    def clean_application_ids(self):
        raw = self.cleaned_data["application_ids"]
        return [x.strip() for x in raw.split(",") if x.strip()]

    def clean_reviewer_ids(self):
        raw = self.cleaned_data["reviewer_ids"]
        return [int(x.strip()) for x in raw.split(",") if x.strip()]


class GrantBulkDecisionForm(forms.Form):
    DECISION_CHOICES = [
        ("approve", "Approve"),
        ("reject", "Reject"),
        ("waitlist", "Waitlist"),
    ]
    application_ids = forms.CharField(widget=forms.HiddenInput())
    decision = forms.ChoiceField(choices=DECISION_CHOICES, widget=forms.Select(attrs={"class": TAILWIND_SELECT}))

    def clean_application_ids(self):
        raw = self.cleaned_data["application_ids"]
        return [x.strip() for x in raw.split(",") if x.strip()]


class GrantPaymentForm(forms.Form):
    """Finance: mark payment status."""
    payment_status = forms.ChoiceField(
        choices=TravelGrantPayment.STATUS_CHOICES,
        widget=forms.Select(attrs={"class": TAILWIND_SELECT}),
    )
    amount_paid = forms.DecimalField(
        required=False,
        widget=forms.NumberInput(attrs={"class": TAILWIND_INPUT, "step": "0.01"}),
    )
    reference = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Transaction reference"}),
    )
    receipt = forms.FileField(required=False)


class GrantOfferResponseForm(forms.Form):
    """
    The recipient answering an offer.

    A decline asks for a reason and does not require one. It is worth asking because
    the answers are actionable -- "the amount would not cover the flight" is a
    different problem from "I can no longer come" -- and worth not requiring because
    somebody withdrawing should not have to justify it.
    """

    reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": TAILWIND_TEXTAREA, "rows": 3}),
        label="Anything you would like to tell us? (optional)",
        help_text=(
            "If the amount was not enough, or your plans changed, saying so helps us "
            "set these better next year."
        ),
    )


class GrantPayoutDetailsForm(forms.ModelForm):
    """Where to send the money, filled in by the recipient."""

    class Meta:
        model = TravelGrantApplication
        fields = ["payout_method", "bank_name", "account_name", "account_number", "payout_notes"]
        labels = {
            "payout_method": "How should we send it?",
            "bank_name": "Bank",
            "account_name": "Account name",
            "account_number": "Account number",
            "payout_notes": "Anything else we need to know",
        }
        help_texts = {
            "account_name": "Exactly as your bank holds it, or the transfer will bounce.",
            "payout_notes": "Optional. A sort code, a different currency, a preferred date.",
        }
        widgets = {
            "payout_method": forms.Select(attrs={"class": TAILWIND_SELECT}),
            "bank_name": forms.TextInput(attrs={"class": TAILWIND_INPUT}),
            "account_name": forms.TextInput(attrs={"class": TAILWIND_INPUT}),
            "account_number": forms.TextInput(attrs={"class": TAILWIND_INPUT}),
            "payout_notes": forms.Textarea(attrs={"class": TAILWIND_TEXTAREA, "rows": 2}),
        }

    def clean(self):
        data = super().clean()
        if data.get("payout_method") == TravelGrantApplication.PAYOUT_BANK_TRANSFER:
            for field, label in (
                ("bank_name", "bank"),
                ("account_name", "account name"),
                ("account_number", "account number"),
            ):
                if not (data.get(field) or "").strip():
                    self.add_error(field, f"A bank transfer needs the {label}.")
        return data


class GrantReceiptForm(forms.Form):
    """A recipient uploading evidence of what they spent."""

    receipt = forms.FileField(
        label="Receipt",
        help_text=(
            "A photo or a PDF is fine. One file — if you have several, combine "
            "them or send the rest to us by email."
        ),
        widget=forms.ClearableFileInput(attrs={"class": TAILWIND_INPUT}),
    )

    #: Big enough for a phone photo of a boarding pass, small enough that nobody
    #: uploads a video by accident.
    MAX_BYTES = 8 * 1024 * 1024
    ALLOWED_SUFFIXES = (".pdf", ".png", ".jpg", ".jpeg", ".heic", ".webp")

    def clean_receipt(self):
        receipt = self.cleaned_data["receipt"]
        name = (receipt.name or "").lower()
        if not name.endswith(self.ALLOWED_SUFFIXES):
            raise forms.ValidationError(
                "Upload a PDF or a photo (" + ", ".join(self.ALLOWED_SUFFIXES) + ")."
            )
        if receipt.size > self.MAX_BYTES:
            raise forms.ValidationError(
                f"That file is {receipt.size / 1024 / 1024:.1f} MB. Please keep it "
                f"under {self.MAX_BYTES // 1024 // 1024} MB."
            )
        return receipt
