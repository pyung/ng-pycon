"""
Visa letter views.

Applicant: landing, request, my request, withdraw, download the letter
Officer:   queue, one request, decide, reveal a passport number, purge
Public:    verification

The verification endpoint is the only unauthenticated page, and it is rate-limited
by requiring two pieces of information rather than by counting requests: a consular
officer holding the letter has the reference and the surname, and somebody guessing
has neither.
"""

import logging

from django.contrib import messages
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST, require_http_methods

from accounts.roles import Role, roles_for
from editions.current import current_year

from . import eligibility, letter, services
from .decorators import login_required, visa_officer_required
from .forms import VerificationForm, VisaDecisionForm, VisaLetterRequestForm
from .models import VisaLetterRequest, VisaSettings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Applicant
# ---------------------------------------------------------------------------

def visa_landing(request):
    """
    What the letter is, who can have one, and when to ask.

    The deadline is the headline rather than a detail at the bottom. Everything
    else on this site can be left late; this one cannot, and the page should say so
    before it says anything else.
    """
    year = current_year()
    settings_obj = VisaSettings.for_year(year)
    assessment = eligibility.assess(request.user, year)
    return render(request, "visas/landing.html", {
        "conference_year": year,
        "settings": settings_obj,
        "assessment": assessment,
        "existing": services.request_for(request.user, year),
        "recommended_deadline": settings_obj.recommended_deadline,
    })


@login_required
@require_http_methods(["GET", "POST"])
def visa_request(request):
    """Make or correct a request. Editable until it is decided."""
    year = current_year()
    settings_obj = VisaSettings.for_year(year)
    assessment = eligibility.assess(request.user, year)
    existing = services.request_for(request.user, year)

    if not assessment.ok:
        messages.error(request, assessment.reason)
        return redirect("visas:landing")

    if existing and not existing.can_edit:
        messages.info(
            request, "Your request has been decided, so it can no longer be edited."
        )
        return redirect("visas:my_request")

    if not settings_obj.is_open and not existing:
        messages.error(
            request,
            "Requests for invitation letters are not open at the moment."
            + (
                f" They open on {letter._date(settings_obj.opens_on)}."
                if settings_obj.opens_on and not settings_obj.has_opened
                else ""
            ),
        )
        return redirect("visas:landing")

    if request.method == "POST":
        form = VisaLetterRequestForm(request.POST, instance=existing)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.user = request.user
            obj.conference_year = year
            obj.save()
            try:
                services.submit(obj, by=request.user)
            except services.VisaError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(
                    request,
                    "Your request is with the visa team. We aim to decide within "
                    f"{settings_obj.processing_days} day"
                    f"{'' if settings_obj.processing_days == 1 else 's'}.",
                )
                return redirect("visas:my_request")
    else:
        form = VisaLetterRequestForm(instance=existing)

    return render(request, "visas/request_form.html", {
        "conference_year": year,
        "settings": settings_obj,
        "assessment": assessment,
        "form": form,
        "existing": existing,
    })


@login_required
def visa_my_request(request):
    """Where an applicant sees their status and downloads the letter."""
    year = current_year()
    obj = services.request_for(request.user, year)
    settings_obj = VisaSettings.for_year(year)
    return render(request, "visas/my_request.html", {
        "conference_year": year,
        "settings": settings_obj,
        "request_obj": obj,
        "assessment": eligibility.assess(request.user, year),
    })


@login_required
@require_POST
def visa_withdraw(request, pk):
    obj = get_object_or_404(VisaLetterRequest, pk=pk, user=request.user)
    try:
        services.withdraw(obj, by=request.user)
    except services.VisaError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Your request has been withdrawn.")
    return redirect("visas:my_request")


