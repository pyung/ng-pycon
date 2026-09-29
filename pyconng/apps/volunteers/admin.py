"""
Django admin for volunteers, for the actions a list view can carry.

Deciding an application is deliberately not one of them: it grants a role, issues a
ticket and sends an email, and doing that from a dropdown on a list of forty rows
is how somebody gets accepted onto a team nobody meant to put them on. The
coordinator screen at /volunteers/coordinate/ is where that happens.
"""

from django.contrib import admin

from .models import (
    Shift,
    ShiftAssignment,
    VolunteerApplication,
    VolunteerAvailability,
    VolunteerSettings,
    VolunteerTeam,
)


@admin.register(VolunteerSettings)
class VolunteerSettingsAdmin(admin.ModelAdmin):
    list_display = [
        "conference_year",
        "status",
        "application_deadline",
        "target_count",
        "shifts_published",
        "certificates_available_from",
    ]
    fieldsets = [
        (None, {"fields": ["conference_year", "status", "application_deadline"]}),
        (
            "What the page says",
            {"fields": ["intro", "what_we_ask", "what_you_get"]},
        ),
        (
            "The ask",
            {
                "fields": [
                    "target_count",
                    "max_team_choices",
                    "setup_days_before",
                    "teardown_days_after",
                ],
                "description": (
                    "The set-up and pack-down days are added to the availability "
                    "question on the form, taken from the edition's own dates."
                ),
            },
        ),
        (
            "Roster and certificates",
            {
                "fields": ["shifts_published", "certificates_available_from"],
                "description": (
                    "Publishing the roster from the coordinator screen also emails "
                    "the volunteers on it. Switching it here does not."
                ),
            },
        ),
    ]


class VolunteerAvailabilityInline(admin.TabularInline):
    model = VolunteerAvailability
    extra = 0


class ShiftAssignmentInline(admin.TabularInline):
    model = ShiftAssignment
    extra = 0
    fields = ["shift", "attended_at", "assigned_by"]
    readonly_fields = ["assigned_by"]


@admin.register(VolunteerApplication)
class VolunteerApplicationAdmin(admin.ModelAdmin):
    list_display = [
        "full_name",
        "user",
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
    filter_horizontal = ["teams"]
    readonly_fields = ["submitted_at", "decided_at", "created_at", "updated_at"]
    inlines = [VolunteerAvailabilityInline, ShiftAssignmentInline]
    ordering = ["-submitted_at"]


@admin.register(VolunteerTeam)
class VolunteerTeamAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "conference_year",
        "lead",
        "staffing_display",
        "interested_count",
        "shortfall",
        "display_order",
        "is_active",
    ]
    list_filter = ["conference_year", "is_active"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "team",
        "starts_at",
        "ends_at",
        "location",
        "assigned_count",
        "capacity",
        "is_understaffed",
        "conference_year",
    ]
    list_filter = ["conference_year", "team"]
    search_fields = ["title", "location"]
    ordering = ["starts_at"]


@admin.register(ShiftAssignment)
class ShiftAssignmentAdmin(admin.ModelAdmin):
    list_display = ["application", "shift", "assigned_at", "assigned_by", "attended_at"]
    list_filter = ["shift__team", "shift__conference_year"]
    search_fields = ["application__full_name", "shift__title"]
    readonly_fields = ["assigned_at"]
    actions = ["mark_attended", "clear_attended"]

    @admin.action(description="Record that these volunteers worked their shift")
    def mark_attended(self, request, queryset):
        from .services import mark_attended

        count = 0
        for assignment in queryset:
            mark_attended(assignment, by=request.user, attended=True)
            count += 1
        self.message_user(request, f"{count} shift(s) marked as worked.")

    @admin.action(description="Clear the attendance record on these shifts")
    def clear_attended(self, request, queryset):
        from .services import mark_attended

        count = 0
        for assignment in queryset.filter(attended_at__isnull=False):
            mark_attended(assignment, by=request.user, attended=False)
            count += 1
        self.message_user(request, f"{count} attendance record(s) cleared.")
