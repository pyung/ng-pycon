"""
Service layer for the Travel Grant system.
"""

import csv
import io

from django.db import models
from django.db.models import F, Sum
from django.utils import timezone

from editions.current import current_year

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
            settings_obj = GrantSettings.objects.get(conference_year=current_year())
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
                user=user, conference_year=current_year(),
            )
        except TravelGrantApplication.DoesNotExist:
            return None

    @staticmethod
    def get_user_cfp_info(user):
        """Whether this user has a proposal this year, and its latest status."""
        if not user.is_authenticated:
            return None

        from cfp.models import Proposal

        latest = (
            Proposal.objects
            .filter(speaker__user=user, conference_year=current_year())
            .order_by("-submitted_at")
            .first()
        )
        if latest is None:
            return {"submitted": False, "status": None, "status_display": None}
        return {
            "submitted": True,
            "status": latest.status,
            "status_display": latest.get_status_display(),
        }

    # ------------------------------------------------------------------
    # Admin / Reviewer helpers
    # ------------------------------------------------------------------

    @staticmethod
    def get_dashboard_stats():
        """Return summary metrics for the admin dashboard."""
        qs = TravelGrantApplication.objects.filter(conference_year=current_year())
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
            status__in=TravelGrantApplication.COMMITTED_STATUSES
        ).aggregate(total=Sum("approved_amount"))["total"]
        stats["total_funds_approved"] = total_approved or 0

        # The offer lifecycle, and the budget figure the dashboard never had.
        stats["accepted"] = qs.filter(status=TravelGrantApplication.STATUS_ACCEPTED).count()
        stats["declined"] = qs.filter(status=TravelGrantApplication.STATUS_DECLINED).count()
        stats["lapsed"] = qs.filter(status=TravelGrantApplication.STATUS_LAPSED).count()
        stats["paid"] = qs.filter(status=TravelGrantApplication.STATUS_PAID).count()
        stats["awaiting_acceptance"] = qs.filter(
            status__in=TravelGrantApplication.AWAITING_ACCEPTANCE_STATUSES
        ).count()
        stats["offers_expiring"] = sum(
            1
            for a in qs.filter(
                status__in=TravelGrantApplication.AWAITING_ACCEPTANCE_STATUSES,
                acceptance_deadline__isnull=False,
            )
            if (a.days_left_to_accept or 0) <= 3
        )

        from . import budget as budget_module

        stats["budget"] = budget_module.status(current_year())
        stats["promotable"] = len(budget_module.affordable_waitlisted(current_year()))
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
    def bulk_decision(application_ids, decision, approved_amounts=None, actor_email="", actor=None):
        """
        Bulk approve, reject or waitlist. ``approved_amounts`` is ``{app_id: amount}``.

        Returns ``(count, refused)``, where ``refused`` is a list of
        ``(application, reason)`` the budget would not allow. Refusing per row rather
        than raising matters here: a chair deciding twenty applications needs to know
        which three did not fit, not to have the whole action fail on the first.

        Approving now makes an *offer* with an acceptance deadline rather than a
        finished award, and every amount is checked against the budget. Before this,
        ``max_grant_budget`` was stored, displayed and never consulted, so a bulk
        approval could overspend in silence.
        """
        from audit.services import record
        from grants.emails import send_grant_decision

        from . import offers
        from .budget import BudgetError

        status_map = {
            "approve": TravelGrantApplication.STATUS_APPROVED,
            "reject": TravelGrantApplication.STATUS_NOT_SELECTED,
            "waitlist": TravelGrantApplication.STATUS_WAITLISTED,
        }
        # Spelled out rather than derived: "waitlist" + "d" reads "Waitlistd".
        action_labels = {
            "reject": "Rejected travel grant",
            "waitlist": "Waitlisted travel grant",
        }
        new_status = status_map.get(decision)
        if not new_status:
            return 0, []
        approved_amounts = approved_amounts or {}
        count = 0
        refused = []

        for app in TravelGrantApplication.objects.filter(pk__in=application_ids):
            if decision == "approve":
                amount = approved_amounts.get(
                    str(app.pk), app.approved_amount or app.total_requested
                )
                try:
                    offers.make_offer(app, amount, by=actor)
                except BudgetError as exc:
                    refused.append((app, str(exc)))
                    continue
                count += 1
                continue

            previous_status = app.status
            app.status = new_status
            app.decision_at = timezone.now()
            app.save()

            record(
                target=app,
                action=action_labels[decision],
                actor=actor,
                actor_label="" if getattr(actor, "pk", None) else (actor_email or "system"),
                old_value=previous_status,
                new_value=new_status,
            )
            send_grant_decision(app)
            count += 1

        return count, refused

    @staticmethod
    def export_approved_grants(fmt="csv"):
        """
        Export the grants that cost money, for finance and for sponsor reports.

        Covers accepted and paid rather than approved: an unanswered offer may lapse,
        and exporting it as a grant given produces a figure that quietly goes wrong.
        Carries the grant type, city and gender the reports need, and the payout
        details finance needs, which were all absent before.
        """
        applications = TravelGrantApplication.objects.filter(
            conference_year=current_year(),
            status__in=[
                TravelGrantApplication.STATUS_ACCEPTED,
                TravelGrantApplication.STATUS_PAID,
            ],
        ).select_related("user", "payment").order_by("user__email")

        columns = [
            "ID", "Applicant", "Email", "Grant type", "City", "Country", "Gender",
            "Speaking", "First time", "Requested", "Approved", "Status",
            "Accepted at", "Payment status", "Receipt",
        ]

        def row_for(a):
            payment = getattr(a, "payment", None)
            return [
                str(a.pk),
                a.user.get_full_name() or a.user.email,
                a.user.email,
                a.get_grant_type_display(),
                a.city,
                a.country_of_residence,
                a.gender_display,
                "yes" if a.is_speaking else "no",
                "yes" if a.first_time_pycon else "no",
                a.total_requested,
                a.approved_amount or "",
                a.get_status_display(),
                a.accepted_at.date() if a.accepted_at else "",
                payment.get_payment_status_display() if payment else "",
                "yes" if payment and payment.receipt else "no",
            ]

        if fmt == "json":
            import json

            return json.dumps(
                [
                    dict(zip(columns, [str(value) for value in row_for(a)]))
                    for a in applications
                ],
                indent=2,
            )

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(columns)
        for a in applications:
            writer.writerow(row_for(a))
        return output.getvalue()
