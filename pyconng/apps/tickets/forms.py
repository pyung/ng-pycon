"""
Forms for buying a ticket and for what happens to it afterwards.

The purchase form carries most of the weight, and most of the rules. It has to
refuse a quantity above the per-order cap, refuse a type whose sale window is shut,
apply a coupon only to the types it covers, and record what the discount actually
was rather than leaving it to be recomputed later from terms that may have changed.
All of that happens before anybody is sent to Paystack, because the alternative is
taking money for a ticket that cannot be issued.
"""

from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import (
    Coupon,
    Ticket,
    TicketSale,
    TicketSettings,
    TicketType,
)

User = get_user_model()

TAILWIND_INPUT = (
    "w-full px-4 py-2 border border-gray-300 rounded-lg "
    "focus:outline-none focus:ring-2 focus:ring-teal-500 focus:border-transparent"
)
TAILWIND_SELECT = (
    "w-full px-4 py-2 border border-gray-300 rounded-lg bg-white "
    "focus:outline-none focus:ring-2 focus:ring-teal-500 focus:border-transparent"
)
TAILWIND_CHECKBOX = "w-4 h-4 text-teal-600 border-gray-300 rounded focus:ring-teal-500"


class PurchaseForm(forms.Form):
    """
    Quantities per ticket type, plus a coupon, company details and any verification.

    Fields are built from the types actually on sale, so a type whose window has
    closed has no field at all and a posted quantity for it is ignored rather than
    honoured.
    """

    coupon = forms.CharField(required=False)

    # Only asked for when the buyer says the ticket is for a company. Optional
    # throughout: an individual buyer should not meet a wall of billing fields.
    company_name = forms.CharField(max_length=200, required=False)
    company_address = forms.CharField(required=False, widget=forms.Textarea)
    buyer_tax_id = forms.CharField(max_length=60, required=False)
    purchase_order_reference = forms.CharField(max_length=100, required=False)

    #: Whatever a concession rate asked the buyer to supply.
    verification_reference = forms.CharField(max_length=300, required=False)

    def __init__(self, *args, conference_year=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.conference_year = conference_year
        self.user = user
        self.available = {}
        if conference_year:
            for ticket_type in TicketType.objects.on_sale().for_year(conference_year):
                self.available[ticket_type.name] = ticket_type
                self.fields[ticket_type.name] = forms.IntegerField(
                    required=False, min_value=0, initial=0
                )

    # --- validation --------------------------------------------------------

    def clean(self):
        cleaned = super().clean()
        for name, ticket_type in self.available.items():
            quantity = cleaned.get(name) or 0
            if quantity <= 0:
                continue
            if quantity > ticket_type.max_per_order:
                self.add_error(
                    name,
                    f"At most {ticket_type.max_per_order} {ticket_type.name} tickets "
                    f"per order. Write to us for a larger group.",
                )
            elif quantity > ticket_type.remaining_count:
                # Checked here as well as at the door of the sale, because stock
                # moves between loading the page and pressing the button.
                remaining = ticket_type.remaining_count
                self.add_error(
                    name,
                    f"Only {remaining} {ticket_type.name} ticket"
                    f"{'' if remaining == 1 else 's'} left."
                    if remaining > 0
                    else f"{ticket_type.name} has sold out.",
                )

        if self.selected_tickets() and self._verification_needed() and not (
            cleaned.get("verification_reference") or ""
        ).strip():
            prompts = ", ".join(
                t.verification_prompt for t in self._types_needing_verification()
            )
            self.add_error(
                "verification_reference",
                f"This rate has to be checked. Please supply: {prompts}",
            )
        return cleaned

    def _types_needing_verification(self):
        return [
            ticket_type
            for name, ticket_type in self.available.items()
            if ticket_type.requires_verification and (self.cleaned_data.get(name) or 0) > 0
        ]

    def _verification_needed(self):
        return bool(self._types_needing_verification())

    def selected_tickets(self):
        """Names of ticket types with a quantity above zero."""
        return [
            name
            for name in self.available
            if (self.cleaned_data.get(name) or 0) > 0
        ]

    def get_coupon(self):
        """
        The coupon, if the code is one this buyer may use right now.

        Returns None rather than raising: a mistyped code should not lose the whole
        order, and the page tells them the discount was not applied.
        """
        code = (self.cleaned_data.get("coupon") or "").strip()
        if not code:
            return None
        coupon = Coupon.objects.filter(
            code__iexact=code, conference_year=self.conference_year
        ).first()
        if coupon and coupon.usable_by(self.user):
            return coupon
        return None

    # --- saving ------------------------------------------------------------

    def save(self, user):
        """
        Create the order rows. Returns ``(first_ticket, total)``, or ``(None, 0)``.

        Prices and discounts are written onto each row as they are now. Nothing here
        reads a price again later, which is what stops a ticket bought at the
        early-bird rate quietly repricing when that rate ends.
        """
        selected = self.selected_tickets()
        coupon = self.get_coupon()

        # Clear any half-finished order for this user and year: they are starting again.
        Ticket.objects.filter(
            status=Ticket.ISSUED, user=user, conference_year=self.conference_year
        ).delete()

        tickets = []
        for name in selected:
            ticket_type = self.available[name]
            quantity = self.cleaned_data[name]
            unit_price = Decimal(ticket_type.current_price)
            subtotal = unit_price * quantity

            discount = Decimal("0")
            if coupon is not None and coupon.applies_to(ticket_type):
                discount = coupon.discount_for(subtotal)

            ticket = Ticket.objects.create(
                order=_new_order_code(),
                user=user,
                ticket_type=ticket_type,
                quantity=quantity,
                amount=subtotal - discount,
                discount_amount=discount,
                coupon=coupon if discount > 0 else None,
                conference_year=self.conference_year,
                status=Ticket.ISSUED,
                verification_status=(
                    Ticket.VERIFICATION_PENDING
                    if ticket_type.requires_verification
                    else Ticket.VERIFICATION_NOT_REQUIRED
                ),
                verification_reference=(
                    self.cleaned_data.get("verification_reference", "").strip()
                    if ticket_type.requires_verification
                    else ""
                ),
            )
            tickets.append(ticket)

        if not tickets:
            return None, 0

        first = tickets[0]
        order_codes = [t.pk for t in tickets]
        Ticket.objects.filter(pk__in=order_codes).update(
            related=first.pk, multiple_tickets=len(tickets) > 1
        )
        total = sum(t.amount for t in tickets)
        self._billing_for = first
        return Ticket.objects.get(pk=first.pk), total

    def billing_details(self):
        """The company fields, for an invoice. Empty dict for an individual buyer."""
        return {
            "company_name": (self.cleaned_data.get("company_name") or "").strip(),
            "company_address": (self.cleaned_data.get("company_address") or "").strip(),
            "buyer_tax_id": (self.cleaned_data.get("buyer_tax_id") or "").strip(),
            "purchase_order_reference": (
                self.cleaned_data.get("purchase_order_reference") or ""
            ).strip(),
        }


def _new_order_code():
    from .utils import generate_order_code

    return generate_order_code()


class TicketCreateForm(forms.Form):
    """
    Attendee details for one place.

    ``attendee_email`` is what makes a group purchase work: the buyer names each
    colleague, and the place is linked to that colleague's account whether it
    already exists or is created later.
    """

    full_name = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Full name"}),
    )
    attendee_email = forms.EmailField(
        required=False,
        widget=forms.EmailInput(
            attrs={"class": TAILWIND_INPUT, "placeholder": "their.email@example.com"}
        ),
        help_text=(
            "Leave blank if this place is yours. Otherwise the ticket appears in "
            "their account -- they do not need one yet."
        ),
    )
    tagline = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(
            attrs={"class": TAILWIND_INPUT, "placeholder": "e.g. Python Developer"}
        ),
    )
    diet = forms.ChoiceField(
        choices=TicketSale.DIET_CHOICES,
        widget=forms.Select(attrs={"class": TAILWIND_SELECT}),
    )

    def save(self, ticket):
        """
        Create the places for this order, if they do not exist yet.

        Every place gets the same details, which is right for one person buying one
        ticket and a starting point for a group: the buyer edits each place
        afterwards, and only then does it need its own name.
        """
        if ticket.created_tickets:
            return ticket
        email = (self.cleaned_data.get("attendee_email") or "").strip()
        owner = ticket.user
        if email and email.lower() != (ticket.user.email or "").lower():
            owner = User.objects.filter(email__iexact=email).first()

        for _ in range(ticket.quantity):
            TicketSale.objects.create(
                ticket=ticket,
                user=owner,
                attendee_email=email or (ticket.user.email or ""),
                full_name=self.cleaned_data["full_name"],
                diet=self.cleaned_data["diet"],
                tagline=self.cleaned_data.get("tagline", ""),
            )
        ticket.created_tickets = True
        ticket.save(update_fields=["created_tickets"])
        return ticket


