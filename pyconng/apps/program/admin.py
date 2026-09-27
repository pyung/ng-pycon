from django.contrib import admin, messages

from .models import EditionPhoto, Room, SavedTalk, Talk


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ["name", "conference_year", "capacity", "display_order", "is_published"]
    list_filter = ["conference_year", "is_published"]
    prepopulated_fields = {"slug": ("name",)}
    ordering = ["conference_year", "display_order", "name"]


@admin.register(SavedTalk)
class SavedTalkAdmin(admin.ModelAdmin):
    list_display = ["user", "talk", "created_at"]
    list_filter = ["talk__conference_year"]
    search_fields = ["user__email", "talk__title"]
    readonly_fields = ["created_at"]


@admin.register(Talk)
class TalkAdmin(admin.ModelAdmin):
    list_display = ["title", "conference_year", "speaker_display", "track_name",
                    "room", "starts_at", "has_video", "is_published"]
    list_filter = ["conference_year", "is_published", "track_name", "room"]
    search_fields = ["title", "abstract", "speaker_names"]
    filter_horizontal = ["speakers"]
    ordering = ["-conference_year", "display_order", "title"]

    @admin.display(description="Speakers")
    def speaker_display(self, obj):
        return obj.speaker_display

    @admin.display(description="Video", boolean=True)
    def has_video(self, obj):
        return obj.has_video


@admin.register(EditionPhoto)
class EditionPhotoAdmin(admin.ModelAdmin):
    list_display = ["alt", "conference_year", "caption", "display_order", "is_published"]
    list_filter = ["conference_year", "is_published"]
    search_fields = ["alt", "caption"]


# --- Building the programme from accepted proposals -------------------------
# Registered against Proposal so it sits where a CFP chair already works, rather
# than requiring a terminal.

from cfp.models import Proposal  # noqa: E402
from .services import build_programme_from_cfp  # noqa: E402


@admin.action(description="Build programme from accepted proposals (whole edition)")
def build_programme(modeladmin, request, queryset):
    years = sorted({p.conference_year for p in queryset})
    if not years:
        modeladmin.message_user(request, "Select at least one proposal first.", messages.WARNING)
        return
    lines = []
    for year in years:
        result = build_programme_from_cfp(year, actor=request.user)
        lines.append(
            f"{year}: {result['created']} created, {result['updated']} updated, "
            f"{result['skipped']} unchanged"
        )
    modeladmin.message_user(
        request,
        "Talks are created unpublished and unscheduled — set the room, time and "
        "publish when ready. " + "; ".join(lines),
        messages.SUCCESS,
    )


ProposalAdmin = admin.site._registry.get(Proposal)
if ProposalAdmin is not None:
    ProposalAdmin.actions = list(getattr(ProposalAdmin, "actions", None) or []) + [build_programme]
