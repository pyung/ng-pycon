from django.contrib import admin

from .models import Sponsor, SponsorTier


@admin.register(SponsorTier)
class SponsorTierAdmin(admin.ModelAdmin):
    list_display = ["name", "display_order", "logo_max_width", "is_published"]
    list_editable = ["display_order", "is_published"]
    prepopulated_fields = {"slug": ("name",)}
    ordering = ["display_order", "name"]


@admin.register(Sponsor)
class SponsorAdmin(admin.ModelAdmin):
    list_display = [
        "name", "tier", "conference_year", "is_published",
        "payment_state", "amount", "tickets_summary", "deliverables_summary",
    ]
    list_filter = ["conference_year", "tier", "is_published", "is_invoiced", "is_paid"]
    search_fields = ["name", "contact_name", "contact_email", "notes"]
    ordering = ["-conference_year", "tier__display_order", "display_order"]

    @admin.display(description="Tickets")
    def tickets_summary(self, obj):
        if not obj.tickets_allocated:
            return "—"
        return f"{obj.tickets_claimed}/{obj.tickets_allocated} claimed"

    @admin.display(description="Benefits")
    def deliverables_summary(self, obj):
        return obj.deliverables_summary