class TicketEditForm(forms.ModelForm):
    """Editing one place: who it is for, and what they eat."""

    class Meta:
        model = TicketSale
        fields = ["full_name", "attendee_email", "diet", "tagline"]
        labels = {"attendee_email": "Attendee email"}
        help_texts = {
            "attendee_email": (
                "Changing this moves the ticket to that person's account when they "
                "have one. Use Transfer below to hand it over for good."
            )
        }
        widgets = {
            "full_name": forms.TextInput(
                attrs={"class": TAILWIND_INPUT, "placeholder": "Full name"}
            ),
            "attendee_email": forms.EmailInput(
                attrs={"class": TAILWIND_INPUT, "placeholder": "their.email@example.com"}
            ),
            "diet": forms.Select(attrs={"class": TAILWIND_SELECT}),
            "tagline": forms.TextInput(
                attrs={"class": TAILWIND_INPUT, "placeholder": "e.g. Python Developer"}
            ),
        }

    def save(self, commit=True):
        sale = super().save(commit=False)
        email = (sale.attendee_email or "").strip()
        if email:
            # Re-point at the matching account if there is one; leave it unclaimed
            # otherwise, and the signal links it when they sign up.
            match = User.objects.filter(email__iexact=email).first()
            sale.user = match
        if commit:
            sale.save()
        return sale


