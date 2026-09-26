"""
Service layer for the Travel Grant system.
"""

import csv
import io

from django.db import models
from django.db.models import F, Sum
from django.utils import timezone

from pyconng.context_processors import CURRENT_YEAR

from .models import (
    GrantReviewerAssignment,
    GrantSettings,
    TravelGrantApplication,
    TravelGrantPayment,
)


class GrantService:
    """Stateless helpers for the Travel Grant workflow."""

    @staticmethod
    def get_current_settings():
        """Return Grant settings for the current year."""
        try:
            settings_obj = GrantSettings.objects.get(conference_year=CURRENT_YEAR)
            if settings_obj.status == GrantSettings.STATUS_OPEN and settings_obj.is_past_deadline:
                settings_obj.status = GrantSettings.STATUS_CLOSED
                settings_obj.save()
            return settings_obj
        except GrantSettings.DoesNotExist:
            return None

    @staticmethod
    def is_grant_open():
        """True when applications are open."""
        settings_obj = GrantService.get_current_settings()
        return settings_obj is not None and settings_obj.is_open

    @staticmethod
    def get_user_application(user):
        """Get the user's application for the current year, if any."""
        if not user.is_authenticated:
            return None
        try:
            return TravelGrantApplication.objects.get(
                user=user, conference_year=CURRENT_YEAR,
            )
        except TravelGrantApplication.DoesNotExist:
            return None

    @staticmethod
    def get_user_cfp_info(user):
        """Check if user has submitted a CFP (by email) and return status."""
        if not user.is_authenticated or not user.email:
            return None
        try:
            from cfp.models import Speaker, Proposal
            speaker = Speaker.objects.get(
                email=user.email, conference_year=CURRENT_YEAR,
            )
            proposals = Proposal.objects.filter(speaker=speaker).order_by("-submitted_at")
            if proposals.exists():
                latest = proposals.first()
                status_display = latest.get_status_display()
                return {"submitted": True, "status": latest.status, "status_display": status_display}
            return {"submitted": False, "status": None, "status_display": None}
        except Exception:
            return {"submitted": False, "status": None, "status_display": None}

    # ------------------------------------------------------------------
    # Admin / Reviewer helpers
    # ------------------------------------------------------------------

    @staticmethod
    def get_dashboard_stats():
        """Return summary metrics for the admin dashboard."""
        qs = TravelGrantApplication.objects.filter(conference_year=CURRENT_YEAR)
        stats = {
            "total": qs.exclude(status=TravelGrantApplication.STATUS_DRAFT).count(),
            "submitted": qs.filter(status=TravelGrantApplication.STATUS_SUBMITTED).count(),
            "under_review": qs.filter(status=TravelGrantApplication.STATUS_UNDER_REVIEW).count(),
            "approved": qs.filter(status=TravelGrantApplication.STATUS_APPROVED).count(),
            "waitlisted": qs.filter(status=TravelGrantApplication.STATUS_WAITLISTED).count(),
            "rejected": qs.filter(status=TravelGrantApplication.STATUS_NOT_SELECTED).count(),
            "withdrawn": qs.filter(status=TravelGrantApplication.STATUS_WITHDRAWN).count(),
        }
        # Total funds requested (submitted + under_review + approved + waitlisted)
        funds_qs = qs.filter(
            status__in=[
                TravelGrantApplication.STATUS_SUBMITTED,
                TravelGrantApplication.STATUS_UNDER_REVIEW,
                TravelGrantApplication.STATUS_APPROVED,
                TravelGrantApplication.STATUS_WAITLISTED,
            ]
        )
        total_requested = funds_qs.aggregate(
            total=Sum(
                F("estimated_transport_cost") + F("estimated_accommodation_cost")
            )
        )["total"]
        stats["total_funds_requested"] = total_requested or 0

        total_approved = qs.filter(
            status__in=[TravelGrantApplication.STATUS_APPROVED, TravelGrantApplication.STATUS_PAID]
        ).aggregate(total=Sum("approved_amount"))["total"]
        stats["total_funds_approved"] = total_approved or 0
        return stats

    @staticmethod
    def auto_assign_reviewers(application, count=2):
        """Assign up to `count` reviewers to an application (round-robin from pool)."""
        from accounts.roles import Role, users_with_role

        year = application.conference_year
        # Chairs decide, so they stay out of the automatic review pool.
        chairs = users_with_role(Role.GRANT_CHAIR, year).values("pk")
        reviewers = list(
            users_with_role(Role.GRANT_REVIEWER, year)
            .exclude(pk__in=chairs)
            .order_by("id")
        )
        if not reviewers:
            return 0
        created = 0
        for i in range(count):
            reviewer = reviewers[i % len(reviewers)]
            _, c = GrantReviewerAssignment.objects.get_or_create(
                reviewer=reviewer,
                application=application,
            )
            if c:
                created += 1
        return created

    @staticmethod
    def on_application_submitted(application):
        """Called when application is submitted: move to under_review, assign reviewers, notify."""
        from grants.emails import send_grant_submission_confirmation

        application.status = TravelGrantApplication.STATUS_UNDER_REVIEW
        application.save(update_fields=["status", "updated_at"])
        GrantService.auto_assign_reviewers(application, count=2)
        send_grant_submission_confirmation(application)

    @staticmethod
    def bulk_decision(application_ids, decision, approved_amounts=None, actor_email=""):
        """Bulk approve / reject / waitlist. approved_amounts: {app_id: amount}."""
        from .models import TravelGrantPayment
        from grants.emails import send_grant_decision

        status_map = {
            "approve": TravelGrantApplication.STATUS_APPROVED,
            "reject": TravelGrantApplication.STATUS_NOT_SELECTED,
            "waitlist": TravelGrantApplication.STATUS_WAITLISTED,
        }
        new_status = status_map.get(decision)
        if not new_status:
            return 0
        approved_amounts = approved_amounts or {}
        count = 0
        for app in TravelGrantApplication.objects.filter(pk__in=application_ids):
            app.status = new_status
            app.decision_at = timezone.now()
            if new_status == TravelGrantApplication.STATUS_APPROVED:
                amount = approved_amounts.get(str(app.pk), app.approved_amount or app.total_requested)
                app.approved_amount = amount
                TravelGrantPayment.objects.get_or_create(
                    application=app,
                    defaults={"amount_paid": amount, "payment_status": TravelGrantPayment.STATUS_PENDING},
                )
            app.save()
            send_grant_decision(app)
            count += 1
        return count

    @staticmethod
    def export_approved_grants(fmt="csv"):
        """Export approved grants as CSV."""
        applications = TravelGrantApplication.objects.filter(
            conference_year=CURRENT_YEAR,
            status__in=[TravelGrantApplication.STATUS_APPROVED, TravelGrantApplication.STATUS_PAID],
        ).select_related("user").order_by("user__email")

        if fmt == "json":
            import json
            data = [
                {
                    "id": str(a.pk),
                    "applicant": a.user.get_full_name() or a.user.email,
                    "email": a.user.email,
                    "country": a.country_of_residence,
                    "requested": str(a.total_requested),
                    "approved": str(a.approved_amount or ""),
                    "status": a.status,
                }
                for a in applications
            ]
            return json.dumps(data, indent=2)

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "ID", "Applicant", "Email", "Country", "Requested", "Approved", "Status",
        ])
        for a in applications:
            writer.writerow([
                str(a.pk),
                a.user.get_full_name() or a.user.email,
                a.user.email,
                a.country_of_residence,
                a.total_requested,
                a.approved_amount or "",
                a.status,
            ])
        return output.getvalue()
