"""
Ticketing: what is on sale, who bought it, and what happens afterwards.

The purchase and payment path was already working. What this module adds is
everything around the sale -- sale windows, fixed-amount coupons, invoices,
refunds, a waitlist, and a check-in code per attendee -- plus the machinery for
issuing a ticket to somebody who should not pay for one: speakers, volunteers,
grant recipients and a sponsor's allocation.

Two ideas run through it. A ticket is an *order*; a ``TicketSale`` is one
attendee's place, and that is the thing with a name on it, a QR code and a
check-in. And money is never recomputed from a price that may since have
changed: what was charged, what was discounted and what was refunded are each
recorded when they happen.
"""

import logging
import secrets

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from editions.validators import validate_edition_year

from .utils import generate_order_code

#: Characters for a code a person may have to read aloud at a door, or type.
#: One of each confusable pair only -- the same reasoning as a Code of Conduct
#: reference. Also entirely within QR alphanumeric mode, which keeps the code's
#: QR small; see qr.py.
CODE_ALPHABET = "ABCDEFGHJKMNPRSTUVWXY23479"

logger = logging.getLogger(__name__)

#: Why a ticket type cannot be bought, in words. Beside the state itself so every
#: page naming it -- the ticket page, the purchase page, the homepage -- says the
#: same thing. An empty string means there is nothing to explain.
SALE_STATE_LABELS = {
    "on_sale": "",
    "not_yet": "Not on sale yet",
    "closed": "Sales closed",
    "sold_out": "Sold out",
    "invite_only": "By invitation",
    "inactive": "",
}


class TicketSettings(models.Model):
    """
    Ticketing policy for one edition.

    The deadlines and the policy text belong to the edition, not to the code: a
    transfer cutoff moves, a refund policy gets reworded, and neither should wait
    for a deploy. One row per year, created on demand.
    """

    conference_year = models.IntegerField(unique=True, validators=[validate_edition_year])

    transfer_deadline = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Last moment a ticket can be passed to someone else. Badges are printed "
            "from attendee names, so this normally sits a few days before the event. "
            "Blank means transfers stay open."
        ),
    )
    refund_deadline = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Last moment a refund can be requested. Blank means no refunds are "
            "offered, which is also a policy -- say so in the text below."
        ),
    )
    refund_policy = models.TextField(
        blank=True,
        help_text=(
            "Shown on the purchase page and the ticket page. Write what you will "
            "actually do, including any fee retained."
        ),
    )
    refund_fee_percentage = models.IntegerField(
        default=0,
        help_text="Percentage retained on a refund, for the amount suggested to the organizer.",
    )

    waitlist_enabled = models.BooleanField(
        default=True,
        help_text="Offer a waitlist when a ticket type sells out.",
    )

    checkin_opens_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Before this, a check-in scan is refused. Blank means any time.",
    )

    # --- Who the invoice is from -------------------------------------------
    invoice_prefix = models.CharField(
        max_length=12,
        default="PYNG",
        help_text="Prefix for invoice numbers, e.g. PYNG gives PYNG-2027-0001.",
    )
    invoice_from_name = models.CharField(
        max_length=200,
        blank=True,
        help_text="The legal name that appears as the seller on an invoice.",
    )
    invoice_from_address = models.TextField(blank=True)
    invoice_tax_id = models.CharField(
        max_length=60,
        blank=True,
        help_text="Your tax identification number, if invoices need to carry one.",
    )
    invoice_notes = models.TextField(
        blank=True,
        help_text="Standing footnote on every invoice, e.g. how to reference a payment.",
    )

    class Meta:
        verbose_name = "ticket settings"
        verbose_name_plural = "ticket settings"
        ordering = ["-conference_year"]

    def __str__(self):
        return f"Ticket settings {self.conference_year}"

    @classmethod
    def for_year(cls, year):
        """
        The settings for ``year``, created with defaults if absent.

        Never returns None: every caller here would otherwise need the same
        "if settings is None" branch, and the defaults are the safe policy
        (transfers open, no refunds offered, waitlist on).
        """
        obj, _ = cls.objects.get_or_create(conference_year=year)
        return obj

    @property
    def transfers_open(self):
        return self.transfer_deadline is None or timezone.now() <= self.transfer_deadline

    @property
    def refunds_open(self):
        return self.refund_deadline is not None and timezone.now() <= self.refund_deadline

    @property
    def checkin_open(self):
        return self.checkin_opens_at is None or timezone.now() >= self.checkin_opens_at


