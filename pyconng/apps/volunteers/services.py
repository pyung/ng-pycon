"""
Deciding on volunteers, and putting them on shifts.

The acceptance path is the interesting one, because accepting somebody has to do
four things at once and none of them can be left to a human to remember: grant the
volunteer role for the edition, issue the free ticket module 4's brief promises,
put them on the team with a named lead, and tell them. Doing it here rather than in
a view means the admin, the queue and any future bulk action all behave the same.

Shift assignment refuses an overlap. That is the one check worth having: a
coordinator building a roster at eleven at night will double-book somebody, and the
cost lands on a volunteer standing in the wrong room.
"""

import logging

from django.db import transaction
from django.utils import timezone

from accounts.models import RoleAssignment
from accounts.roles import Role
from audit.services import record
from editions.current import current_year

from .models import (
    Shift,
    ShiftAssignment,
    VolunteerApplication,
    VolunteerSettings,
    VolunteerTeam,
)

logger = logging.getLogger(__name__)


class VolunteerError(Exception):
    """Something could not be done, with a reason worth showing somebody."""


def settings_for(year=None):
    return VolunteerSettings.for_year(year or current_year())


def is_open(year=None):
    return settings_for(year).is_open


def application_for(user, year=None):
    """This person's application for the edition, or None."""
    if user is None or not getattr(user, "pk", None):
        return None
    return VolunteerApplication.objects.filter(
        user=user, conference_year=year or current_year()
    ).first()


def teams_for(year=None):
    return VolunteerTeam.objects.filter(
        conference_year=year or current_year(), is_active=True
    )


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------

@transaction.atomic
def submit(application, *, by=None):
    """
    Move an application from draft to submitted, and acknowledge it.

    Idempotent: submitting twice does not re-send the acknowledgement, because the
    usual cause is a double-clicked button.
    """
    if application.status != VolunteerApplication.STATUS_DRAFT:
        return application, False

    application.status = VolunteerApplication.STATUS_SUBMITTED
    application.save()

    record(
        target=application,
        action="Volunteer application submitted",
        actor=by or application.user,
        new_value=application.get_status_display(),
        note=application.full_name,
        conference_year=application.conference_year,
    )
    _notify(application, "volunteers/received", "We have your volunteer application")
    return application, True


@transaction.atomic
def withdraw(application, *, by=None):
    """Let somebody take themselves out, and free any shifts they held."""
    if application.status == VolunteerApplication.STATUS_WITHDRAWN:
        return application

    previous = application.get_status_display()
    released = application.shift_assignments.count()
    application.shift_assignments.all().delete()
    application.status = VolunteerApplication.STATUS_WITHDRAWN
    application.save()

    record(
        target=application,
        action="Volunteer application withdrawn",
        actor=by or application.user,
        old_value=previous,
        new_value=application.get_status_display(),
        note=(
            f"{application.full_name}"
            + (f"; {released} shift(s) released" if released else "")
        ),
        conference_year=application.conference_year,
    )
    return application


# ---------------------------------------------------------------------------
# Deciding
# ---------------------------------------------------------------------------

#: Decision -> (email key, subject). Kept as data so adding an outcome does not
#: mean editing a branch, and so every outcome has to have an email.
DECISION_EMAILS = {
    VolunteerApplication.STATUS_ACCEPTED: (
        "volunteers/accepted",
        "You're on the PyCon Nigeria volunteer team",
    ),
    VolunteerApplication.STATUS_WAITLISTED: (
        "volunteers/waitlisted",
        "Your PyCon Nigeria volunteer application",
    ),
    VolunteerApplication.STATUS_NOT_SELECTED: (
        "volunteers/not_selected",
        "Your PyCon Nigeria volunteer application",
    ),
}


