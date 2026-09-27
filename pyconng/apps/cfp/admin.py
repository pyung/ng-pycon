from django.contrib import admin

from .models import (
    CFPSettings,
    Proposal,
    ProposalSnapshot,
    Review,
    ReviewerAssignment,
    Speaker,
    Track,
)


@admin.register(CFPSettings)
class CFPSettingsAdmin(admin.ModelAdmin):
    list_display = ["conference_year", "status", "submission_deadline", "updated_at"]
    list_filter = ["status"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(Track)
class TrackAdmin(admin.ModelAdmin):
    list_display = ["name", "conference_year", "is_active", "display_order"]
    list_filter = ["conference_year", "is_active"]
    search_fields = ["name"]
    ordering = ["conference_year", "display_order"]


@admin.register(Speaker)
class SpeakerAdmin(admin.ModelAdmin):
    list_display = [
        "full_name", "user", "country", "first_time_speaker",
        "conference_year", "created_at",
    ]
    list_filter = ["conference_year", "first_time_speaker", "country"]
    search_fields = ["full_name", "user__email", "organisation"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(Proposal)
class ProposalAdmin(admin.ModelAdmin):
    list_display = [
        "title", "speaker", "track", "format", "duration",
        "status", "conference_year", "submitted_at",
    ]
    list_filter = ["status", "conference_year", "format", "audience_level", "track"]
    search_fields = ["title", "speaker__full_name", "speaker__user__email"]
    readonly_fields = ["id", "created_at", "updated_at", "submitted_at"]
    ordering = ["-submitted_at"]


@admin.register(ProposalSnapshot)
class ProposalSnapshotAdmin(admin.ModelAdmin):
    list_display = ["proposal", "snapshot_type", "created_at"]
    list_filter = ["snapshot_type"]
    readonly_fields = ["proposal", "data", "snapshot_type", "created_at"]


@admin.register(ReviewerAssignment)
class ReviewerAssignmentAdmin(admin.ModelAdmin):
    list_display = ["reviewer", "proposal", "assigned_at", "has_conflict"]
    list_filter = ["has_conflict"]
    search_fields = ["reviewer__username", "reviewer__email", "proposal__title"]


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ["assignment", "weighted_score", "relevance", "clarity", "depth", "speaker_readiness", "updated_at"]
    list_filter = ["relevance", "clarity", "depth", "speaker_readiness"]
    readonly_fields = ["weighted_score", "created_at", "updated_at"]