class Coupon(models.Model):
    """
    A discount code.

    Carries either a percentage or a fixed amount off, has a window of its own,
    and can be restricted to particular ticket types -- which is what makes a
    sponsor code usable: it takes the whole price off one type and nothing off
    any other.
    """

    PERCENTAGE = "percentage"
    FIXED = "fixed"
    DISCOUNT_CHOICES = [
        (PERCENTAGE, "Percentage off"),
        (FIXED, "Fixed amount off"),
    ]

    code = models.CharField(max_length=50, unique=True)
    discount_type = models.CharField(
        max_length=20, choices=DISCOUNT_CHOICES, default=PERCENTAGE
    )
    percentage = models.IntegerField(
        default=5, help_text="Used when the type is a percentage. 5 means 5% off."
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Used when the type is a fixed amount. Never takes a total below zero.",
    )
    max_usage = models.IntegerField(default=1, help_text="Maximum number of times this coupon can be used")
    max_per_user = models.IntegerField(
        default=0,
        help_text="Times one person may use it. 0 means no per-person limit.",
    )
    ticket_types = models.ManyToManyField(
        "tickets.TicketType",
        blank=True,
        related_name="coupons",
        help_text="Leave empty to apply to every ticket type.",
    )
    valid_from = models.DateTimeField(
        null=True, blank=True, help_text="Blank means valid immediately."
    )
    valid_until = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Blank means no expiry date. Set this rather than relying on somebody "
            "remembering to tick the box below."
        ),
    )
    expired = models.BooleanField(
        default=False,
        help_text="A manual kill switch, independent of the dates above.",
    )
    conference_year = models.IntegerField(
        validators=[validate_edition_year],
        help_text="Conference year this coupon is valid for",
    )
    description = models.CharField(
        max_length=200,
        blank=True,
        help_text="What this code is for, so a stray code can be recognised later.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.code} ({self.discount_display})"

    def clean(self):
        if self.valid_from and self.valid_until and self.valid_from > self.valid_until:
            raise ValidationError(
                {"valid_until": "The end of the window comes before its start."}
            )
        if self.discount_type == self.PERCENTAGE and not 0 < self.percentage <= 100:
            raise ValidationError(
                {"percentage": "A percentage discount must be between 1 and 100."}
            )
        if self.discount_type == self.FIXED and self.amount <= 0:
            raise ValidationError(
                {"amount": "A fixed discount needs an amount above zero."}
            )

    @property
    def discount_display(self):
        if self.discount_type == self.FIXED:
            return f"{self.amount:,.0f} off"
        return f"{self.percentage}% off"

    @property
    def usage_count(self):
        return self.ticket_usages.count()

    @property
    def window_open(self):
        now = timezone.now()
        if self.valid_from and now < self.valid_from:
            return False
        if self.valid_until and now > self.valid_until:
            return False
        return True

    @property
    def is_valid(self):
        """Valid for somebody, right now. Per-user and per-type limits are separate."""
        return (
            not self.expired
            and self.window_open
            and self.usage_count < self.max_usage
        )

    def applies_to(self, ticket_type):
        """True when this code covers ``ticket_type``. No restriction means all."""
        if ticket_type is None:
            return False
        if not self.pk:
            return True
        restricted = self.ticket_types.all()
        if not restricted:
            return True
        return any(t.pk == ticket_type.pk for t in restricted)

    def usable_by(self, user):
        """Whether ``user`` has any uses of this code left."""
        if not self.is_valid:
            return False
        if self.max_per_user <= 0:
            return True
        if user is None or not getattr(user, "pk", None):
            return True
        used = self.ticket_usages.filter(user=user).count()
        return used < self.max_per_user

    def discount_for(self, subtotal):
        """
        What this code takes off ``subtotal``. Never more than the subtotal, and
        never negative -- a fixed discount larger than the order makes it free,
        not a refund.
        """
        from decimal import Decimal

        subtotal = Decimal(subtotal or 0)
        if subtotal <= 0:
            return Decimal("0")
        if self.discount_type == self.FIXED:
            return min(Decimal(self.amount), subtotal)
        return (subtotal * Decimal(self.percentage) / Decimal(100)).quantize(
            Decimal("0.01")
        )


class TicketTypeQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def for_year(self, year):
        return self.filter(conference_year=year)

    def purchasable(self):
        """
        Types the public may buy. Excludes the invitation-only ones, which exist
        to be issued -- a sponsor's allocation, a speaker's comp -- and would
        otherwise appear on the purchase page at a price of nothing.
        """
        return self.filter(availability=TicketType.PUBLIC)

    def on_sale(self, when=None):
        """
        Types whose sale window is open right now.

        Done in the database rather than by filtering in Python so the purchase
        page stays one query, and so `is_on_sale` and this agree by construction.
        """
        when = when or timezone.now()
        return (
            self.active()
            .purchasable()
            .filter(Q(sales_start_at__isnull=True) | Q(sales_start_at__lte=when))
            .filter(Q(sales_end_at__isnull=True) | Q(sales_end_at__gte=when))
        )

    def with_tickets_purchased(self):
        return self.annotate(
            purchased_count=models.Sum(
                "tickets__quantity",
                filter=models.Q(tickets__status=Ticket.PAID),
            )
        )


class TicketType(models.Model):
    """
    One thing that can be bought, or issued.

    Inventory is two counts: an early-bird allocation and a regular one. Early
    bird ends when either its count runs out or its date passes, whichever comes
    first -- a count alone silently keeps the cheap price alive for months if
    sales are slow, and a date alone gives it away to more people than budgeted.
    """

    PUBLIC = "public"
    INVITE = "invite"
    AVAILABILITY_CHOICES = [
        (PUBLIC, "On sale to the public"),
        (INVITE, "Issued only, never shown for sale"),
    ]

    name = models.CharField(max_length=100, help_text="e.g. Student, Personal, Company, Patron")
    description = models.TextField(blank=True, help_text="Description of what this ticket includes")
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Regular price")
    early_bird_price = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, help_text="Early bird price (0 = no early bird)"
    )
    early_bird_count = models.IntegerField(default=0, help_text="Number of early bird tickets available")
    early_bird_ends_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Early bird also ends at this moment, whether or not the allocation has "
            "run out. Blank means the count alone decides."
        ),
    )
    regular_count = models.IntegerField(default=0, help_text="Number of regular tickets available")
    conference_year = models.IntegerField(
        validators=[validate_edition_year],
        help_text="Conference year this ticket type belongs to",
    )
    is_active = models.BooleanField(default=True)
    availability = models.CharField(
        max_length=20,
        choices=AVAILABILITY_CHOICES,
        default=PUBLIC,
        help_text=(
            "Issued-only types never appear on the purchase page. Use one for a "
            "sponsor's allocation, or for speaker and volunteer tickets."
        ),
    )
    sales_start_at = models.DateTimeField(
        null=True, blank=True, help_text="Sales open at this moment. Blank means already open."
    )
    sales_end_at = models.DateTimeField(
        null=True, blank=True, help_text="Sales close at this moment. Blank means no end."
    )
    max_per_order = models.IntegerField(
        default=10,
        help_text=(
            "Most of this type one person may buy at once. Keeps a mistyped quantity "
            "from consuming the whole allocation."
        ),
    )
    requires_verification = models.BooleanField(
        default=False,
        help_text=(
            "Ask the buyer for something to check, and flag the order for review. "
            "Use for a student or concession rate."
        ),
    )
    verification_prompt = models.CharField(
        max_length=200,
        blank=True,
        help_text=(
            "What to ask for, e.g. 'Your institution and student ID number'. Shown "
            "only when verification is required."
        ),
    )
    display_order = models.IntegerField(default=0, help_text="Order in which ticket types are displayed")

    objects = TicketTypeQuerySet.as_manager()

    class Meta:
        ordering = ["display_order", "price"]
        unique_together = ["name", "conference_year"]

    def __str__(self):
        return f"{self.name} ({self.conference_year})"

    def clean(self):
        if self.sales_start_at and self.sales_end_at and self.sales_start_at > self.sales_end_at:
            raise ValidationError(
                {"sales_end_at": "Sales would close before they open."}
            )
        if self.requires_verification and not self.verification_prompt.strip():
            raise ValidationError(
                {
                    "verification_prompt": (
                        "Say what the buyer should supply, or they will be asked for "
                        "'verification' and guess."
                    )
                }
            )

    @property
    def total_available(self):
        return self.early_bird_count + self.regular_count

    @property
    def total_sold(self):
        """
        Places taken. Refunded and cancelled orders release their places, which is
        the point of recording those statuses rather than deleting the row.
        """
        return (
            Ticket.objects.filter(
                ticket_type=self,
                status=Ticket.PAID,
            ).aggregate(total=models.Sum("quantity"))["total"]
            or 0
        )

    @property
    def remaining_count(self):
        return self.total_available - self.total_sold

    @property
    def is_sold_out(self):
        return self.remaining_count <= 0

    @property
    def early_bird_remaining(self):
        """Whether the early-bird price still applies: both the count and the date."""
        if self.early_bird_count == 0 or self.early_bird_price <= 0:
            return False
        if self.early_bird_ends_at and timezone.now() > self.early_bird_ends_at:
            return False
        return self.total_sold < self.early_bird_count

    @property
    def sale_state(self):
        """
        Why this type can or cannot be bought, as one of a few known strings.

        A single value rather than a handful of booleans in the template, because
        the page needs to say *which* reason: "opens on Friday" and "sold out" want
        different words, and "inactive" wants no row at all.
        """
        if not self.is_active:
            return "inactive"
        if self.availability != self.PUBLIC:
            return "invite_only"
        now = timezone.now()
        if self.sales_start_at and now < self.sales_start_at:
            return "not_yet"
        if self.sales_end_at and now > self.sales_end_at:
            return "closed"
        if self.is_sold_out:
            return "sold_out"
        return "on_sale"

    @property
    def is_on_sale(self):
        return self.sale_state == "on_sale"

    @property
    def state_label(self):
        """The sale state in words, or empty when there is nothing to explain."""
        return SALE_STATE_LABELS.get(self.sale_state, "")

    @property
    def current_price(self):
        """
        What a buyer pays right now.

        Returns the regular price even when sold out or outside the window, so the
        page can show what a ticket costs while explaining that it cannot be bought.
        Whether it *may* be bought is `is_on_sale`; the two used to be tangled
        together, and a sold-out type reported a price of zero.
        """
        if self.early_bird_remaining:
            return self.early_bird_price
        return self.price