@transaction.atomic
def decide(application, status, *, by=None, team=None, lead=None, note="", notify=True):
    """
    Record a decision, and do everything that follows from it.

    Accepting grants the volunteer role for the edition and issues the free
    ticket. Both are idempotent, so re-accepting somebody -- which happens, when a
    coordinator fixes a team assignment -- does not grant twice or issue twice.

    A status other than accepted revokes the role again and releases any shifts,
    because a volunteer who is no longer coming must not stay on a roster somebody
    is counting.
    """
    if status not in dict(VolunteerApplication.STATUS_CHOICES):
        raise VolunteerError(f"{status!r} is not a volunteer application status.")
    if status == VolunteerApplication.STATUS_ACCEPTED and team is None and application.assigned_team is None:
        raise VolunteerError(
            "Accepting somebody needs a team. They will ask which one, and "
            "“we will let you know” is how volunteers drift away."
        )

    previous = application.get_status_display()
    application.status = status
    if note:
        application.decision_note = note
    if team is not None:
        application.assigned_team = team
    if lead is not None:
        application.assigned_lead = lead
    elif application.assigned_team is not None and application.assigned_lead is None:
        application.assigned_lead = application.assigned_team.lead
    if getattr(by, "pk", None):
        application.decided_by = by
    application.save()

    if status == VolunteerApplication.STATUS_ACCEPTED:
        _grant_role(application, by)
        _issue_ticket(application, by)
    else:
        _revoke_role(application, by)
        released = application.shift_assignments.count()
        if released:
            application.shift_assignments.all().delete()
            logger.info(
                "Released %s shift(s) from %s after a %s decision",
                released, application.full_name, status,
            )

    record(
        target=application,
        action="Volunteer application decided",
        actor=by,
        old_value=previous,
        new_value=application.get_status_display(),
        note=(
            f"{application.full_name}"
            + (f"; {application.assigned_team.name}" if application.assigned_team else "")
        ),
        conference_year=application.conference_year,
    )

    if notify and status in DECISION_EMAILS:
        template, subject = DECISION_EMAILS[status]
        _notify(application, template, subject)
    return application


def _grant_role(application, by):
    """Give them the volunteer role for this edition, once."""
    assignment, created = RoleAssignment.objects.get_or_create(
        user=application.user,
        role=Role.VOLUNTEER.value,
        conference_year=application.conference_year,
        defaults={
            "granted_by": by if getattr(by, "pk", None) else None,
            "note": "Accepted as a volunteer.",
        },
    )
    if not created and not assignment.is_active:
        # Reinstating somebody previously revoked, rather than making a second row.
        assignment.is_active = True
        assignment.save()
    return assignment


def _revoke_role(application, by):
    """
    Take the role back, without deleting the record of it having been granted.

    Deactivated rather than removed, so "this person was a volunteer and then was
    not" stays answerable.
    """
    return RoleAssignment.objects.filter(
        user=application.user,
        role=Role.VOLUNTEER.value,
        conference_year=application.conference_year,
        is_active=True,
    ).update(is_active=False)


def _issue_ticket(application, by):
    """
    Issue the free ticket, now that there is an acceptance to trigger it.

    This is the last piece of module 4's auto-issue requirement: the machinery
    existed and ran from a command, and what was missing was the moment to call it
    from. Never raises -- an acceptance has happened and the applicant must be
    told, so a ticketing problem is logged for an organizer rather than rolled back
    over somebody's good news.
    """
    try:
        from tickets.issuing import REASON_VOLUNTEER, issue_complimentary_ticket

        ticket, created = issue_complimentary_ticket(
            user=application.user,
            year=application.conference_year,
            reason=REASON_VOLUNTEER,
            issued_by=by,
            full_name=application.full_name,
            # The acceptance email says they have a ticket, so a second email
            # saying the same thing is noise.
            notify=False,
        )
        if created:
            logger.info("Issued volunteer ticket %s to %s", ticket.order, application.user.email)
        return ticket
    except Exception:  # noqa: BLE001
        logger.exception(
            "Could not issue a volunteer ticket for %s; accepted anyway.",
            application.user.email,
        )
        return None


