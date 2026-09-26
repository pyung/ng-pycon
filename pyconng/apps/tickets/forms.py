from django import forms
from django.contrib.auth import get_user_model

from .models import Coupon, Ticket, TicketSale, TicketType

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
    Dynamic form that accepts ticket quantities for each active ticket type.
    Fields are created dynamically based on active TicketType records.
    """

    coupon = forms.CharField(required=False)

    def __init__(self, *args, conference_year=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.conference_year = conference_year
        if conference_year:
            ticket_types = TicketType.objects.active().for_year(conference_year)
            for tt in ticket_types:
                self.fields[tt.name] = forms.IntegerField(
                    required=False,
                    min_value=0,
                    initial=0,
                )

    def selected_tickets(self):
        """Return names of ticket types with quantity > 0."""
        return [
            name
            for name, value in self.cleaned_data.items()
            if name != "coupon" and value and int(value) > 0
        ]

    def get_coupon(self):
        """Look up a valid coupon by code."""
        code = self.cleaned_data.get("coupon", "").strip()
        if not code:
            return None
        coupon = Coupon.objects.filter(
            code__iexact=code,
            expired=False,
            conference_year=self.conference_year,
        ).first()
        if coupon and coupon.is_valid:
            return coupon
        return None

    def save(self, user):
        """Create Ticket records for each selected ticket type."""
        selected = self.selected_tickets()
        tickets = []
        coupon = self.get_coupon()

        # Clear any existing issued tickets for this user and year
        Ticket.objects.filter(
            status=Ticket.ISSUED,
            user=user,
            conference_year=self.conference_year,
        ).delete()

        for ticket_name in selected:
            ticket = Ticket.create(
                user=user,
                ticket_name=ticket_name,
                conference_year=self.conference_year,
            )
            ticket.quantity = self.cleaned_data[ticket_name]
            ticket_type = TicketType.objects.get(
                name=ticket_name,
                conference_year=self.conference_year,
            )
            ticket.ticket_type = ticket_type

            if ticket_type.current_price:
                discount = coupon.percentage if coupon else 0
                ticket.amount = (
                    ticket.quantity * ticket_type.current_price * (100 - discount) / 100
                )
                if coupon:
                    ticket.coupon = coupon
                ticket.save()
                tickets.append(ticket)

        if not tickets:
            return None, 0

        # Mark as multi-ticket purchase if multiple types selected
        if len(tickets) > 1:
            Ticket.objects.filter(pk__in=[t.pk for t in tickets]).update(
                multiple_tickets=True
            )

        # Group tickets together via the 'related' field
        Ticket.objects.filter(pk__in=[t.pk for t in tickets]).update(
            related=tickets[0].pk
        )

        first_ticket = Ticket.objects.get(pk=tickets[0].pk)
        total = first_ticket.get_total()
        return first_ticket, total


class TicketCreateForm(forms.Form):
    """Form for assigning attendee details to a purchased ticket."""

    full_name = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={
            "class": TAILWIND_INPUT,
            "placeholder": "Full name",
        }),
    )
    tagline = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={
            "class": TAILWIND_INPUT,
            "placeholder": "e.g. Python Developer, Data Scientist",
        }),
    )
    diet = forms.ChoiceField(
        choices=TicketSale.DIET_CHOICES,
        widget=forms.Select(attrs={"class": TAILWIND_SELECT}),
    )

    def save(self, ticket):
        """Create TicketSale records for the ticket if not already created."""
        if not ticket.created_tickets:
            return ticket.create_sales(**self.cleaned_data)
        return ticket


class TicketEditForm(forms.ModelForm):
    """Form for editing an individual ticket sale's attendee info."""

    class Meta:
        model = TicketSale
        fields = ["full_name", "diet", "tagline"]
        widgets = {
            "full_name": forms.TextInput(attrs={
                "class": TAILWIND_INPUT,
                "placeholder": "Full name",
            }),
            "diet": forms.Select(attrs={"class": TAILWIND_SELECT}),
            "tagline": forms.TextInput(attrs={
                "class": TAILWIND_INPUT,
                "placeholder": "e.g. Python Developer",
            }),
        }

    def save(self, **kwargs):
        return super().save(commit=kwargs.get("commit", True))


class TicketTransferForm(forms.Form):
    """Form for transferring a ticket to another user."""

    email = forms.EmailField(
        widget=forms.EmailInput(attrs={
            "class": TAILWIND_INPUT,
            "placeholder": "Recipient's email address",
        }),
    )

    def clean_email(self):
        email = self.cleaned_data["email"]
        if not User.objects.filter(email=email).exists():
            raise forms.ValidationError(
                "No account found with this email. The recipient must have an account first."
            )
        return email

    def save(self, ticket_sale):
        """Transfer the ticket sale to another user."""
        email = self.cleaned_data["email"]
        old_owner = ticket_sale.user
        new_owner = User.objects.get(email=email)
        ticket_sale.user = new_owner
        ticket_sale.save()

        # Notify the new owner
        ticket_type = ticket_sale.ticket_type_name
        from emails.services import send_email, site_url

        send_email(
            template="tickets/transfer",
            to=[email],
            subject="PyCon Nigeria - Ticket Transfer",
            context={
                "old_owner_email": old_owner.email,
                "ticket_type": ticket_type,
                "dashboard_url": f"{site_url()}/tickets/",
            },
            tags=["tickets", "transfer"],
            fail_silently=True,
        )
        return ticket_sale