class TicketQuerySet(models.QuerySet):
    def issued(self, user=None):
        qs = self.filter(status=Ticket.ISSUED)
        if user:
            qs = qs.filter(user=user, multiple_tickets=True)
        return qs

    def update_payment(self, new_amount=0):
        return self.update(
            date_paid=timezone.now(),
            status=Ticket.PAID,
            total_amount=new_amount,
        )

    def for_year(self, year):
        return self.filter(conference_year=year)

    def not_booked(self, user):
        """Find paid tickets that don't have TicketSale records yet."""
        return (
            self.filter(user=user, status=Ticket.PAID)
            .annotate(sale_count=models.Count("ticket_sales"))
            .filter(sale_count=0)
        )


class Ticket(models.Model):
    """
    One order. Its ``quantity`` places become ``TicketSale`` rows with names on.

    Refunded and cancelled are statuses rather than deletions: the money moved and
    the record of it has to survive, and a released place has to stop counting
    against the allocation without the order vanishing from the books.
    """

    ISSUED = 1
    PAID = 2
    REFUNDED = 3
    CANCELLED = 4
    STATUS_CHOICES = (
        (ISSUED, "Issued"),
        (PAID, "Paid"),
        (REFUNDED, "Refunded"),
        (CANCELLED, "Cancelled"),
    )

    #: Statuses where the buyer holds a place. Anything else releases its places.
    ACTIVE_STATUSES = (PAID,)

    VERIFICATION_NOT_REQUIRED = "not_required"
    VERIFICATION_PENDING = "pending"
    VERIFICATION_APPROVED = "approved"
    VERIFICATION_REJECTED = "rejected"
    VERIFICATION_CHOICES = [
        (VERIFICATION_NOT_REQUIRED, "Not required"),
        (VERIFICATION_PENDING, "Awaiting review"),
        (VERIFICATION_APPROVED, "Approved"),
        (VERIFICATION_REJECTED, "Rejected"),
    ]

    order = models.CharField(max_length=12, primary_key=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tickets",
    )
    ticket_type = models.ForeignKey(
        TicketType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tickets",
    )
    quantity = models.IntegerField(default=1)
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.IntegerField(default=ISSUED, choices=STATUS_CHOICES)
    paystack_reference = models.CharField(max_length=100, blank=True)
    coupon = models.ForeignKey(
        Coupon,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ticket_usages",
    )
    conference_year = models.IntegerField()
    related = models.CharField(
        max_length=12,
        blank=True,
        help_text="Groups multiple ticket types in one purchase",
    )
    multiple_tickets = models.BooleanField(default=False)
    created_tickets = models.BooleanField(
        default=False,
        help_text="Whether attendee details (TicketSale) have been created",
    )
    date_created = models.DateTimeField(auto_now_add=True)
    date_paid = models.DateTimeField(null=True, blank=True)

    # --- What the discount actually was ------------------------------------
    discount_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text=(
            "What the coupon took off, recorded when it was applied. Not recomputed "
            "from the coupon, whose terms may have changed since."
        ),
    )

    # --- Issued rather than sold -------------------------------------------
    complimentary_reason = models.CharField(
        max_length=200,
        blank=True,
        help_text=(
            "Why this ticket was issued without payment: speaker, volunteer, grant "
            "recipient, sponsor allocation. Empty on a purchased ticket."
        ),
    )
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tickets_issued",
        help_text="Who issued a complimentary ticket, or empty when a command did.",
    )
    issued_for_sponsor = models.ForeignKey(
        "sponsors.Sponsor",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="issued_tickets",
        help_text="Set when this came out of a sponsor's ticket allocation.",
    )

    # --- Concession rates that need checking -------------------------------
    verification_status = models.CharField(
        max_length=20,
        choices=VERIFICATION_CHOICES,
        default=VERIFICATION_NOT_REQUIRED,
    )
    verification_reference = models.CharField(
        max_length=300,
        blank=True,
        help_text="What the buyer supplied, in their words. Never validated automatically.",
    )
    verification_note = models.CharField(
        max_length=300,
        blank=True,
        help_text="The reviewer's note. Internal.",
    )

    objects = TicketQuerySet.as_manager()

    class Meta:
        ordering = ["-date_created"]
        indexes = [
            models.Index(fields=["conference_year", "status"]),
            models.Index(fields=["verification_status"]),
        ]

    def __str__(self):
        return self.order

    @property
    def full_name(self):
        return f"{self.user.first_name} {self.user.last_name}".strip()

    @staticmethod
    def create(user, **kwargs):
        """Create or retrieve an issued ticket for a user and ticket type."""
        ticket_name = kwargs.get("ticket_name")
        conference_year = kwargs.get("conference_year")
        order = generate_order_code()

        previous_issued = Ticket.objects.filter(
            user=user,
            status=Ticket.ISSUED,
            ticket_type__name=ticket_name,
            conference_year=conference_year,
        ).first()

        if previous_issued is None:
            obj = Ticket.objects.create(
                user=user,
                status=Ticket.ISSUED,
                order=order,
                conference_year=conference_year,
            )
        else:
            obj = previous_issued
            if not obj.order:
                obj.order = order
                obj.save()

        return Ticket.objects.filter(order=obj.order).first()

    def update_wallet_and_notify(self, full_payment):
        """Mark this ticket (and related grouped tickets) as paid, then email confirmation."""
        new_amount = full_payment
        others = Ticket.objects.issued(self.user).values_list("pk", flat=True)
        if self.pk in others:
            Ticket.objects.filter(pk__in=others).update_payment(new_amount=new_amount)
        Ticket.objects.filter(pk=self.pk).update_payment(new_amount=new_amount)

        self._send_purchase_confirmation_email()
        self._after_payment()

    def _after_payment(self):
        """
        The bookkeeping that follows a successful payment.

        Each step is guarded separately. A payment has happened and the buyer must
        be told so; an invoice that cannot be numbered or a waitlist row that will
        not update is a problem for an organizer, not a reason to leave somebody
        thinking their payment failed.
        """
        try:
            from .invoices import issue_invoice

            issue_invoice(self)
        except Exception:  # noqa: BLE001
            logger.exception("Could not issue an invoice for order %s", self.order)

        try:
            from .waitlist import mark_converted

            if self.user and self.user.email:
                mark_converted(self.user.email, self.conference_year)
        except Exception:  # noqa: BLE001
            logger.exception("Could not close the waitlist entry for %s", self.order)

    def _send_purchase_confirmation_email(self):
        from emails.services import send_email, site_url

        self.refresh_from_db()
        if not self.user or not self.user.email:
            return
        ticket_type_name = self.ticket_type.name if self.ticket_type else "Ticket"
        send_email(
            template="tickets/purchase_confirmation",
            to=[self.user.email],
            subject="Your PyCon Nigeria ticket is confirmed",
            context={
                "user_name": self.user.get_full_name() or self.user.email,
                "ticket_type": ticket_type_name,
                "quantity": self.quantity,
                "order_code": self.order,
                "dashboard_url": f"{site_url()}/tickets/",
            },
            tags=["tickets", "purchase"],
            conference_year=self.conference_year,
            fail_silently=False,
        )

    def change_order(self):
        """Generate a new order code (used when payment fails)."""
        old_order = self.order
        self.order = generate_order_code()
        self.save()
        Ticket.objects.filter(order=old_order).exclude(pk=self.pk).delete()
        return self

    def get_total(self):
        """Get total amount for this ticket and any grouped tickets."""
        if self.multiple_tickets:
            others = Ticket.objects.issued(self.user)
        else:
            others = Ticket.objects.filter(pk=self.pk)
        return sum(x.amount for x in others)

    @transaction.atomic
    def create_sales(self, **kwargs):
        """Create individual TicketSale records for each ticket in the quantity."""
        for _ in range(self.quantity):
            TicketSale.objects.create(
                ticket=self,
                user=self.user,
                **kwargs,
            )
        self.created_tickets = True
        self.save()
        return self

    # --- Money afterwards ---------------------------------------------------

    @property
    def is_complimentary(self):
        return bool(self.complimentary_reason)

    @property
    def refunded_amount(self):
        """Total already paid out against this order."""
        from decimal import Decimal

        return self.refunds.filter(status=Refund.PAID).aggregate(
            total=models.Sum("amount")
        )["total"] or Decimal("0")

    @property
    def refundable_amount(self):
        """What is left to refund: what was paid, less what has been."""
        from decimal import Decimal

        paid = Decimal(self.total_amount or self.amount or 0)
        return max(paid - self.refunded_amount, Decimal("0"))

    def refund_state(self, settings_obj=None):
        """
        Why this order can or cannot be refunded, as one known string.

        Returns the reason rather than a boolean so the ticket page can say which:
        "past the deadline" and "already refunded" are different conversations.
        """
        if self.status == self.REFUNDED:
            return "already_refunded"
        if self.status != self.PAID:
            return "not_paid"
        if self.is_complimentary:
            return "complimentary"
        if self.refundable_amount <= 0:
            return "nothing_to_refund"
        policy = settings_obj or TicketSettings.for_year(self.conference_year)
        if policy.refund_deadline is None:
            return "no_policy"
        if not policy.refunds_open:
            return "deadline_passed"
        return "refundable"

    @property
    def needs_verification_review(self):
        return self.verification_status == self.VERIFICATION_PENDING

    def suggested_refund(self, settings_obj=None):
        """
        What to pay back after the retained fee, as a suggestion only.

        A suggestion because the organizer types the final figure: a partial
        refund, a goodwill exception and a bank charge are all normal, and a
        computed number presented as final invites nobody to think.
        """
        from decimal import Decimal

        policy = settings_obj or TicketSettings.for_year(self.conference_year)
        fee = max(0, min(100, policy.refund_fee_percentage))
        gross = self.refundable_amount
        return (gross * (Decimal(100) - Decimal(fee)) / Decimal(100)).quantize(
            Decimal("0.01")
        )


