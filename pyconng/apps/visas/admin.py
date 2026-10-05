"""
Django admin, for the cases the Wagtail surface deliberately does not cover.

Passport numbers are shown masked here too. Somebody with Django admin access can
of course read the database directly -- the point is not to make it impossible, it
is that the ordinary path does not put passport numbers on screen by default, and
the path that does is audited.
"""

from django.contrib import admin

from .models import VisaLetterRequest, VisaSettings


@admin.register(VisaSettings)
class VisaSettingsAdmin(admin.ModelAdmin):
    list_display = ("conference_year", "status", "opens_on", "request_deadline")
    list_filter = ("status",)


@admin.register(VisaLetterRequest)
class VisaLetterRequestAdmin(admin.ModelAdmin):
    list_display = (
        "display_name", "conference_year", "status", "basis",
        "passport_number_masked", "reference", "submitted_at", "purged_at",
    )
    list_filter = ("status", "conference_year", "basis")
    search_fields = ("reference", "passport_surname", "user__email")
    readonly_fields = ("reference", "submitted_at", "decided_at", "purged_at")