@login_required
def visa_letter_download(request, pk):
    """
    The letter as a PDF.

    Served by the applicant and by the officer, and by nobody else -- an invitation
    letter naming somebody with their passport number is not a document to leave on
    a guessable URL.
    """
    obj = get_object_or_404(VisaLetterRequest, pk=pk)
    is_owner = obj.user_id == request.user.id
    is_officer = roles_for(request.user, obj.conference_year).has(Role.VISA_OFFICER)
    if not (is_owner or is_officer):
        raise Http404

    if not obj.has_letter:
        messages.error(request, "There is no letter to download yet.")
        return redirect("visas:my_request" if is_owner else "visas:queue")

    try:
        data = services.letter_bytes(obj, by=request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("visas:my_request" if is_owner else "visas:queue")

    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{letter.filename(obj)}"'
    # Not cached: the letter carries passport details, and a shared machine's
    # browser cache is exactly where they should not sit.
    response["Cache-Control"] = "no-store, max-age=0"
    return response


# ---------------------------------------------------------------------------
# Visa officer
# ---------------------------------------------------------------------------

@visa_officer_required
def visa_queue(request):
    """
    The approval queue, oldest first.

    Ordered by how long each has waited rather than by when it arrived, which is
    the same ordering here and makes the point: the top of this list is the person
    closest to missing their appointment.
    """
    year = current_year()
    status = request.GET.get("status") or ""
    valid = dict(VisaLetterRequest.STATUS_CHOICES)
    if status not in valid:
        status = ""
    return render(request, "visas/queue.html", {
        "conference_year": year,
        "requests": services.queue(year, status or None),
        "overview": services.overview(year),
        "status_choices": [
            (value, label) for value, label in VisaLetterRequest.STATUS_CHOICES
            if value != VisaLetterRequest.STATUS_DRAFT
        ],
        "selected_status": status,
    })


@visa_officer_required
@require_http_methods(["GET", "POST"])
def visa_detail(request, pk):
    """One request, with its eligibility re-checked as the officer reads it."""
    year = current_year()
    obj = get_object_or_404(
        VisaLetterRequest.objects.select_related("user", "decided_by"), pk=pk
    )
    if request.method == "POST":
        form = VisaDecisionForm(request.POST)
        if form.is_valid():
            decision = form.cleaned_data["decision"]
            note = form.cleaned_data["note"]
            action = (
                services.approve if decision == form.DECISION_APPROVE else services.reject
            )
            try:
                action(obj, by=request.user, note=note)
            except services.VisaError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(
                    request,
                    f"Approved. Reference {obj.reference}."
                    if decision == form.DECISION_APPROVE
                    else "Rejected, and the applicant has been told why.",
                )
                return redirect("visas:queue")
    else:
        form = VisaDecisionForm()

    return render(request, "visas/detail.html", {
        "conference_year": year,
        "request_obj": obj,
        "form": form,
        # Re-read now, not taken from the record: a ticket refunded since
        # submission means the letter would claim something untrue.
        "current": eligibility.basis_for(obj),
        "settings": VisaSettings.for_year(obj.conference_year),
    })


@visa_officer_required
@require_POST
def visa_reveal(request, pk):
    """
    Return the full passport number, having written the read to the audit trail.

    A separate request rather than rendering it on the page, so that opening a
    record is not the same act as reading the number, and the trail can tell them
    apart.
    """
    obj = get_object_or_404(VisaLetterRequest, pk=pk)
    if not obj.details_retained:
        return JsonResponse(
            {"error": "These details have been deleted."}, status=410
        )
    number = services.reveal_passport_number(
        obj, by=request.user, reason="Opened from the request page"
    )
    return JsonResponse({"passport_number": number})


@visa_officer_required
@require_POST
def visa_purge(request):
    """
    Delete passport details for this edition now, ahead of the schedule.

    Manual as well as scheduled, because "we are done, take it off the system"
    should not require waiting for a cron job or asking a developer.
    """
    from .retention import purge_year

    year = current_year()
    purged = purge_year(year, by=request.user, force=True)
    if purged:
        messages.success(
            request,
            f"Passport details deleted for {purged} request(s). The letters, their "
            "references and the audit trail are untouched.",
        )
    else:
        messages.info(request, "No request for this edition is still holding details.")
    return redirect("visas:queue")


# ---------------------------------------------------------------------------
# Public verification
# ---------------------------------------------------------------------------

@require_http_methods(["GET", "POST"])
def visa_verify(request):
    """
    Where an embassy confirms a reference.

    Shows only what a consular officer needs to trust the document in their hand:
    that the reference is genuine, who it names, which conference, and whether it
    is still valid. Never the passport number -- they are holding the passport.

    A withdrawn or rejected letter reports as *not valid* rather than not found,
    because "this was issued and then cancelled" is materially different
    information to an embassy than "this never existed".
    """
    year = current_year()
    settings_obj = VisaSettings.for_year(year)
    result = None
    checked = False

    if request.method == "POST":
        form = VerificationForm(request.POST)
        if form.is_valid():
            checked = True
            obj = services.verify(form.cleaned_data["reference"])
            surname = form.cleaned_data["surname"].strip().lower()
            if obj and obj.passport_surname.strip().lower() == surname:
                result = obj
            elif obj and not obj.passport_surname:
                # Details purged after the event: the reference is still genuine,
                # and saying "not found" to an embassy checking an old letter
                # would be wrong.
                result = obj
    else:
        form = VerificationForm()

    return render(request, "visas/verify.html", {
        "conference_year": year,
        "settings": settings_obj,
        "form": form,
        "result": result,
        "checked": checked,
        "enabled": settings_obj.verification_enabled,
    })
