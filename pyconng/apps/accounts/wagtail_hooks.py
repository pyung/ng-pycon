from wagtail_modeladmin.options import ModelAdmin, modeladmin_register

from .models import RoleAssignment


class RoleAssignmentAdmin(ModelAdmin):
    model = RoleAssignment
    menu_label = "Roles"
    menu_icon = "group"
    menu_order = 140
    list_display = ["user", "role", "conference_year", "is_active", "granted_by", "granted_at"]
    list_filter = ["role", "conference_year", "is_active"]
    search_fields = ["user__email", "user__username", "user__first_name", "user__last_name"]


modeladmin_register(RoleAssignmentAdmin)
