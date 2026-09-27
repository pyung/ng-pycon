"""
Admin surfaces for ticketing.

Grouped the way the work actually divides: what is on sale, the orders, the people
coming, and the money afterwards. The read-only fields matter as much as the
editable ones -- an invoice number and a check-in code are records of something
that happened, and nothing good comes of being able to retype them.
"""

from wagtail_modeladmin.options import ModelAdmin, ModelAdminGroup, modeladmin_register

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


class TicketTypeAdmin(ModelAdmin):
    model = TicketType
    menu_label = "Ticket Types"
    menu_icon = "tag"
    menu_order = 100
    list_display = [
        "name",
        "conference_year",
        "availability",
        "price",
        "early_bird_price",
        "early_bird_ends_at",
        "sales_start_at",
        "sales_end_at",
        "sale_state",
        "remaining_count",
        "is_active",
    ]
    list_filter = ["conference_year", "is_active", "availability", "requires_verification"]
    search_fields = ["name"]
    ordering = ["conference_year", "display_order"]
    inspect_view_enabled = True


class CouponAdmin(ModelAdmin):
    model = Coupon
    menu_label = "Coupons"
    menu_icon = "snippet"
    menu_order = 200
    list_display = [
        "code",
        "discount_display",
        "usage_count",
        "max_usage",
        "max_per_user",
        "valid_from",
        "valid_until",
        "expired",
        "conference_year",
    ]
    list_filter = ["conference_year", "expired", "discount_type"]
    search_fields = ["code", "description"]


class TicketAdmin(ModelAdmin):
    model = Ticket
    menu_label = "Orders"
    menu_icon = "doc-full"
    menu_order = 300
    list_display = [
        "order",
        "user",
        "ticket_type",
        "quantity",
        "amount",
        "discount_amount",
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
    search_fields = ["order", "user__email", "verification_reference"]
    ordering = ["-date_created"]
    inspect_view_enabled = True


class TicketSaleAdmin(ModelAdmin):
    model = TicketSale
    menu_label = "Attendees"
    menu_icon = "group"
    menu_order = 400
    list_display = [
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
    inspect_view_enabled = True


class InvoiceAdmin(ModelAdmin):
    model = Invoice
    menu_label = "Invoices"
    menu_icon = "doc-empty"
    menu_order = 500
    list_display = [
        "number",
        "addressee",
        "company_name",
        "total",
        "currency",
        "conference_year",
        "issued_at",
        "is_paid",
    ]
    list_filter = ["conference_year", "currency"]
    search_fields = ["number", "buyer_name", "buyer_email", "company_name", "ticket__order"]
    inspect_view_enabled = True


class RefundAdmin(ModelAdmin):
    model = Refund
    menu_label = "Refunds"
    menu_icon = "undo"
    menu_order = 600
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
    ordering = ["-requested_at"]
    inspect_view_enabled = True


class WaitlistAdmin(ModelAdmin):
    model = TicketWaitlistEntry
    menu_label = "Waitlist"
    menu_icon = "time"
    menu_order = 700
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
    ordering = ["created_at"]


class TicketSettingsAdmin(ModelAdmin):
    model = TicketSettings
    menu_label = "Settings"
    menu_icon = "cogs"
    menu_order = 800
    list_display = [
        "conference_year",
        "transfer_deadline",
        "refund_deadline",
        "refund_fee_percentage",
        "waitlist_enabled",
        "invoice_prefix",
    ]


class TicketsAdminGroup(ModelAdminGroup):
    menu_label = "Tickets"
    menu_icon = "ticket"
    menu_order = 200
    items = (
        TicketTypeAdmin,
        CouponAdmin,
        TicketAdmin,
        TicketSaleAdmin,
        InvoiceAdmin,
        RefundAdmin,
        WaitlistAdmin,
        TicketSettingsAdmin,
    )


modeladmin_register(TicketsAdminGroup)
