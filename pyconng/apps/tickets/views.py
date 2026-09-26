import json
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import DetailView, RedirectView, TemplateView

from editions.current import current_year

from .forms import PurchaseForm, TicketCreateForm, TicketEditForm, TicketTransferForm
from .models import Coupon, Ticket, TicketSale, TicketType
from .services import PaystackService

logger = logging.getLogger(__name__)


class TicketHomeView(TemplateView):
    """Display available ticket types with current pricing."""

    template_name = "tickets/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        ticket_types = (
            TicketType.objects.active()
            .for_year(current_year())
            .order_by("display_order", "price")
        )
        context["ticket_types"] = [
            {
                "name": tt.name,
                "description": tt.description,
                "current_price": tt.current_price,
                "price": tt.price,
                "early_bird_price": tt.early_bird_price,
                "is_early_bird": tt.early_bird_remaining,
                "remaining": tt.remaining_count,
                "is_sold_out": tt.is_sold_out,
            }
            for tt in ticket_types
            if tt.current_price > 0
        ]
        context["conference_year"] = current_year()
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

        form_data = {**data.get("tickets", {}), "coupon": data.get("coupon", "")}
        form = PurchaseForm(form_data, conference_year=current_year())

        if form.is_valid():
            result = form.save(request.user)
            if result[0] is None:
                return JsonResponse({"error": "No valid tickets selected"}, status=400)
            ticket, total = result
            return JsonResponse({"order": ticket.pk, "total": float(total)})

        return JsonResponse({"error": form.errors}, status=400)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        ticket_types = (
            TicketType.objects.active()
            .for_year(current_year())
            .order_by("display_order", "price")
        )

        tickets = []
        pricings = {}
        for tt in ticket_types:
            price = tt.current_price
            if price > 0:
                tickets.append({
                    "name": f"{tt.name} ticket",
                    "short_name": tt.name,
                    "amount": float(price),
                    "remaining": tt.remaining_count,
                })
                pricings[tt.name] = float(price)

        context.update({
            "tickets": tickets,
            "pricings_json": json.dumps(pricings),
            "public_key": settings.PAYSTACK_PUBLIC_KEY,
            "conference_year": current_year(),
            "user_email": self.request.user.email if self.request.user.is_authenticated else "",
            "user_first_name": self.request.user.first_name if self.request.user.is_authenticated else "",
            "user_last_name": self.request.user.last_name if self.request.user.is_authenticated else "",
        })
        return context


def valid_coupons(request):
    """AJAX endpoint to validate a coupon code."""
    code = request.GET.get("value", "").strip()
    if code:
        coupon = Coupon.objects.filter(
            code__iexact=code,
            expired=False,
            conference_year=current_year(),
        ).first()
        if coupon and coupon.is_valid:
            return JsonResponse({"status": coupon.percentage})
    return JsonResponse({"status": 0})


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
    """Display the purchase success page."""
    ticket = get_object_or_404(Ticket, pk=order)

    if ticket.status != Ticket.PAID:
        messages.error(request, "No payment has been recorded for this order.")
        return redirect("tickets:purchase")

    count = Ticket.objects.filter(status=Ticket.PAID, related=order).count()
    return render(
        request,
        "tickets/purchase_complete.html",
        {"ticket": ticket, "count": count},
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
        sales = self.object.ticket_sales.select_related(
            "ticket__ticket_type"
        ).filter(user=self.request.user)

        if "forms" not in kwargs:
            forms_list = [
                {"form": TicketEditForm(instance=sale), "ticket": sale}
                for sale in sales
            ]
            context["tickets"] = forms_list
            context["transfer_form"] = TicketTransferForm()
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
            form = TicketTransferForm(request.POST)
        else:
            form = TicketEditForm(request.POST, instance=self.object)

        if form.is_valid():
            if is_transfer:
                result = form.save(ticket_sale=self.object)
                messages.success(
                    request,
                    f"Ticket has been transferred to {result.user.email}",
                )
            else:
                form.save()
                messages.success(request, "Ticket details updated successfully.")
        else:
            if is_transfer:
                messages.error(
                    request,
                    "Transfer failed. Please check the email address and try again.",
                )
            else:
                messages.error(
                    request,
                    "Please ensure all required fields are filled in correctly.",
                )

        return reverse("tickets:detail", args=[self.object.ticket.pk])
