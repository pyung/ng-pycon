import json
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST
from django.views.generic import DetailView, RedirectView, TemplateView

from accounts.roles import Role, roles_for
from editions.current import current_year

from . import checkin as checkin_service
from . import waitlist as waitlist_service
from .forms import (
    InvoiceDetailsForm,
    PurchaseForm,
    RefundRequestForm,
    TicketCreateForm,
    TicketEditForm,
    TicketTransferForm,
    WaitlistForm,
)
from .invoices import invoice_for
from .models import (
    Coupon,
    Ticket,
    TicketSale,
    TicketSettings,
    TicketType,
)
from .refunds import STATE_MESSAGES, RefundError, request_refund
from .services import PaystackService

logger = logging.getLogger(__name__)

#: Where the buyer's company details wait between placing an order and paying for
#: it. On the session rather than the order, because they belong to an invoice that
#: only exists once money has arrived, and an abandoned order should not leave a
#: company's address in the database.
BILLING_SESSION_KEY = "tickets_billing_details"

def _ticket_type_rows(year, when=None):
    """
    Every publicly relevant ticket type with its price and why it can be bought.

    Includes the ones that cannot: a sold-out type and a type opening on Friday
    both belong on the page, saying so. Inactive and invitation-only types are left
    out, because those are not offers to the public at all.
    """
    rows = []
    for ticket_type in (
        TicketType.objects.active()
        .purchasable()
        .for_year(year)
        .order_by("display_order", "price")
    ):
        state = ticket_type.sale_state
        rows.append(
            {
                "name": ticket_type.name,
                "short_name": ticket_type.name,
                "description": ticket_type.description,
                "amount": float(ticket_type.current_price),
                "current_price": ticket_type.current_price,
                "price": ticket_type.price,
                "early_bird_price": ticket_type.early_bird_price,
                "is_early_bird": ticket_type.early_bird_remaining,
                "early_bird_ends_at": ticket_type.early_bird_ends_at,
                "remaining": ticket_type.remaining_count,
                "is_sold_out": ticket_type.is_sold_out,
                "sale_state": state,
                "state_label": ticket_type.state_label,
                "is_on_sale": state == "on_sale",
                "sales_start_at": ticket_type.sales_start_at,
                "sales_end_at": ticket_type.sales_end_at,
                "max_per_order": ticket_type.max_per_order,
                # The quantities actually offered: never more than the per-order cap
                # and never more than is left. Computed here because a template
                # cannot build a range, and because a select listing ten when three
                # remain is an error message waiting to happen.
                "quantity_options": list(
                    range(
                        0,
                        max(
                            0,
                            min(ticket_type.max_per_order, ticket_type.remaining_count),
                        )
                        + 1,
                    )
                ),
                "requires_verification": ticket_type.requires_verification,
                "verification_prompt": ticket_type.verification_prompt,
            }
        )
    return rows


class TicketHomeView(TemplateView):
    """Display available ticket types with current pricing."""

    template_name = "tickets/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        year = current_year()
        rows = _ticket_type_rows(year)
        settings_obj = TicketSettings.for_year(year)
        context.update(
            {
                "ticket_types": rows,
                "any_on_sale": any(row["is_on_sale"] for row in rows),
                "sold_out_types": [row for row in rows if row["sale_state"] == "sold_out"],
                "waitlist_enabled": settings_obj.waitlist_enabled,
                "waitlist_form": WaitlistForm(conference_year=year),
                "refund_policy": settings_obj.refund_policy,
                "conference_year": year,
            }
        )
        return context


