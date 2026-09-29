"""
Views for the call for volunteers.

Split the way the work splits: five pages for somebody offering to help, and four
for the coordinator who places them. The applicant's pages never require a role;
the coordinator's require the coordinator role or an Organizer.
"""

import csv
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST, require_http_methods

from editions.current import current_year, edition_for_year

from . import services
from .decorators import coordinator_required, volunteers_open_required
from .forms import ShiftForm, VolunteerApplicationForm, VolunteerDecisionForm
from .models import Shift, VolunteerApplication

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------

def landing(request):
    """
    What volunteering involves, and the way in.

    Open to anyone, signed in or not: somebody deciding whether to volunteer should
    not have to make an account to read what it entails.
    """
    year = current_year()
    settings_obj = services.settings_for(year)
    existing = services.application_for(request.user, year) if request.user.is_authenticated else None
    return render(
        request,
        "volunteers/landing.html",
        {
            "settings_obj": settings_obj,
            "teams": services.teams_for(year),
            "application": existing,
            "conference_year": year,
            "edition": edition_for_year(year),
        },
    )


def closed(request):
    """Shown instead of a form that could not be submitted."""
    year = current_year()
    return render(
        request,
        "volunteers/closed.html",
        {
            "settings_obj": services.settings_for(year),
            "application": services.application_for(request.user, year)
            if request.user.is_authenticated
            else None,
            "conference_year": year,
        },
    )


@login_required
@volunteers_open_required
@require_http_methods(["GET", "POST"])
def apply(request):
    """
    Apply, or edit an application that has not been decided yet.

    One form for both. A volunteer whose availability changes in March should fix
    it themselves rather than emailing somebody, so the same page serves until a
    decision is made.
    """
    year = current_year()
    settings_obj = services.settings_for(year)
    existing = services.application_for(request.user, year)

    if existing is not None and not existing.is_editable:
        messages.info(
            request, "Your application has been decided, so it can no longer be edited."
        )
        return redirect("volunteers:my_application")

    if request.method == "POST":
        form = VolunteerApplicationForm(
            request.POST,
            instance=existing,
            conference_year=year,
            settings_obj=settings_obj,
        )
        if form.is_valid():
            application = form.save(commit=False)
            application.user = request.user
            application.conference_year = year
            if not application.status:
                application.status = VolunteerApplication.STATUS_DRAFT
            application.save()
            form.save_m2m()
            form.save_availability(application)

            application, sent = services.submit(application, by=request.user)
            messages.success(
                request,
                "Thank you — your application is in. We will be in touch."
                if sent
                else "Your application has been updated.",
            )
            return redirect("volunteers:my_application")
    else:
        form = VolunteerApplicationForm(
            instance=existing, conference_year=year, settings_obj=settings_obj
        )

    return render(
        request,
        "volunteers/apply.html",
        {
            "form": form,
            "settings_obj": settings_obj,
            "application": existing,
            "conference_year": year,
            "edition": edition_for_year(year),
            "editing": existing is not None,
        },
    )


@login_required
def my_application(request):
    """
    Where a volunteer sees where they stand, and their shifts once published.

    The roster is gated on ``shifts_published``: a volunteer who reads a draft
    roster turns up at the wrong hour, and rosters get rebuilt several times.
    """
    year = current_year()
    application = services.application_for(request.user, year)
    if application is None:
        return redirect("volunteers:landing")

    settings_obj = services.settings_for(year)
    return render(
        request,
        "volunteers/my_application.html",
        {
            "application": application,
            "settings_obj": settings_obj,
            "roster": services.roster_for(application) if settings_obj.shifts_published else [],
            "shifts_published": settings_obj.shifts_published,
            "ticket": services.volunteer_ticket(application),
            "certificate_ready": (
                settings_obj.certificates_available and application.worked_a_shift
            ),
            "conference_year": year,
        },
    )


@login_required
@require_POST
def withdraw(request):
    """Take yourself out, releasing any shifts you were holding."""
    year = current_year()
    application = services.application_for(request.user, year)
    if application is None:
        raise Http404("No application to withdraw.")
    services.withdraw(application, by=request.user)
    messages.success(
        request,
        "Your application has been withdrawn. You are welcome to apply again while "
        "the call is open.",
    )
    return redirect("volunteers:landing")


