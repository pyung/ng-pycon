from django.db import models
from django.conf import settings
from django.utils import timezone
from django.db import transaction

from .utils import generate_order_code
from editions.validators import validate_edition_year


class Coupon(models.Model):
    """Discount coupon for ticket purchases."""

    code = models.CharField(max_length=50, unique=True)
    percentage = models.IntegerField(default=5, help_text="Discount percentage (e.g. 5 = 5% off)")
    max_usage = models.IntegerField(default=1, help_text="Maximum number of times this coupon can be used")
    expired = models.BooleanField(default=False)
    conference_year = models.IntegerField(
        validators=[validate_edition_year],
        help_text="Conference year this coupon is valid for",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.code} ({self.percentage}%)"

    @property
    def usage_count(self):
        return self.ticket_usages.count()

    @property
    def is_valid(self):
        return self.usage_count < self.max_usage and not self.expired


class TicketTypeQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def for_year(self, year):
        return self.filter(conference_year=year)

    def with_tickets_purchased(self):
        return self.annotate(
            purchased_count=models.Sum(
                "tickets__quantity",
                filter=models.Q(tickets__status=Ticket.PAID),
            )
        )


class TicketType(models.Model):
    """Admin-managed ticket pricing tier."""

    name = models.CharField(max_length=100, help_text="e.g. Student, Personal, Company, Patron")
    description = models.TextField(blank=True, help_text="Description of what this ticket includes")
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Regular price")
    early_bird_price = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, help_text="Early bird price (0 = no early bird)"
    )
    early_bird_count = models.IntegerField(default=0, help_text="Number of early bird tickets available")
    regular_count = models.IntegerField(default=0, help_text="Number of regular tickets available")
    conference_year = models.IntegerField(
        validators=[validate_edition_year],
        help_text="Conference year this ticket type belongs to",
    )
    is_active = models.BooleanField(default=True)
    display_order = models.IntegerField(default=0, help_text="Order in which ticket types are displayed")

    objects = TicketTypeQuerySet.as_manager()

    class Meta:
        ordering = ["display_order", "price"]
        unique_together = ["name", "conference_year"]

    def __str__(self):
        return f"{self.name} ({self.conference_year})"

    @property
    def total_available(self):
        return self.early_bird_count + self.regular_count

    @property
    def total_sold(self):
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
        """Check if early bird tickets are still available."""
        if self.early_bird_count == 0:
            return False
        return self.total_sold < self.early_bird_count

    @property
    def current_price(self):
        """Return current price based on early bird availability."""
        if self.is_sold_out:
            return 0
        if self.early_bird_remaining and self.early_bird_price > 0:
            return self.early_bird_price
        if self.remaining_count > 0:
            return self.price
        return 0


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
    """A ticket purchase/order record."""

    ISSUED = 1
    PAID = 2
    STATUS_CHOICES = (
        (ISSUED, "Issued"),
        (PAID, "Paid"),
    )

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

    objects = TicketQuerySet.as_manager()

    class Meta:
        ordering = ["-date_created"]

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


class TicketSale(models.Model):
    """Individual attendee assignment for a ticket."""

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
        on_delete=models.CASCADE,
        related_name="assigned_tickets",
    )
    full_name = models.CharField(max_length=150)
    diet = models.CharField(max_length=30, choices=DIET_CHOICES, default="Omnivorous")
    tagline = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return f"{self.full_name} - {self.ticket_type_name}"

    @property
    def ticket_type_name(self):
        if self.ticket and self.ticket.ticket_type:
            return self.ticket.ticket_type.name
        return "Unknown"

    @property
    def ticket_id_display(self):
        return f"{self.pk:04d}"