class PurchaseView(LoginRequiredMixin, TemplateView):
    """
    GET: Display the ticket purchase page with quantity selectors.
    POST: Create Ticket records and return JSON {order, total}.
    """

    template_name = "tickets/purchase.html"

    def post(self, request, *args, **kwargs):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid request data"}, status=400)

        form_data = {
            **data.get("tickets", {}),
            "coupon": data.get("coupon", ""),
            "verification_reference": data.get("verification_reference", ""),
            "company_name": data.get("company_name", ""),
            "company_address": data.get("company_address", ""),
            "buyer_tax_id": data.get("buyer_tax_id", ""),
            "purchase_order_reference": data.get("purchase_order_reference", ""),
        }
        form = PurchaseForm(
            form_data, conference_year=current_year(), user=request.user
        )

        if form.is_valid():
            ticket, total = form.save(request.user)
            if ticket is None:
                return JsonResponse({"error": "No valid tickets selected"}, status=400)
            # Held on the session rather than the ticket: they are billing details
            # for an invoice that only exists once the money arrives, and an
            # abandoned order should not leave a company's address behind.
            request.session[BILLING_SESSION_KEY] = form.billing_details()
            return JsonResponse({"order": ticket.pk, "total": float(total)})

        return JsonResponse({"error": form.errors}, status=400)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        year = current_year()
        rows = _ticket_type_rows(year)
        on_sale = [row for row in rows if row["is_on_sale"]]
        settings_obj = TicketSettings.for_year(year)

        tickets = [
            {
                "name": f"{row['name']} ticket",
                "short_name": row["name"],
                "amount": row["amount"],
                "remaining": row["remaining"],
                "max_per_order": row["max_per_order"],
                "quantity_options": row["quantity_options"],
                "requires_verification": row["requires_verification"],
                "verification_prompt": row["verification_prompt"],
            }
            for row in on_sale
        ]
        pricings = {row["name"]: row["amount"] for row in on_sale}

        context.update({
            "tickets": tickets,
            "all_ticket_types": rows,
            "unavailable_types": [row for row in rows if not row["is_on_sale"]],
            "needs_verification": [row for row in on_sale if row["requires_verification"]],
            "needs_verification_names": json.dumps(
                [row["name"] for row in on_sale if row["requires_verification"]]
            ),
            "pricings_json": json.dumps(pricings),
            "max_per_order_json": json.dumps(
                {row["name"]: row["max_per_order"] for row in on_sale}
            ),
            "public_key": settings.PAYSTACK_PUBLIC_KEY,
            "conference_year": year,
            "refund_policy": settings_obj.refund_policy,
            "waitlist_enabled": settings_obj.waitlist_enabled,
            "waitlist_form": WaitlistForm(conference_year=year),
            "user_email": self.request.user.email if self.request.user.is_authenticated else "",
            "user_first_name": self.request.user.first_name if self.request.user.is_authenticated else "",
            "user_last_name": self.request.user.last_name if self.request.user.is_authenticated else "",
        })
        return context


def valid_coupons(request):
    """
    Check a coupon code, for the purchase page.

    Returns the discount in a shape the page can apply either way: ``status`` is
    kept as the percentage for the existing script, and ``kind``/``amount``
    describe a fixed discount, which the old endpoint could not express at all.
    """
    code = request.GET.get("value", "").strip()
    if not code:
        return JsonResponse({"status": 0})

    user = request.user if request.user.is_authenticated else None
    coupon = Coupon.objects.filter(
        code__iexact=code, conference_year=current_year()
    ).first()
    if coupon is None or not coupon.usable_by(user):
        return JsonResponse({"status": 0, "message": "That code is not valid."})

    if coupon.discount_type == Coupon.FIXED:
        return JsonResponse({
            "status": 0,
            "kind": Coupon.FIXED,
            "amount": float(coupon.amount),
            "label": coupon.discount_display,
        })
    return JsonResponse({
        "status": coupon.percentage,
        "kind": Coupon.PERCENTAGE,
        "amount": 0,
        "label": coupon.discount_display,
    })


@login_required
def validate_paystack_ref(request, order, code):
    """Verify a Paystack transaction and update the ticket status."""
    paystack = PaystackService()
    result = paystack.verify_transaction(code)
    ticket = get_object_or_404(Ticket, pk=order)

    if result:
        ticket.paystack_reference = code
        ticket.save()
        ticket.update_wallet_and_notify(result["amount_paid"])

        # Also update any related/grouped tickets
        if ticket.related:
            Ticket.objects.filter(
                related=ticket.related,
                status=Ticket.ISSUED,
            ).update(
                status=Ticket.PAID,
                date_paid=ticket.date_paid,
                paystack_reference=code,
            )

        messages.info(request, "Payment successful!")
        return JsonResponse({"status": True})
    else:
        ticket = ticket.change_order()
        return JsonResponse({"status": False, "order": ticket.order})