def generate_checkin_code(length=10):
    """
    A short, unguessable code identifying one attendee's place.

    Random rather than derived from the row id: the code is printed on a badge and
    encoded in a QR that anyone can photograph, so it must not be enumerable. Ten
    characters from a 26-character alphabet is about 2^47 -- far beyond guessing at
    a door, and short enough to read aloud when a scanner will not cooperate.
    """
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))
        if not TicketSale.objects.filter(checkin_code=code).exists():
            return code


class TicketSaleQuerySet(models.QuerySet):
    def for_year(self, year):
        return self.filter(ticket__conference_year=year)

    def admitted(self):
        """Places that may actually be used: the order is paid, and not refunded."""
        return self.filter(ticket__status=Ticket.PAID)

    def unclaimed(self):
        """Places named but not yet linked to an account."""
        return self.filter(user__isnull=True)


class TicketSale(models.Model):
    """
    One attendee's place: the thing with a name on it, a QR code and a check-in.

    ``user`` is nullable, which is the fix for group purchases. A buyer paying for
    five colleagues used to have all five places saved against their own account,
    so nobody else ever saw a ticket. Now a place can be named by email before its
    owner has an account, and is linked when they sign up.
    """

    DIET_CHOICES = (
        ("Omnivorous", "Omnivorous"),
        ("Vegetarian", "Vegetarian"),
        ("Others", "Others"),
    )

    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.CASCADE,
        related_name="ticket_sales",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_tickets",
        help_text=(
            "The attendee's own account. Empty until they have one -- a place can be "
            "assigned by email before its owner has signed up."
        ),
    )
    attendee_email = models.EmailField(
        blank=True,
        help_text=(
            "Who this place is for. Kept even after the account is linked, so the "
            "order still shows who the buyer named."
        ),
    )
    full_name = models.CharField(max_length=150)
    diet = models.CharField(max_length=30, choices=DIET_CHOICES, default="Omnivorous")
    tagline = models.CharField(max_length=100, blank=True)

    checkin_code = models.CharField(
        max_length=16,
        unique=True,
        editable=False,
        db_index=True,
        help_text="What the QR code carries, and what a volunteer can type instead.",
    )
    checked_in_at = models.DateTimeField(null=True, blank=True)
    checked_in_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="checkins_recorded",
    )

    transferred_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this place last changed hands.",
    )
    transferred_from_email = models.EmailField(
        blank=True,
        help_text="Who held it before the last transfer, so a chain stays traceable.",
    )

    objects = TicketSaleQuerySet.as_manager()

    class Meta:
        ordering = ["pk"]

    def __str__(self):
        return f"{self.full_name} - {self.ticket_type_name}"

    def save(self, *args, **kwargs):
        if not self.checkin_code:
            self.checkin_code = generate_checkin_code()
        super().save(*args, **kwargs)

    @property
    def ticket_type_name(self):
        if self.ticket and self.ticket.ticket_type:
            return self.ticket.ticket_type.name
        return "Unknown"

    @property
    def ticket_id_display(self):
        return f"{self.pk:04d}"

    @property
    def is_checked_in(self):
        return self.checked_in_at is not None

    @property
    def owner_email(self):
        """The best address for this attendee, whoever ends up holding the place."""
        if self.user and self.user.email:
            return self.user.email
        return self.attendee_email

    @property
    def is_admitted(self):
        """Whether this place entitles anyone to come in."""
        return self.ticket_id and self.ticket.status == Ticket.PAID


