from wagtail_modeladmin.options import (
    ModelAdmin,
    ModelAdminGroup,
    modeladmin_register,
)

from .models import (
    CFPSettings,
    EmailTemplate,
    Proposal,
    ReviewerAssignment,
    Speaker,
    Track,
)


class CFPSettingsAdmin(ModelAdmin):
    model = CFPSettings
    menu_label = "CFP Settings"
    menu_icon = "cog"
    menu_order = 100
    list_display = ["conference_year", "status", "submission_deadline"]
    list_filter = ["status"]


class TrackAdmin(ModelAdmin):
    model = Track
    menu_label = "Tracks"
    menu_icon = "list-ul"
    menu_order = 200
    list_display = ["name", "conference_year", "is_active", "display_order"]
    list_filter = ["conference_year", "is_active"]
    search_fields = ["name"]


class SpeakerAdmin(ModelAdmin):
    model = Speaker
    menu_label = "Speakers"
    menu_icon = "user"
    menu_order = 300
    list_display = [
        "full_name", "user", "country", "first_time_speaker",
        "conference_year",
    ]
    list_filter = ["conference_year", "first_time_speaker"]
    search_fields = ["full_name", "user__email"]


class ProposalAdmin(ModelAdmin):
    model = Proposal
    menu_label = "Proposals"
    menu_icon = "doc-full"
    menu_order = 400
    list_display = [
        "title", "speaker", "track", "format", "status",
        "conference_year", "submitted_at",
    ]
    list_filter = ["status", "conference_year", "format", "track"]
    search_fields = ["title", "speaker__full_name", "speaker__user__email"]


class ReviewerAssignmentAdmin(ModelAdmin):
    model = ReviewerAssignment
    menu_label = "Assignments"
    menu_icon = "tasks"
    menu_order = 600
    list_display = ["reviewer", "proposal", "has_conflict", "assigned_at"]
    list_filter = ["has_conflict"]


class EmailTemplateAdmin(ModelAdmin):
    model = EmailTemplate
    menu_label = "Email Templates"
    menu_icon = "mail"
    menu_order = 700
    list_display = ["name", "template_type", "conference_year"]
    list_filter = ["template_type", "conference_year"]


class CFPAdminGroup(ModelAdminGroup):
    menu_label = "CFP"
    menu_icon = "openquote"
    menu_order = 150
    items = (
        CFPSettingsAdmin,
        TrackAdmin,
        SpeakerAdmin,
        ProposalAdmin,
        ReviewerAssignmentAdmin,
        EmailTemplateAdmin,
    )


modeladmin_register(CFPAdminGroup)
