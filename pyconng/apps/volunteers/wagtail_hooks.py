"""
Admin surfaces for volunteers.

The coordinator's real workspace is at /volunteers/coordinate/, because deciding an
application means reading it alongside the availability and the shifts, which a
list view cannot show. These exist for the things a list view is good at: finding
one person, checking a team's headcount, and editing the settings.
"""

from wagtail_modeladmin.options import ModelAdmin, ModelAdminGroup, modeladmin_register

from .models import (
    Shift,
    ShiftAssignment,
    VolunteerApplication,
    VolunteerSettings,
    VolunteerTeam,
)


class VolunteerApplicationAdmin(ModelAdmin):
    model = VolunteerApplication
    menu_label = "Applications"
    menu_icon = "group"
    menu_order = 100
    list_display = [
        "full_name",
        "conference_year",
        "status",
        "assigned_team",
        "assigned_lead",
        "tshirt_size",
        "shift_count",
        "submitted_at",
    ]
    list_filter = ["conference_year", "status", "assigned_team", "experience"]
    search_fields = ["full_name", "user__email", "skills"]
    ordering = ["-submitted_at"]
    inspect_view_enabled = True


class VolunteerTeamAdmin(ModelAdmin):
    model = VolunteerTeam
    menu_label = "Teams"
    menu_icon = "site"
    menu_order = 200
    list_display = [
        "name",
        "conference_year",
        "lead",
        "staffing_display",
        "interested_count",
        "display_order",
        "is_active",
    ]
    list_filter = ["conference_year", "is_active"]
    search_fields = ["name"]


class ShiftAdmin(ModelAdmin):
    model = Shift
    menu_label = "Shifts"
    menu_icon = "time"
    menu_order = 300
    list_display = [
        "title",
        "team",
        "starts_at",
        "ends_at",
        "location",
        "assigned_count",
        "capacity",
        "conference_year",
    ]
    list_filter = ["conference_year", "team"]
    search_fields = ["title", "location"]
    ordering = ["starts_at"]


class ShiftAssignmentAdmin(ModelAdmin):
    model = ShiftAssignment
    menu_label = "Roster"
    menu_icon = "list-ul"
    menu_order = 400
    list_display = ["application", "shift", "assigned_at", "assigned_by", "attended_at"]
    list_filter = ["shift__team", "shift__conference_year"]
    search_fields = ["application__full_name", "shift__title"]


class VolunteerSettingsAdmin(ModelAdmin):
    model = VolunteerSettings
    menu_label = "Settings"
    menu_icon = "cogs"
    menu_order = 500
    list_display = [
        "conference_year",
        "status",
        "application_deadline",
        "target_count",
        "shifts_published",
        "certificates_available_from",
    ]


class VolunteersAdminGroup(ModelAdminGroup):
    menu_label = "Volunteers"
    menu_icon = "group"
    menu_order = 190
    items = (
        VolunteerApplicationAdmin,
        VolunteerTeamAdmin,
        ShiftAdmin,
        ShiftAssignmentAdmin,
        VolunteerSettingsAdmin,
    )


modeladmin_register(VolunteersAdminGroup)