@csrf_exempt
def paystack_webhook(request):
    """
    Receive and process Paystack webhook events.
    Verifies the HMAC signature before processing.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    # Verify webhook signature
    if not PaystackService.verify_webhook_signature(request):
        logger.warning("Invalid Paystack webhook signature")
        return JsonResponse({"error": "Invalid signature"}, status=400)

    try:
        payload = json.loads(request.body)
        event = payload.get("event", "")

        if event == "charge.success":
            data = payload.get("data", {})
            reference = data.get("reference", "")
            amount = data.get("amount", 0) / 100  # kobo to naira

            # Find the ticket by reference or order
            ticket = Ticket.objects.filter(
                paystack_reference=reference,
                status=Ticket.ISSUED,
            ).first()

            if not ticket:
                # Try matching by order code (reference is the order code)
                ticket = Ticket.objects.filter(
                    order=reference,
                    status=Ticket.ISSUED,
                ).first()

            if ticket:
                ticket.paystack_reference = reference
                ticket.save()
                ticket.update_wallet_and_notify(amount)

                if ticket.related:
                    Ticket.objects.filter(
                        related=ticket.related,
                        status=Ticket.ISSUED,
                    ).update(
                        status=Ticket.PAID,
                        date_paid=ticket.date_paid,
                        paystack_reference=reference,
                    )

                logger.info("Webhook: Payment confirmed for order %s", ticket.order)

    except (json.JSONDecodeError, KeyError) as e:
        logger.error("Paystack webhook processing error: %s", e)

    # Always return 200 to Paystack to acknowledge receipt
    return JsonResponse({"status": "ok"})


class PaystackCallbackView(RedirectView):
    """Handle Paystack redirect callback after payment."""

    permanent = False
    query_string = True

    def get_redirect_url(self, *args, **kwargs):
        order = kwargs.get("order")
        ticket = get_object_or_404(Ticket, pk=order)
        trxref = self.request.GET.get("trxref")

        if trxref:
            paystack = PaystackService()
            result = paystack.verify_transaction(trxref)
            if result:
                ticket.paystack_reference = trxref
                ticket.save()
                ticket.update_wallet_and_notify(result["amount_paid"])

                if ticket.related:
                    Ticket.objects.filter(
                        related=ticket.related,
                        status=Ticket.ISSUED,
                    ).update(
                        status=Ticket.PAID,
                        date_paid=ticket.date_paid,
                        paystack_reference=trxref,
                    )

                messages.info(self.request, "Payment successful!")
                return reverse("tickets:purchase-complete", args=[ticket.order])

        messages.error(
            self.request,
            "Sorry, there was an error processing your payment. Please try again or contact support.",
        )
        ticket = ticket.change_order()
        return reverse("tickets:purchase")


@login_required
def purchase_complete(request, order):
    """Display the purchase success page, and finish the invoice."""
    ticket = get_object_or_404(Ticket, pk=order)

    if ticket.status != Ticket.PAID:
        messages.error(request, "No payment has been recorded for this order.")
        return redirect("tickets:purchase")

    # The payment path already issued an invoice; this adds the company details the
    # buyer typed before paying, which only this session knows about.
    billing = request.session.pop(BILLING_SESSION_KEY, None) or {}
    invoice = invoice_for(ticket, **billing)
    if invoice is not None and billing.get("company_name") and not invoice.company_name:
        for field, value in billing.items():
            setattr(invoice, field, value)
        invoice.save()

    count = Ticket.objects.filter(status=Ticket.PAID, related=order).count()
    return render(
        request,
        "tickets/purchase_complete.html",
        {"ticket": ticket, "count": count, "invoice": invoice},
    )


class CreateTicketView(LoginRequiredMixin, DetailView):
    """Assign attendee details to purchased ticket(s)."""

    model = Ticket
    pk_url_kwarg = "order"
    template_name = "tickets/create.html"

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        form = TicketCreateForm(request.POST)
        if form.is_valid():
            result = form.save(self.object)
            return redirect(reverse("tickets:detail", args=[result.pk]))
        messages.error(request, "Please fix the errors below.")
        return self.render_to_response(self.get_context_data(ticket_form=form))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if "ticket_form" not in kwargs:
            form = TicketCreateForm(
                initial={"full_name": self.object.full_name}
            )
            context["ticket_form"] = form
        return context


class TicketDetailView(LoginRequiredMixin, DetailView):
    """View and edit assigned ticket details."""

    model = Ticket
    pk_url_kwarg = "order"
    template_name = "tickets/detail.html"

    def get(self, request, *args, **kwargs):
        result = super().get(request, *args, **kwargs)
        if not self.object.created_tickets:
            messages.error(request, "This ticket hasn't had attendee details assigned yet.")
            return redirect("tickets:create_ticket", order=self.object.pk)
        return result

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        ticket = self.object
        # The buyer sees every place they paid for, including ones assigned to
        # somebody else -- they are the one who can still change them. An attendee
        # who was given a place sees only their own.
        sales = ticket.ticket_sales.select_related("ticket__ticket_type", "user")
        if ticket.user_id != self.request.user.pk:
            sales = sales.filter(user=self.request.user)

        settings_obj = TicketSettings.for_year(ticket.conference_year)
        refund_state = ticket.refund_state(settings_obj)

        # A form per place, each with its own field ids. Sharing them meant several
        # inputs called id_full_name on one page, so a label focused the wrong
        # field and a screen reader announced the wrong thing.
        context["tickets"] = [
            {
                "form": TicketEditForm(instance=sale, auto_id=f"id_%s_{sale.pk}"),
                "transfer_form": TicketTransferForm(
                    ticket_sale=sale, auto_id=f"id_transfer_%s_{sale.pk}"
                ),
                "ticket": sale,
                "qr_svg": checkin_service.qr_svg(sale),
            }
            for sale in sales
        ]
        context.update(
            {
                "transfer_form": TicketTransferForm(),
                "transfers_open": settings_obj.transfers_open,
                "transfer_deadline": settings_obj.transfer_deadline,
                "refund_state": refund_state,
                "refund_message": STATE_MESSAGES.get(refund_state, ""),
                "can_request_refund": refund_state == "refundable",
                "refund_policy": settings_obj.refund_policy,
                "suggested_refund": ticket.suggested_refund(settings_obj),
                "refunds": ticket.refunds.all(),
                "invoice": getattr(ticket, "invoice", None),
                "is_buyer": ticket.user_id == self.request.user.pk,
            }
        )
        return context


class SalesTicketView(LoginRequiredMixin, RedirectView):
    """Handle editing or transferring an individual ticket sale."""

    query_string = True

    def get_object(self):
        return get_object_or_404(TicketSale, pk=self.kwargs["pk"])

    def get_redirect_url(self, *args, **kwargs):
        self.object = self.get_object()
        request = self.request
        is_transfer = request.GET.get("transfer")

        if is_transfer:
            form = TicketTransferForm(request.POST, ticket_sale=self.object)
        else:
            form = TicketEditForm(request.POST, instance=self.object)

        if form.is_valid():
            if is_transfer:
                result = form.save()
                messages.success(
                    request, f"Ticket has been transferred to {result.owner_email}."
                )
            else:
                form.save()
                messages.success(request, "Ticket details updated successfully.")
        else:
            # Show the actual reason. "Transfer failed" told a buyer nothing, and
            # the reason is usually a closed window or their own address.
            for error in form.non_field_errors():
                messages.error(request, error)
            for field, errors in form.errors.items():
                if field == "__all__":
                    continue
                for error in errors:
                    messages.error(request, error)

        return reverse("tickets:detail", args=[self.object.ticket.pk])


# ---------------------------------------------------------------------------
# Invoices, refunds, the waitlist and the door
# ---------------------------------------------------------------------------


@login_required
def invoice_view(request, order):
    """
    A printable invoice for one order.

    HTML with a print stylesheet rather than a generated PDF. A browser's "save as
    PDF" produces a perfectly good file, and the alternative is a rendering library
    in the image for one page -- a poor trade for a team that finds deploying
    expensive. The company details can still be filled in here, because buyers
    routinely discover they need them a fortnight later.
    """
    ticket = get_object_or_404(Ticket, pk=order)
    if ticket.user_id != request.user.pk and not _is_organizer(request.user):
        raise Http404("No invoice here.")
    if ticket.status != Ticket.PAID:
        messages.error(request, "An invoice is only issued once an order is paid.")
        return redirect("tickets:detail", order=ticket.pk)

    invoice = invoice_for(ticket)

    if request.method == "POST":
        form = InvoiceDetailsForm(request.POST)
        if form.is_valid():
            for field, value in form.cleaned_data.items():
                setattr(invoice, field, value)
            invoice.save()
            messages.success(request, "Invoice details updated.")
            return redirect("tickets:invoice", order=ticket.pk)
    else:
        form = InvoiceDetailsForm(
            initial={
                "company_name": invoice.company_name,
                "company_address": invoice.company_address,
                "buyer_tax_id": invoice.buyer_tax_id,
                "purchase_order_reference": invoice.purchase_order_reference,
            }
        )

    settings_obj = TicketSettings.for_year(ticket.conference_year)
    return render(
        request,
        "tickets/invoice.html",
        {
            "ticket": ticket,
            "invoice": invoice,
            "form": form,
            "seller": settings_obj,
            "places": ticket.ticket_sales.all(),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def refund_request(request, order):
    """Let a buyer ask for a refund, within the edition's policy."""
    ticket = get_object_or_404(Ticket, pk=order, user=request.user)
    settings_obj = TicketSettings.for_year(ticket.conference_year)
    state = ticket.refund_state(settings_obj)

    if state != "refundable":
        messages.error(
            request, STATE_MESSAGES.get(state, "This order cannot be refunded.")
        )
        return redirect("tickets:detail", order=ticket.pk)

    if request.method == "POST":
        form = RefundRequestForm(request.POST)
        if form.is_valid():
            try:
                request_refund(
                    ticket,
                    reason=form.cleaned_data["reason"],
                    requested_by=request.user,
                )
            except RefundError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(
                    request,
                    "Your refund request has reached the organizers. Nothing has "
                    "been paid back yet -- they will be in touch.",
                )
                return redirect("tickets:detail", order=ticket.pk)
    else:
        form = RefundRequestForm()

    return render(
        request,
        "tickets/refund_request.html",
        {
            "ticket": ticket,
            "form": form,
            "policy": settings_obj.refund_policy,
            "suggested": ticket.suggested_refund(settings_obj),
            "deadline": settings_obj.refund_deadline,
        },
    )


