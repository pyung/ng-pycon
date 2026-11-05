"""
Wagtail admin for visa letters.

The settings are fully editable here, which is the point: the window, the
letterhead, who signs, the retention period and whether the public verification
page is on are all things an officer needs to change without a developer and
without a deploy.

The requests are a different matter, and this file is the second place -- after
``conduct`` -- that needs its own permission rules.

A default modeladmin on ``VisaLetterRequest`` is worse than no admin at all. It
puts the raw passport number, date of birth and expiry on an edit form, readable
and writable with **no audit entry** -- which is the one thing the module promises
cannot happen. It also exposes ``status``, ``reference`` and ``decided_by``, so a
bare save could mark a request approved and mint a reference without the
eligibility re-check, without the trail, and without telling the applicant. And
all of it gated on the Django ``change_visaletterrequest`` permission rather than
on :data:`Role.VISA_OFFICER`, which hands any staff account a way round the narrow
role the module was built on.

So requests are **read-only here, for the visa officer only**, and the sensitive
fields are kept off the inspect view entirely. Deciding happens at
``/visa/queue/``, which re-checks eligibility, writes the trail and sends the
email. Reading a passport number happens there too, through the one endpoint that
records who looked.
"""

from wagtail import hooks
from wagtail_modeladmin.helpers import PermissionHelper
from wagtail_modeladmin.options import ModelAdmin, ModelAdminGroup, modeladmin_register

from accounts.roles import Role, roles_for

from .models import VisaLetterRequest, VisaSettings


def is_visa_officer(user):
    """
    True only for a named visa officer.

    Superusers are deliberately not waved through, for the same reason as the Code
    of Conduct team: this is read access to the most sensitive data the site holds,
    and it belongs to people who were asked to hold it.
    """
    if user is None or not user.is_authenticated:
        return False
    return Role.VISA_OFFICER in roles_for(user)


class VisaOfficerReadOnly(PermissionHelper):
    """List and inspect for the visa officer; create, edit and delete for nobody."""

    def user_can_list(self, user):
        return is_visa_officer(user)

    def user_can_inspect_obj(self, user, obj):
        return is_visa_officer(user)

    def user_can_create(self, user):
        # A request is made by the applicant, which is what records their basis.
        return False

    def user_can_edit_obj(self, user, obj):
        # Never here. An edit form on this model is a passport number on a page
        # with no audit entry, and a decision with no eligibility check.
        return False

    def user_can_delete_obj(self, user, obj):
        # The reference has to keep resolving for an embassy that checks it later,
        # and the trail of who approved what has its own reasons to survive.
        # Passport details are removed by `purge_visa_details`, not by deletion.
        return False


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
    permission_helper_class = VisaOfficerReadOnly
    menu_label = "Requests"
    menu_icon = "doc-full"
    menu_order = 200
    list_display = [
        "display_name",
        "conference_year",
        "status",
        "basis",
        "nationality",
        # Never the whole number in a list. The full one is read at /visa/queue/,
        # one record at a time, and that read is audited.
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
    inspect_view_enabled = True
    #: Deliberately excludes passport_number, date_of_birth and passport_expiry.
    #: Everything here is either not sensitive or already masked.
    inspect_view_fields = [
        "user",
        "conference_year",
        "status",
        "basis",
        "passport_surname",
        "passport_given_names",
        "nationality",
        "passport_number_masked",
        "embassy_city",
        "embassy_country",
        "arrival_on",
        "departure_on",
        "accommodation",
        "notes",
        "reference",
        "submitted_at",
        "decided_at",
        "decided_by",
        "decision_note",
        "letter_issued_at",
        "letter_downloads",
        "purged_at",
    ]

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if not is_visa_officer(request.user):
            return queryset.none()
        return queryset


class VisaGroup(ModelAdminGroup):
    menu_label = "Visa letters"
    menu_icon = "mail"
    menu_order = 460
    items = (VisaSettingsAdmin, VisaLetterRequestAdmin)


modeladmin_register(VisaGroup)


@hooks.register("construct_main_menu")
def hide_requests_from_everyone_else(request, menu_items):
    """
    Keep the Requests entry out of the menu for anyone who is not a visa officer.

    ModelAdmin already hides what a user cannot list, but a visible entry that
    403s still tells everybody the passport data is there and roughly how much.
    The settings stay visible: a letterhead is not sensitive.
    """
    if is_visa_officer(request.user):
        return
    for item in menu_items:
        children = getattr(getattr(item, "menu", None), "_registered_menu_items", None)
        if children is None:
            continue
        children[:] = [
            child for child in children
            if getattr(child, "name", "") != "visaletterrequest"
        ]