class TicketTransferForm(forms.Form):
    """
    Hand a place to somebody else, for good.

    Unlike editing the attendee email, a transfer is refused once the edition's
    cutoff has passed -- badges are printed from these names, and a name that
    changes after printing is a person who cannot get in.
    """

    email = forms.EmailField(
        widget=forms.EmailInput(
            attrs={"class": TAILWIND_INPUT, "placeholder": "Recipient's email address"}
        ),
        help_text="They do not need an account yet. The ticket appears when they make one.",
    )

    def __init__(self, *args, ticket_sale=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.ticket_sale = ticket_sale

    def clean(self):
        cleaned = super().clean()
        if self.ticket_sale is None:
            return cleaned
        year = self.ticket_sale.ticket.conference_year
        policy = TicketSettings.for_year(year)
        if not policy.transfers_open:
            raise forms.ValidationError(
                "Transfers for this edition closed on "
                f"{policy.transfer_deadline:%-d %B %Y}. Write to us if you are stuck."
            )
        return cleaned

    def clean_email(self):
        email = self.cleaned_data["email"].strip()
        if self.ticket_sale is not None:
            current = (self.ticket_sale.owner_email or "").lower()
            if email.lower() == current:
                raise forms.ValidationError("That is already the holder of this ticket.")
        return email

    def save(self, ticket_sale=None):
        """Move the place, record where it came from, and tell the recipient."""
        sale = ticket_sale or self.ticket_sale
        email = self.cleaned_data["email"]
        previous_email = sale.owner_email

        sale.transferred_from_email = previous_email or ""
        sale.transferred_at = timezone.now()
        sale.attendee_email = email
        sale.user = User.objects.filter(email__iexact=email).first()
        sale.save()

        from emails.services import send_email, site_url

        send_email(
            template="tickets/transfer",
            to=[email],
            subject="PyCon Nigeria - Ticket Transfer",
            context={
                "old_owner_email": previous_email,
                "ticket_type": sale.ticket_type_name,
                "dashboard_url": f"{site_url()}/tickets/",
            },
            tags=["tickets", "transfer"],
            conference_year=sale.ticket.conference_year,
        )
        return sale


class WaitlistForm(forms.Form):
    """One field, because somebody just told them the ticket is gone."""

    email = forms.EmailField(
        widget=forms.EmailInput(
            attrs={"class": TAILWIND_INPUT, "placeholder": "you@example.com"}
        ),
        label="Email address",
    )
    ticket_type = forms.ModelChoiceField(
        queryset=TicketType.objects.none(),
        required=False,
        empty_label="Any ticket type",
        widget=forms.Select(attrs={"class": TAILWIND_SELECT}),
    )

    def __init__(self, *args, conference_year=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.conference_year = conference_year
        if conference_year:
            self.fields["ticket_type"].queryset = (
                TicketType.objects.active().purchasable().for_year(conference_year)
            )


class RefundRequestForm(forms.Form):
    """A buyer asking for their money back."""

    reason = forms.CharField(
        widget=forms.Textarea(attrs={"class": TAILWIND_INPUT, "rows": 4}),
        label="Why are you asking for a refund?",
        help_text="A sentence is plenty. It is read by a person, not a rule.",
    )

    def clean_reason(self):
        reason = (self.cleaned_data.get("reason") or "").strip()
        if len(reason) < 5:
            raise forms.ValidationError("Please say a little more.")
        return reason


class InvoiceDetailsForm(forms.Form):
    """Company details for an invoice, filled in after the purchase if need be."""

    company_name = forms.CharField(
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={"class": TAILWIND_INPUT}),
        help_text="Leave blank for an invoice in your own name.",
    )
    company_address = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": TAILWIND_INPUT, "rows": 3}),
    )
    buyer_tax_id = forms.CharField(
        max_length=60,
        required=False,
        widget=forms.TextInput(attrs={"class": TAILWIND_INPUT}),
        label="Tax identification number",
    )
    purchase_order_reference = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={"class": TAILWIND_INPUT}),
        label="Purchase order reference",
    )