@login_required
def certificate(request):
    """
    A printable certificate of participation.

    Only for somebody who actually worked a shift, and only after the coordinator
    opens them. HTML with a print stylesheet, for the same reason the invoices are:
    a browser's "save as PDF" is a perfectly good file.
    """
    year = current_year()
    application = services.application_for(request.user, year)
    if application is None or not application.is_accepted:
        raise Http404("No certificate here.")

    settings_obj = services.settings_for(year)
    if not settings_obj.certificates_available:
        messages.info(
            request, "Certificates are not available yet. We will let you know."
        )
        return redirect("volunteers:my_application")
    if not application.worked_a_shift:
        messages.info(
            request,
            "Our records do not show a completed shift for you. If that looks wrong, "
            "tell your team lead and we will correct it.",
        )
        return redirect("volunteers:my_application")

    return render(
        request,
        "volunteers/certificate.html",
        {
            "application": application,
            "edition": edition_for_year(year),
            "shifts": services.roster_for(application),
            "hours": sum(a.shift.duration_hours for a in services.roster_for(application)),
            "conference_year": year,
        },
    )


# ---------------------------------------------------------------------------
# Coordinating
# ---------------------------------------------------------------------------

@coordinator_required
def coordinator_dashboard(request):
    """
    One screen: who is waiting, how the teams stand, and which shifts are short.

    The staffing gaps are the point. An under-staffed Saturday morning is not
    something to discover on Saturday morning.
    """
    year = current_year()
    status = request.GET.get("status") or ""
    team = request.GET.get("team") or ""

    applications = (
        VolunteerApplication.objects.filter(conference_year=year)
        .select_related("assigned_team", "user", "assigned_lead")
        .prefetch_related("teams")
    )
    if status:
        applications = applications.filter(status=status)
    if team:
        applications = applications.filter(teams__slug=team).distinct()

    return render(
        request,
        "volunteers/coordinator_dashboard.html",
        {
            "overview": services.overview(year),
            "applications": applications,
            "statuses": VolunteerApplication.STATUS_CHOICES,
            "selected_status": status,
            "selected_team": team,
            "conference_year": year,
        },
    )


@coordinator_required
@require_http_methods(["GET", "POST"])
def review_detail(request, application_id):
    """One application: read it, decide it, and place them on shifts."""
    year = current_year()
    application = get_object_or_404(
        VolunteerApplication.objects.select_related("assigned_team", "user"),
        pk=application_id,
        conference_year=year,
    )

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "assign_shift":
            return _assign_shift(request, application)
        if action == "unassign_shift":
            return _unassign_shift(request, application)
        if action == "attendance":
            return _set_attendance(request, application)

        form = VolunteerDecisionForm(request.POST, application=application)
        if form.is_valid():
            try:
                services.decide(
                    application,
                    form.cleaned_data["decision"],
                    by=request.user,
                    team=form.cleaned_data.get("team"),
                    lead=form.cleaned_data.get("lead"),
                    note=form.cleaned_data.get("note", ""),
                )
            except services.VolunteerError as exc:
                messages.error(request, str(exc))
            else:
                if form.cleaned_data.get("internal_note"):
                    application.internal_note = form.cleaned_data["internal_note"]
                    application.save(update_fields=["internal_note"])
                messages.success(
                    request,
                    f"{application.full_name} is now "
                    f"{application.get_status_display().lower()}."
                    + (
                        " Their role and free ticket have been issued."
                        if application.is_accepted
                        else ""
                    ),
                )
                return redirect("volunteers:review_detail", application_id=application.pk)
    else:
        form = VolunteerDecisionForm(application=application)

    return render(
        request,
        "volunteers/review_detail.html",
        {
            "application": application,
            "form": form,
            "availability": application.availability_summary(),
            "roster": services.roster_for(application),
            "open_shifts": Shift.objects.filter(conference_year=year)
            .select_related("team")
            .order_by("starts_at"),
            "ticket": services.volunteer_ticket(application),
            "conference_year": year,
        },
    )


def _assign_shift(request, application):
    shift = get_object_or_404(Shift, pk=request.POST.get("shift"), conference_year=application.conference_year)
    try:
        _, created = services.assign_shift(
            application,
            shift,
            by=request.user,
            allow_overlap=bool(request.POST.get("allow_overlap")),
        )
    except services.VolunteerError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            f"{application.full_name} is on {shift.title}."
            if created
            else f"{application.full_name} was already on {shift.title}.",
        )
    return redirect("volunteers:review_detail", application_id=application.pk)


def _unassign_shift(request, application):
    assignment = get_object_or_404(
        application.shift_assignments, pk=request.POST.get("assignment")
    )
    title = assignment.shift.title
    services.unassign_shift(assignment, by=request.user)
    messages.success(request, f"{application.full_name} is off {title}.")
    return redirect("volunteers:review_detail", application_id=application.pk)


