from wagtail_modeladmin.options import ModelAdmin, ModelAdminGroup, modeladmin_register

from .models import Sponsor, SponsorTier


class SponsorAdmin(ModelAdmin):
    model = Sponsor
    menu_label = "Sponsors"
    menu_icon = "group"
    menu_order = 100
    list_display = [
        "name", "tier", "conference_year", "is_published",
        "payment_state", "amount", "deliverables_summary",
    ]
    list_filter = ["conference_year", "tier", "is_published", "is_invoiced", "is_paid"]
    search_fields = ["name", "contact_name", "contact_email"]
    ordering = ["-conference_year", "tier__display_order", "display_order"]
    inspect_view_enabled = True


class SponsorTierAdmin(ModelAdmin):
    model = SponsorTier
    menu_label = "Tiers"
    menu_icon = "list-ol"
    menu_order = 200
    list_display = ["name", "display_order", "logo_max_width", "is_published"]
    ordering = ["display_order", "name"]


class SponsorsAdminGroup(ModelAdminGroup):
    menu_label = "Sponsorship"
    menu_icon = "group"
    menu_order = 170
    items = (SponsorAdmin, SponsorTierAdmin)


modeladmin_register(SponsorsAdminGroup)