class Invoice(models.Model):
    """
    A numbered invoice for one order.

    Company buyers are usually the largest orders and they cannot claim an expense
    without a document carrying their own name, an address and a number. The
    figures are copied at issue rather than joined to the order: an invoice is a
    statement about a moment, and it must not change when a price does.
    """

    ticket = models.OneToOneField(
        Ticket,
        on_delete=models.CASCADE,
        related_name="invoice",
    )
    number = models.CharField(
        max_length=40,
        unique=True,
        editable=False,
        help_text="Sequential within the edition, e.g. PYNG-2027-0001.",
    )
    conference_year = models.IntegerField(validators=[validate_edition_year])
    issued_at = models.DateTimeField(auto_now_add=True)

    # --- Who it is addressed to --------------------------------------------
    buyer_name = models.CharField(max_length=200)
    buyer_email = models.EmailField()
    company_name = models.CharField(
        max_length=200,
        blank=True,
        help_text="Empty for an individual buyer, in which case the name above stands alone.",
    )
    company_address = models.TextField(blank=True)
    buyer_tax_id = models.CharField(
        max_length=60,
        blank=True,
        help_text="The buyer's own tax number, where their finance team needs it shown.",
    )
    purchase_order_reference = models.CharField(
        max_length=100,
        blank=True,
        help_text="Their PO number, if they gave one. Often the only way they can pay.",
    )

    # --- What it says -------------------------------------------------------
    currency = models.CharField(max_length=3, default="NGN")
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-issued_at"]

    def __str__(self):
        return self.number

    @property
    def addressee(self):
        return self.company_name or self.buyer_name

    @property
    def is_paid(self):
        return self.ticket_id and self.ticket.status == Ticket.PAID