def _set_attendance(request, application):
    assignment = get_object_or_404(
        application.shift_assignments, pk=request.POST.get("assignment")
    )
    attended = request.POST.get("attended") == "1"
    services.mark_attended(assignment, by=request.user, attended=attended)
    messages.success(
        request,
        f"Recorded that {application.full_name} "
        f"{'worked' if attended else 'did not work'} {assignment.shift.title}.",
    )
    return redirect("volunteers:review_detail", application_id=application.pk)


@coordinator_required
@require_http_methods(["GET", "POST"])
def shifts(request):
    """Build the roster: the shifts themselves, with their staffing at a glance."""
    year = current_year()
    form = ShiftForm(conference_year=year)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "publish":
            told = services.publish_shifts(year, by=request.user)
            messages.success(
                request,
                f"The roster is live and {told} volunteer"
                f"{'' if told == 1 else 's'} {'has' if told == 1 else 'have'} been told."
                if told
                else "The roster is live. Nobody has a shift yet, so nobody was emailed.",
            )
            return redirect("volunteers:shifts")
        if action == "unpublish":
            services.unpublish_shifts(year, by=request.user)
            messages.info(
                request, "The roster is hidden again. Nobody was emailed about that."
            )
            return redirect("volunteers:shifts")
        if action == "delete":
            shift = get_object_or_404(Shift, pk=request.POST.get("shift"), conference_year=year)
            title = shift.title
            shift.delete()
            messages.success(request, f"Removed {title} and any assignments on it.")
            return redirect("volunteers:shifts")

        form = ShiftForm(request.POST, conference_year=year)
        if form.is_valid():
            shift = form.save()
            messages.success(request, f"Added {shift.title}.")
            return redirect("volunteers:shifts")

    return render(
        request,
        "volunteers/shifts.html",
        {
            "form": form,
            "shifts": Shift.objects.filter(conference_year=year)
            .select_related("team")
            .prefetch_related("assignments__application")
            .order_by("starts_at"),
            "gaps": services.staffing_gaps(year),
            "settings_obj": services.settings_for(year),
            "conference_year": year,
        },
    )


@coordinator_required
def shift_detail(request, shift_id):
    """Who is on one shift, and who else is free at that time."""
    year = current_year()
    shift = get_object_or_404(
        Shift.objects.select_related("team"), pk=shift_id, conference_year=year
    )
    accepted = VolunteerApplication.objects.filter(
        conference_year=year, status=VolunteerApplication.STATUS_ACCEPTED
    ).prefetch_related("shift_assignments__shift", "availability")

    candidates = []
    for application in accepted:
        if application.shift_assignments.filter(shift=shift).exists():
            continue
        clash = services.overlapping_assignment(application, shift)
        available = application.availability.filter(day=shift.starts_at.date()).exists()
        candidates.append(
            {"application": application, "clash": clash, "said_available": available}
        )
    # Volunteers who said they were free that day first, then everyone else.
    candidates.sort(key=lambda c: (not c["said_available"], bool(c["clash"])))

    return render(
        request,
        "volunteers/shift_detail.html",
        {
            "shift": shift,
            "assignments": shift.assignments.select_related("application"),
            "candidates": candidates,
            "conference_year": year,
        },
    )


@coordinator_required
def export_csv(request):
    """
    Every volunteer as a CSV, for the shirt order and the badge printer.

    Includes the fields the day actually needs — team, lead, shirt size, dietary
    needs, phone, emergency contact — and deliberately not the motivation text,
    which is for deciding and not for a spreadsheet passed around a venue.
    """
    year = current_year()
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="pyconng-{year}-volunteers.csv"'
    )
    writer = csv.writer(response)
    writer.writerow([
        "Name", "Email", "Status", "Team", "Lead", "Phone",
        "T-shirt", "Dietary", "Accessibility",
        "Emergency contact", "Emergency phone", "Shifts", "Shifts worked",
    ])
    for application in (
        VolunteerApplication.objects.filter(conference_year=year)
        .select_related("assigned_team", "assigned_lead", "user")
        .prefetch_related("shift_assignments")
        .order_by("assigned_team__display_order", "full_name")
    ):
        lead = application.lead_contact
        writer.writerow([
            application.full_name,
            application.user.email,
            application.get_status_display(),
            application.assigned_team.name if application.assigned_team else "",
            (lead.get_full_name() or lead.email) if lead else "",
            application.phone,
            application.get_tshirt_size_display() if application.tshirt_size else "",
            application.dietary_requirements,
            application.accessibility_needs,
            application.emergency_contact_name,
            application.emergency_contact_phone,
            application.shift_count,
            application.shift_assignments.filter(attended_at__isnull=False).count(),
        ])
    return response