@require_POST
def waitlist_join(request):
    """
    Join the waitlist for a sold-out ticket type.

    No login: somebody who has just been told the ticket is gone will not make an
    account to be told again later.
    """
    year = current_year()
    if not waitlist_service.is_enabled(year):
        messages.error(request, "There is no waiting list for this edition.")
        return redirect("tickets:home")

    form = WaitlistForm(request.POST, conference_year=year)
    if not form.is_valid():
        messages.error(request, "Please check the email address and try again.")
        return redirect("tickets:home")

    entry, created = waitlist_service.join(
        email=form.cleaned_data["email"],
        ticket_type=form.cleaned_data.get("ticket_type"),
        year=year,
        user=request.user if request.user.is_authenticated else None,
    )
    if created:
        messages.success(
            request,
            "You are on the waiting list. We will email you if a place opens up -- "
            "being told is not a reservation, so be quick.",
        )
    else:
        messages.info(request, "You were already on that waiting list.")
    return redirect("tickets:home")


def _is_organizer(user):
    """Whether this account may act on somebody else's order."""
    if user is None or not user.is_authenticated:
        return False
    return roles_for(user).has(Role.ORGANIZER, Role.SUPER_ADMIN, Role.VOLUNTEER)


@login_required
@require_http_methods(["GET", "POST"])
def checkin(request, code):
    """
    Admit one attendee by their check-in code.

    Where the QR on a ticket points. A GET shows who the code belongs to and asks
    for confirmation; the POST records it. That split is deliberate: a scanner app
    opening a URL should never admit somebody as a side effect of loading a page,
    and the volunteer wants to see a name before they wave anybody through.

    The full scanning screen -- offline tolerance, a queue, a running count -- is
    module 11. This is the single-ticket case, and it works from any phone camera.
    """
    if not _is_organizer(request.user):
        raise Http404("No check-in here.")

    if request.method == "POST":
        outcome, sale = checkin_service.check_in(code, by=request.user)
    else:
        outcome, sale = checkin_service.inspect(code)

    return render(
        request,
        "tickets/checkin.html",
        {
            "outcome": outcome,
            "message": checkin_service.MESSAGES.get(outcome, ""),
            "sale": sale,
            "code": checkin_service.normalise(code),
            "recorded": request.method == "POST" and outcome == checkin_service.OK,
            "can_admit": request.method == "GET" and outcome == checkin_service.OK,
        },
    )
