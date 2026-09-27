"""
Django admin for ticketing, where the actions live.

The Wagtail side is for looking things up; this is where an organizer does the
things that change money: approving a concession rate, deciding a refund, marking
one paid. Each one goes through the service functions rather than editing fields,
so the audit trail and the emails happen whichever surface is used.
"""

from django.contrib import admin, messages

from .issuing import REASONS
from .models import (
    Coupon,
    Invoice,
    Refund,
    Ticket,
    TicketSale,
    TicketSettings,
    TicketType,
    TicketWaitlistEntry,
)
from .refunds import RefundError, decide_refund, mark_paid


@admin.register(TicketSettings)
class TicketSettingsAdmin(admin.ModelAdmin):
    list_display = [
        "conference_year",
        "transfer_deadline",
        "refund_deadline",
        "refund_fee_percentage",
        "waitlist_enabled",
        "invoice_prefix",
    ]
    fieldsets = [
        (None, {"fields": ["conference_year"]}),
        ("Transfers", {"fields": ["transfer_deadline"]}),
        (
            "Refunds",
            {
                "fields": ["refund_deadline", "refund_fee_percentage", "refund_policy"],
                "description": (
                    "Leaving the deadline empty means no refunds are offered. Say so "
                    "in the policy text as well -- silence is not a policy."
                ),
            },
        ),
        ("Waitlist", {"fields": ["waitlist_enabled"]}),
        ("Check-in", {"fields": ["checkin_opens_at"]}),
        (
            "Invoices",
            {
                "fields": [
                    "invoice_prefix",
                    "invoice_from_name",
                    "invoice_from_address",
                    "invoice_tax_id",
                    "invoice_notes",
                ]
            },
        ),
    ]


@admin.register(TicketType)
class TicketTypeAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "conference_year",
        "availability",
        "sale_state",
        "current_price_display",
        "early_bird_price",
        "early_bird_ends_at",
        "total_sold_display",
        "remaining_display",
        "is_active",
    ]
    list_filter = ["conference_year", "is_active", "availability", "requires_verification"]
    search_fields = ["name"]
    ordering = ["conference_year", "display_order", "price"]
    fieldsets = [
        (None, {"fields": ["name", "description", "conference_year", "display_order"]}),
        (
            "Price",
            {
                "fields": [
                    "price",
                    "early_bird_price",
                    "early_bird_count",
                    "early_bird_ends_at",
                ],
                "description": (
                    "Early bird ends when the count runs out or the date passes, "
                    "whichever comes first."
                ),
            },
        ),
        (
            "Availability",
            {
                "fields": [
                    "is_active",
                    "availability",
                    "regular_count",
                    "sales_start_at",
                    "sales_end_at",
                    "max_per_order",
                ]
            },
        ),
        (
            "Verification",
            {
                "fields": ["requires_verification", "verification_prompt"],
                "description": "For a student or other concession rate that has to be checked.",
            },
        ),
    ]

    @admin.display(description="Current price")
    def current_price_display(self, obj):
        return obj.current_price

    @admin.display(description="Sold")
    def total_sold_display(self, obj):
        return obj.total_sold

    @admin.display(description="Remaining")
    def remaining_display(self, obj):
        return obj.remaining_count


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = [
        "code",
        "discount_display",
        "usage_count_display",
        "max_usage",
        "max_per_user",
        "valid_from",
        "valid_until",
        "expired",
        "conference_year",
    ]
    list_filter = ["conference_year", "expired", "discount_type"]
    search_fields = ["code", "description"]
    filter_horizontal = ["ticket_types"]
    actions = ["mark_as_expired"]

    @admin.display(description="Times used")
    def usage_count_display(self, obj):
        return obj.usage_count

    @admin.action(description="Mark selected coupons as expired")
    def mark_as_expired(self, request, queryset):
        updated = queryset.update(expired=True)
        self.message_user(request, f"{updated} coupon(s) marked expired.")


class TicketSaleInline(admin.TabularInline):
    model = TicketSale
    extra = 0
    fields = [
        "full_name",
        "attendee_email",
        "user",
        "diet",
        "tagline",
        "checkin_code",
        "checked_in_at",
    ]
    readonly_fields = ["checkin_code", "checked_in_at"]


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = [
        "order",
        "user",
        "ticket_type",
        "quantity",
        "amount",
        "discount_amount",
        "total_amount",
        "status",
        "verification_status",
        "complimentary_reason",
        "conference_year",
        "date_created",
    ]
    list_filter = [
        "status",
        "verification_status",
        "conference_year",
        "ticket_type__name",
        "complimentary_reason",
    ]
    search_fields = ["order", "user__email", "user__username", "verification_reference"]
    readonly_fields = ["order", "date_created", "date_paid", "discount_amount"]
    ordering = ["-date_created"]
    inlines = [TicketSaleInline]
    actions = ["approve_verification", "reject_verification"]

    @admin.action(description="Approve the concession rate on selected orders")
    def approve_verification(self, request, queryset):
        self._set_verification(request, queryset, Ticket.VERIFICATION_APPROVED)

    @admin.action(description="Reject the concession rate on selected orders")
    def reject_verification(self, request, queryset):
        self._set_verification(request, queryset, Ticket.VERIFICATION_REJECTED)

    def _set_verification(self, request, queryset, status):
        """
        Record the decision, and say so in the trail.

        Rejecting does not cancel the order or take anybody's ticket away: that is a
        conversation and possibly a refund, not a side effect of a dropdown.
        """
        from audit.services import record

        pending = queryset.exclude(verification_status=Ticket.VERIFICATION_NOT_REQUIRED)
        count = 0
        for ticket in pending:
            previous = ticket.get_verification_status_display()
            ticket.verification_status = status
            ticket.save(update_fields=["verification_status"])
            record(
                target=ticket,
                action="Concession rate reviewed",
                actor=request.user,
                old_value=previous,
                new_value=ticket.get_verification_status_display(),
                note=ticket.order,
                conference_year=ticket.conference_year,
            )
            count += 1

        skipped = queryset.count() - count
        self.message_user(request, f"{count} order(s) updated.")
        if skipped:
            self.message_user(
                request,
                f"{skipped} order(s) skipped: they do not need verification.",
                level=messages.WARNING,
            )


