from django.contrib import admin

from .models import RoleAssignment


@admin.register(RoleAssignment)
class RoleAssignmentAdmin(admin.ModelAdmin):
    list_display = ["user", "role", "conference_year", "is_active", "granted_by", "granted_at"]
    list_filter = ["role", "conference_year", "is_active"]
    search_fields = ["user__email", "user__username", "user__first_name", "user__last_name"]
    autocomplete_fields = ["user", "granted_by"]
    readonly_fields = ["granted_at"]