class Refund(models.Model):
    """
    One refund against an order: asked for, decided, and paid.

    Three separate moments, each with its own timestamp, because they are days
    apart in practice and "we said yes" is not "the money left". Paystack has a
    refund API, but the amount and the decision are recorded here either way --
    refunds get made by bank transfer often enough.
    """

    REQUESTED = "requested"
    APPROVED = "approved"
    REJECTED = "rejected"
    PAID = "paid"
    STATUS_CHOICES = [
        (REQUESTED, "Requested"),
        (APPROVED, "Approved, not yet paid"),
        (REJECTED, "Rejected"),
        (PAID, "Paid"),
    ]

    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.CASCADE,
        related_name="refunds",
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="What is being paid back. May be less than what was paid.",
    )
    reason = models.TextField(help_text="The requester's words.")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=REQUESTED)

    requested_at = models.DateTimeField(auto_now_add=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="refunds_requested",
    )
    decided_at = models.DateTimeField(null=True, blank=True, editable=False)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="refunds_decided",
    )
    paid_at = models.DateTimeField(null=True, blank=True, editable=False)
    provider_reference = models.CharField(
        max_length=120,
        blank=True,
        help_text="Paystack's refund reference, or the bank transfer reference.",
    )
    note = models.TextField(blank=True, help_text="The organizer's note. Internal.")

    class Meta:
        ordering = ["-requested_at"]
        indexes = [models.Index(fields=["status"])]

    def __str__(self):
        return f"{self.ticket_id}: {self.amount} ({self.get_status_display()})"

    def save(self, *args, **kwargs):
        now = timezone.now()
        if self.status in (self.APPROVED, self.REJECTED, self.PAID) and self.decided_at is None:
            self.decided_at = now
        if self.status == self.PAID and self.paid_at is None:
            self.paid_at = now
        super().save(*args, **kwargs)


