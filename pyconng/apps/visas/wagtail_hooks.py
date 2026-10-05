"""
Wagtail admin for visa letters.

The settings are registered so an officer can set the window, the letterhead and
the retention period without a developer -- the standing rule on this site that
anything a person needs to change must not need a deploy.

The requests are registered read-only-ish: they are listed and searchable, but the
list shows the masked passport number and the decision happens on the module's own
queue page, not here. Deciding needs the eligibility re-check, the audit entry and
the email, and a bare admin save does none of those.
"""

from wagtail_modeladmin.options import ModelAdmin, ModelAdminGroup, modeladmin_register

from .models import VisaLetterRequest, VisaSettings


class VisaSettingsAdmin(ModelAdmin):
    model = VisaSettings
    menu_label = "Letter settings"
    menu_icon = "cog"
    menu_order = 100
    list_display = [
        "conference_year",
        "status",
        "opens_on",
        "request_deadline",
        # Shown because it is the number that decides whether the deadline above is
        # any use, and because getting it wrong is invisible until it is too late.
        "embassy_lead_days",
        "retain_details_days",
    ]
    list_filter = ["status"]
    ordering = ["-conference_year"]


class VisaLetterRequestAdmin(ModelAdmin):
    model = VisaLetterRequest
    menu_label = "Requests"
    menu_icon = "doc-full"
    menu_order = 200
    inspect_view_enabled = False
    list_display = [
        "display_name",
        "conference_year",
        "status",
        "basis",
        "nationality",
        # Never the whole number in a list. The full one is read on the request
        # page, one record at a time, and that read is audited.
        "passport_number_masked",
        "reference",
        "submitted_at",
    ]
    list_filter = ["status", "conference_year", "basis"]
    search_fields = [
        "reference",
        "passport_surname",
        "passport_given_names",
        "user__email",
    ]
    ordering = ["-submitted_at"]


class VisaGroup(ModelAdminGroup):
    menu_label = "Visa letters"
    menu_icon = "mail"
    menu_order = 460
    items = (VisaSettingsAdmin, VisaLetterRequestAdmin)


modeladmin_register(VisaGroup)