@admin.register(TicketSale)
class TicketSaleAdmin(admin.ModelAdmin):
    list_display = [
        "ticket_id_display",
        "full_name",
        "owner_email",
        "ticket_type_name",
        "diet",
        "checkin_code",
        "checked_in_at",
        "ticket",
    ]
    list_filter = ["ticket__ticket_type__name", "diet", "ticket__conference_year"]
    search_fields = ["full_name", "attendee_email", "user__email", "checkin_code"]
    readonly_fields = ["checkin_code", "checked_in_at", "checked_in_by", "transferred_at"]
    ordering = ["pk"]
    actions = ["undo_checkin"]

    @admin.action(description="Undo check-in for selected attendees")
    def undo_checkin(self, request, queryset):
        from .checkin import undo

        count = 0
        for sale in queryset.filter(checked_in_at__isnull=False):
            undo(sale, by=request.user)
            count += 1
        self.message_user(request, f"{count} check-in(s) undone.")


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = [
        "number",
        "addressee",
        "total",
        "currency",
        "conference_year",
        "issued_at",
        "is_paid",
    ]
    list_filter = ["conference_year", "currency"]
    search_fields = ["number", "buyer_name", "buyer_email", "company_name", "ticket__order"]
    # An invoice number and its figures are a record of a moment. Editing the
    # addressee is fine; editing what was charged is rewriting history.
    readonly_fields = ["number", "ticket", "conference_year", "issued_at", "subtotal", "discount", "total"]


@admin.register(Refund)
class RefundAdmin(admin.ModelAdmin):
    list_display = [
        "ticket",
        "amount",
        "status",
        "requested_at",
        "requested_by",
        "decided_by",
        "paid_at",
        "provider_reference",
    ]
    list_filter = ["status", "ticket__conference_year"]
    search_fields = ["ticket__order", "ticket__user__email", "provider_reference"]
    readonly_fields = ["requested_at", "decided_at", "paid_at"]
    ordering = ["-requested_at"]
    actions = ["approve", "reject", "mark_as_paid"]

    @admin.action(description="Approve selected refunds (does not move money)")
    def approve(self, request, queryset):
        self._decide(request, queryset, approve=True)

    @admin.action(description="Reject selected refunds")
    def reject(self, request, queryset):
        self._decide(request, queryset, approve=False)

    def _decide(self, request, queryset, approve):
        done, failed = 0, []
        for refund in queryset:
            try:
                decide_refund(refund, approve=approve, decided_by=request.user)
                done += 1
            except RefundError as exc:
                failed.append(f"{refund.ticket_id}: {exc}")
        self.message_user(request, f"{done} refund(s) updated.")
        for problem in failed:
            self.message_user(request, problem, level=messages.WARNING)

    @admin.action(description="Mark selected refunds paid and release the place")
    def mark_as_paid(self, request, queryset):
        """
        Records that the money went back. Does not call Paystack.

        Deliberately separate: a refund often goes out by transfer, and an action
        that silently hits a payment API from a list view is the wrong shape for
        something irreversible. Use tickets.refunds.refund_through_paystack for that,
        then mark it paid with the reference it returns.
        """
        done, failed = 0, []
        for refund in queryset:
            try:
                mark_paid(refund, paid_by=request.user)
                done += 1
            except RefundError as exc:
                failed.append(f"{refund.ticket_id}: {exc}")
        self.message_user(
            request, f"{done} refund(s) marked paid. The buyers have been emailed."
        )
        for problem in failed:
            self.message_user(request, problem, level=messages.WARNING)


@admin.register(TicketWaitlistEntry)
class TicketWaitlistEntryAdmin(admin.ModelAdmin):
    list_display = [
        "email",
        "ticket_type",
        "conference_year",
        "created_at",
        "notified_at",
        "converted_at",
    ]
    list_filter = ["conference_year", "ticket_type"]
    search_fields = ["email"]
    readonly_fields = ["created_at"]
    ordering = ["created_at"]