def volunteer_ticket(application):
    """The complimentary ticket issued to this volunteer, if there is one."""
    try:
        from tickets.issuing import REASON_VOLUNTEER, already_issued

        return already_issued(application.user, application.conference_year, REASON_VOLUNTEER)
    except Exception:  # noqa: BLE001
        return None


def _notify(application, template, subject):
    from emails.services import send_email, site_url

    user = application.user
    if not user or not user.email:
        return False
    team = application.assigned_team
    return send_email(
        template=template,
        to=[user.email],
        subject=subject,
        context={
            "name": application.full_name or user.get_full_name() or user.email,
            "conference_year": application.conference_year,
            "team": team.name if team else "",
            "team_description": team.description if team else "",
            "lead_name": (
                application.lead_contact.get_full_name() or application.lead_contact.email
                if application.lead_contact
                else ""
            ),
            "note": application.decision_note,
            "dashboard_url": f"{site_url()}/volunteers/my-application/",
        },
        tags=["volunteers", application.status],
        conference_year=application.conference_year,
    )


# ---------------------------------------------------------------------------
# Shifts
# ---------------------------------------------------------------------------

def overlapping_assignment(application, shift):
    """
    An existing assignment that clashes with ``shift``, or None.

    The check that matters: a coordinator building a roster late at night will
    double-book somebody, and the volunteer is the one standing in the wrong room.
    """
    for assignment in application.shift_assignments.select_related("shift"):
        if assignment.shift_id != shift.pk and assignment.shift.overlaps(shift):
            return assignment
    return None


@transaction.atomic
def assign_shift(application, shift, *, by=None, allow_overlap=False):
    """
    Put a volunteer on a shift. Returns ``(assignment, created)``.

    Refuses three things: somebody who has not been accepted, a shift already at
    capacity, and an overlap with a shift they already hold. ``allow_overlap`` is
    there because a coordinator sometimes knows better -- a ten-minute overlap
    between two adjacent desks is fine -- and the override is deliberate rather
    than a missing check.
    """
    if not application.is_accepted:
        raise VolunteerError(
            f"{application.full_name} has not been accepted, so they cannot be "
            f"put on a shift yet."
        )
    if shift.conference_year != application.conference_year:
        raise VolunteerError("That shift belongs to a different edition.")

    existing = ShiftAssignment.objects.filter(shift=shift, application=application).first()
    if existing is not None:
        return existing, False

    if shift.is_full:
        raise VolunteerError(
            f"{shift.title} already has its {shift.capacity} volunteer"
            f"{'' if shift.capacity == 1 else 's'}."
        )

    clash = overlapping_assignment(application, shift)
    if clash is not None and not allow_overlap:
        raise VolunteerError(
            f"{application.full_name} is already on {clash.shift.title} "
            f"({clash.shift.starts_at:%a %H:%M}–{clash.shift.ends_at:%H:%M}), "
            f"which overlaps this one."
        )

    assignment = ShiftAssignment.objects.create(
        shift=shift,
        application=application,
        assigned_by=by if getattr(by, "pk", None) else None,
    )
    record(
        target=application,
        action="Volunteer assigned to a shift",
        actor=by,
        new_value=shift.title,
        note=f"{application.full_name}: {shift.starts_at:%a %-d %b %H:%M}",
        conference_year=application.conference_year,
    )
    return assignment, True


@transaction.atomic
def unassign_shift(assignment, *, by=None):
    """Take a volunteer off a shift, and say so in the trail."""
    application = assignment.application
    title = assignment.shift.title
    assignment.delete()
    record(
        target=application,
        action="Volunteer removed from a shift",
        actor=by,
        old_value=title,
        note=application.full_name,
        conference_year=application.conference_year,
    )
    return application


def mark_attended(assignment, *, by=None, attended=True):
    """
    Record whether they actually worked the shift.

    Separate from the assignment because a certificate rests on this and not on
    being rostered: one for somebody who did not come devalues everyone else's.
    """
    assignment.attended_at = timezone.now() if attended else None
    assignment.save(update_fields=["attended_at"])
    record(
        target=assignment.application,
        action="Volunteer attendance recorded" if attended else "Volunteer attendance cleared",
        actor=by,
        new_value=assignment.shift.title,
        note=assignment.application.full_name,
        conference_year=assignment.application.conference_year,
    )
    return assignment


