"""
Submitting and handling Code of Conduct reports.

The rules that matter here are about what does *not* travel. A notification email
carries the reference and a one-line summary, never the description: an email is
copied, forwarded and read on a phone in public, and a report may name somebody
the recipient works alongside. The audit trail records that a report arrived and
how its status moved, never its contents, because the trail is read by anyone with
admin access and the report is not.
"""

import logging

from django.db import transaction
from django.urls import reverse

from accounts.roles import Role, users_with_role
from audit.services import record
from editions.current import current_year
from emails.services import send_email, site_url

from .models import IncidentReport, IncidentReportNote

logger = logging.getLogger(__name__)


def coc_team_emails(year=None):
    """
    Addresses of the Code of Conduct team for an edition.

    Falls back to the team with a standing, year-less assignment, so a report
    filed for an edition nobody has staffed yet still reaches somebody.
    """
    year = year or current_year()
    users = users_with_role(Role.COC_TEAM, year)
    return sorted({u.email for u in users if u.email})


def admin_url_for(report):
    """Absolute URL of the report in the Wagtail admin."""
    try:
        path = reverse("conduct_incidentreport_modeladmin_edit", args=[report.pk])
    except Exception:  # noqa: BLE001 - the admin URL name depends on modeladmin
        path = "/admin/"
    return f"{site_url()}{path}"


@transaction.atomic
def submit_report(*, data, user=None, year=None):
    """
    Create a report, notify the team, and acknowledge to the reporter.

    ``data`` is cleaned form data. Returns the saved report. The notification and
    the acknowledgement are best-effort: a report that reached the database has
    been received, and a mail server having a bad minute must not tell the
    reporter their report failed.
    """
    year = year or current_year()

    report = IncidentReport(conference_year=year, **data)
    if not report.is_anonymous and user is not None and getattr(user, "pk", None):
        report.reporter_user = user
        if not report.reporter_email:
            report.reporter_email = user.email or ""
    report.save()

    record(
        target=report,
        action="Code of Conduct report filed",
        actor=None if report.is_anonymous else user,
        actor_label="anonymous" if report.is_anonymous else "",
        new_value=report.get_status_display(),
        note=f"{report.reference}. Contents are not recorded here.",
        conference_year=year,
    )

    notify_team(report)
    acknowledge_reporter(report)
    return report


def notify_team(report):
    """Tell the team a report is waiting, without putting it in an inbox."""
    recipients = coc_team_emails(report.conference_year)
    if not recipients:
        logger.error(
            "Code of Conduct report %s has nobody to go to: no user holds the "
            "%s role for %s.",
            report.reference,
            Role.COC_TEAM.value,
            report.conference_year,
        )
        return False

    return send_email(
        template="conduct/report_filed",
        to=recipients,
        subject=f"New Code of Conduct report: {report.reference}",
        context={
            "reference": report.reference,
            "summary": report.summary_line(),
            "reporter": report.reporter_label,
            "can_reply": report.can_reply,
            "admin_url": admin_url_for(report),
            "conference_year": report.conference_year,
        },
        tags=["conduct", "report_filed"],
        conference_year=report.conference_year,
    )


def acknowledge_reporter(report):
    """Confirm receipt, if the reporter left an address to confirm to."""
    if not report.can_reply:
        return False
    return send_email(
        template="conduct/report_received",
        to=[report.reporter_email],
        subject=f"We have your report ({report.reference})",
        context={
            "reference": report.reference,
            "name": report.reporter_name or "there",
            "conference_year": report.conference_year,
        },
        tags=["conduct", "report_received"],
        conference_year=report.conference_year,
    )


def set_status(report, status, *, actor=None, note=""):
    """
    Move a report's status and record the move.

    Kept as a function rather than left to the admin form so the trail is written
    whichever surface makes the change.
    """
    previous = report.get_status_display()
    report.status = status
    if actor is not None and getattr(actor, "pk", None) and report.handled_by is None:
        report.handled_by = actor
    report.save()

    if note:
        IncidentReportNote.objects.create(
            report=report,
            author=actor if getattr(actor, "pk", None) else None,
            author_label=getattr(actor, "email", "") or "system",
            note=note,
        )

    record(
        target=report,
        action="Code of Conduct report status changed",
        actor=actor,
        old_value=previous,
        new_value=report.get_status_display(),
        note=report.reference,
        conference_year=report.conference_year,
    )
    return report