class TicketWaitlistEntry(models.Model):
    """
    Somebody who wanted a ticket that had sold out.

    Kept per ticket type, because "any ticket" and "the student rate" are
    different asks. Email rather than an account, so the form is one field and
    costs nobody a signup at the moment they have just been disappointed.
    """

    conference_year = models.IntegerField(validators=[validate_edition_year])
    ticket_type = models.ForeignKey(
        TicketType,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="waitlist_entries",
        help_text="Empty means any ticket type.",
    )
    email = models.EmailField()
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ticket_waitlist_entries",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    notified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set when they were told a place opened up. Stops a second telling.",
    )
    converted_at = models.DateTimeField(
        null=True, blank=True, help_text="Set when they went on to buy."
    )

    class Meta:
        ordering = ["created_at"]
        verbose_name = "waitlist entry"
        verbose_name_plural = "waitlist entries"
        constraints = [
            models.UniqueConstraint(
                fields=["conference_year", "ticket_type", "email"],
                name="unique_waitlist_entry_per_type",
            ),
            # Two constraints, because a unique constraint ignores rows where one of
            # its columns is NULL: NULL is not equal to NULL. Without this second
            # one, "any ticket type" entries could be added over and over and the
            # same person emailed each time a place opened up.
            models.UniqueConstraint(
                fields=["conference_year", "email"],
                condition=Q(ticket_type__isnull=True),
                name="unique_waitlist_entry_any_type",
            ),
        ]

    def __str__(self):
        target = self.ticket_type.name if self.ticket_type else "any ticket"
        return f"{self.email} -> {target} ({self.conference_year})"

    @property
    def is_waiting(self):
        return self.notified_at is None and self.converted_at is None