@transaction.atomic
def publish_shifts(year=None, *, by=None, notify=True):
    """
    Make the roster visible, and tell every volunteer on it.

    One switch and one email, because a roster is only useful once people have been
    told to look. Emails only the volunteers who actually have a shift: telling
    somebody the roster is out when they are not on it is worse than saying nothing.
    Returns the number told.
    """
    year = year or current_year()
    settings_obj = settings_for(year)
    settings_obj.shifts_published = True
    settings_obj.save(update_fields=["shifts_published"])

    told = 0
    if notify:
        rostered = (
            VolunteerApplication.objects.filter(
                conference_year=year,
                status=VolunteerApplication.STATUS_ACCEPTED,
                shift_assignments__isnull=False,
            )
            .distinct()
            .select_related("assigned_team", "assigned_lead", "user")
        )
        for application in rostered:
            if _notify(application, "volunteers/shifts_published", "Your PyCon Nigeria volunteer shifts"):
                told += 1

    record(
        target=settings_obj,
        action="Volunteer roster published",
        actor=by,
        new_value=f"{told} volunteer(s) told",
        conference_year=year,
    )
    return told


def unpublish_shifts(year=None, *, by=None):
    """
    Hide the roster again, for a rebuild.

    Deliberately silent: nobody needs an email saying the roster they were told
    about has gone away for an hour.
    """
    year = year or current_year()
    settings_obj = settings_for(year)
    settings_obj.shifts_published = False
    settings_obj.save(update_fields=["shifts_published"])
    record(
        target=settings_obj,
        action="Volunteer roster hidden for rebuilding",
        actor=by,
        conference_year=year,
    )
    return settings_obj


def roster_for(application):
    """This volunteer's shifts in time order, for the dashboard."""
    return (
        application.shift_assignments.select_related("shift", "shift__team")
        .order_by("shift__starts_at")
    )


def staffing_gaps(year=None):
    """
    Shifts that do not have the people they need, soonest first.

    The question a coordinator should be able to answer in one glance, because the
    alternative is finding out on the morning.
    """
    year = year or current_year()
    gaps = []
    for shift in (
        Shift.objects.filter(conference_year=year)
        .select_related("team")
        .prefetch_related("assignments")
        .order_by("starts_at")
    ):
        if shift.is_understaffed:
            gaps.append(
                {
                    "shift": shift,
                    "needed": shift.capacity - shift.assigned_count,
                    "assigned": shift.assigned_count,
                }
            )
    return gaps


def overview(year=None):
    """Counts for the coordinator's dashboard."""
    year = year or current_year()
    applications = VolunteerApplication.objects.filter(conference_year=year)
    by_status = {
        status: applications.filter(status=status).count()
        for status, _ in VolunteerApplication.STATUS_CHOICES
    }
    shifts = Shift.objects.filter(conference_year=year)
    gaps = staffing_gaps(year)
    settings_obj = settings_for(year)
    accepted = by_status.get(VolunteerApplication.STATUS_ACCEPTED, 0)
    return {
        "settings": settings_obj,
        "by_status": by_status,
        "total": applications.count(),
        "awaiting": applications.filter(
            status__in=VolunteerApplication.OPEN_STATUSES
        ).count(),
        "accepted": accepted,
        "target": settings_obj.target_count,
        "still_needed": max(settings_obj.target_count - accepted, 0)
        if settings_obj.target_count
        else 0,
        "teams": list(teams_for(year)),
        "shift_count": shifts.count(),
        "understaffed": gaps,
        "unassigned_accepted": applications.filter(
            status=VolunteerApplication.STATUS_ACCEPTED, shift_assignments__isnull=True
        ).distinct(),
    }
