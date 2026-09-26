from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from .models import Meetup, MeetupTalk
from .services import MeetupService


class MeetupTalkInline(admin.TabularInline):
    model = MeetupTalk
    extra = 1
    fields = ["sort_order", "speaker_name", "user", "title", "slides_url", "recording_url", "promoted_proposal"]
    readonly_fields = ["promoted_proposal"]
    autocomplete_fields = ["user"]


@admin.register(Meetup)
class MeetupAdmin(admin.ModelAdmin):
    list_display = ["title", "chapter", "starts_at", "conference_year", "is_published", "has_recap", "attendee_coupon"]
    list_filter = ["conference_year", "is_published", "chapter"]
    search_fields = ["title", "venue", "address", "chapter__city"]
    date_hierarchy = "starts_at"
    inlines = [MeetupTalkInline]
    actions = ["generate_attendee_coupons"]

    @admin.action(description="Generate an attendee discount code")
    def generate_attendee_coupons(self, request, queryset):
        created = []
        for meetup in queryset:
            existed = meetup.attendee_coupon_id is not None
            coupon = meetup.generate_attendee_coupon()
            if not existed:
                created.append(f"{meetup.title}: {coupon.code}")
        if created:
            self.message_user(request, "Created " + "; ".join(created), messages.SUCCESS)
        else:
            self.message_user(
                request, "Every selected meetup already had a code.", messages.INFO,
            )


@admin.register(MeetupTalk)
class MeetupTalkAdmin(admin.ModelAdmin):
    list_display = ["title", "speaker_name", "meetup", "user", "promoted_proposal", "promoted_at"]
    list_filter = ["meetup__conference_year", "meetup__chapter"]
    search_fields = ["title", "speaker_name", "user__email"]
    autocomplete_fields = ["user"]
    actions = ["promote_to_cfp"]

    @admin.action(description="Promote to conference CFP")
    def promote_to_cfp(self, request, queryset):
        promoted, skipped, failed = [], [], []
        for talk in queryset.select_related("meetup", "user"):
            if talk.promoted_proposal_id:
                skipped.append(talk.title)
                continue
            try:
                MeetupService.promote_talk_to_cfp(talk, actor=request.user, request=request)
                promoted.append(talk.title)
            except ValidationError as exc:
                failed.append(f"{talk.title}: {exc.messages[0]}")

        if promoted:
            self.message_user(
                request,
                f"Promoted {len(promoted)} talk(s) to draft proposals; "
                "each speaker has been emailed to finish theirs.",
                messages.SUCCESS,
            )
        if skipped:
            self.message_user(
                request, f"Already promoted: {', '.join(skipped)}", messages.INFO,
            )
        for problem in failed:
            self.message_user(request, problem, messages.ERROR)
