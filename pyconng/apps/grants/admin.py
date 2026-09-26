from django.contrib import admin

from .models import (
    GrantReviewerAssignment,
    GrantSettings,
    TravelGrantApplication,
    TravelGrantPayment,
    TravelGrantReview,
)


@admin.register(GrantSettings)
class GrantSettingsAdmin(admin.ModelAdmin):
    list_display = [
        "conference_year", "status", "application_deadline",
        "max_grant_budget", "max_per_applicant", "updated_at",
    ]
    list_filter = ["status"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(TravelGrantApplication)
class TravelGrantApplicationAdmin(admin.ModelAdmin):
    list_display = [
        "user", "conference_year", "status", "country_of_residence",
        "total_requested_display", "submitted_at",
    ]
    list_filter = ["status", "conference_year"]
    search_fields = ["user__email", "user__first_name", "user__last_name"]
    readonly_fields = ["id", "created_at", "updated_at", "submitted_at", "decision_at"]

    def total_requested_display(self, obj):
        return f"₦{obj.total_requested:,.2f}"

    total_requested_display.short_description = "Total Requested"


@admin.register(GrantReviewerAssignment)
class GrantReviewerAssignmentAdmin(admin.ModelAdmin):
    list_display = ["reviewer", "application", "has_conflict", "assigned_at"]


@admin.register(TravelGrantReview)
class TravelGrantReviewAdmin(admin.ModelAdmin):
    list_display = ["assignment", "weighted_score", "need_score", "impact_score", "contribution_score", "diversity_score"]


@admin.register(TravelGrantPayment)
class TravelGrantPaymentAdmin(admin.ModelAdmin):
    list_display = ["application", "payment_status", "amount_paid", "reference", "paid_at"]
