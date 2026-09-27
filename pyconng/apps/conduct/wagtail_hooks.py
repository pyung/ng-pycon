"""
Admin surface for Code of Conduct reports.

The only admin in this project with its own permission rules, and it needs them:
every other modeladmin here is visible to anybody who can reach /admin/, which is
right for sponsors and proposals and wrong for incident reports. A report may name
an organizer, so access is granted to the Code of Conduct team by name -- it is not
inherited from being an organizer or a superuser.

Reports are read and annotated here, never created: one is only ever filed through
the public form. Nor are they deleted, so a record cannot be made to disappear by
whoever it is about.
"""

from wagtail import hooks
from wagtail_modeladmin.helpers import PermissionHelper
from wagtail_modeladmin.options import ModelAdmin, modeladmin_register

from accounts.roles import Role, roles_for

from .models import IncidentReport


def is_coc_team(user):
    """
    True only for a member of the Code of Conduct team.

    Superusers are deliberately not waved through. A report about an organizer is
    read by the people the community was told would read it, and that list is
    kept as role assignments rather than inferred from account privileges.
    """
    if user is None or not user.is_authenticated:
        return False
    return Role.COC_TEAM in roles_for(user)


class CoCTeamOnly(PermissionHelper):
    """Role-gated access: list and edit for the team, create and delete for nobody."""

    def user_can_list(self, user):
        return is_coc_team(user)

    def user_can_create(self, user):
        # A report is filed through the public form, which is what stamps the
        # reference and notifies the team. Hand-made rows would skip both.
        return False

    def user_can_inspect_obj(self, user, obj):
        return is_coc_team(user)

    def user_can_edit_obj(self, user, obj):
        return is_coc_team(user)

    def user_can_delete_obj(self, user, obj):
        # Never. The whole point of the record is that it cannot be made to go away.
        return False


class IncidentReportAdmin(ModelAdmin):
    model = IncidentReport
    permission_helper_class = CoCTeamOnly
    menu_label = "Conduct reports"
    menu_icon = "warning"
    menu_order = 185
    list_display = [
        "reference",
        "conference_year",
        "status",
        "reporter_label",
        "submitted_at",
        "handled_by",
    ]
    list_filter = ["conference_year", "status", "is_anonymous"]
    search_fields = ["reference", "incident_where", "incident_when"]
    ordering = ["-submitted_at"]
    inspect_view_enabled = True
    #: No search on the description on purpose: an admin search box is the wrong
    #: place to pull phrases out of an incident report.
    list_per_page = 25

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if not is_coc_team(request.user):
            return queryset.none()
        return queryset


modeladmin_register(IncidentReportAdmin)


@hooks.register("construct_main_menu")
def hide_conduct_reports_from_everyone_else(request, menu_items):
    """
    Belt and braces on the menu.

    ModelAdmin already hides an item a user cannot list, but the menu is the part
    people see, and a "Conduct reports" entry that 403s tells everyone the reports
    exist and roughly how many there are.
    """
    if is_coc_team(request.user):
        return
    menu_items[:] = [item for item in menu_items if item.name != "conduct-reports"]
