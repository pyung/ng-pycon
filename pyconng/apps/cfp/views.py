"""
CFP views – organised by audience:

    Public       – landing, submit, closed
    Speaker      – access, my_proposals, proposal CRUD
    Reviewer     – review_list, review_detail, review_score, review_conflict
    Chair / Admin – dashboard, assign, decisions, export, email
"""

import logging

from django.contrib import messages
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from pyconng.context_processors import CURRENT_YEAR

from accounts.decorators import role_required
from accounts.roles import Role

from .decorators import (
    cfp_open_required,
    proposal_owner_required,
    speaker_required,
)
from .forms import (
    AssignReviewerForm,
    BulkDecisionForm,
    BulkEmailForm,
    ProposalForm,
    ReviewForm,
    SpeakerAccessForm,
    SpeakerForm,
)
from .models import (
    CFPSettings,
    Proposal,
    Review,
    ReviewerAssignment,
    Speaker,
    Track,
)
from .services import CFPService

logger = logging.getLogger(__name__)


# ======================================================================
# PUBLIC VIEWS
# ======================================================================

def cfp_landing(request):
    """CFP landing page – guidelines, dates, tracks."""
    cfp = CFPService.get_current_cfp()
    tracks = Track.objects.filter(
        conference_year=CURRENT_YEAR, is_active=True,
    ).order_by("display_order", "name")

    return render(request, "cfp/landing.html", {
        "cfp": cfp,
        "tracks": tracks,
        "conference_year": CURRENT_YEAR,
    })


@cfp_open_required
def cfp_submit(request):
    """Proposal submission form (GET = blank form, POST = create)."""
    cfp = CFPService.get_current_cfp()

    # Pre-fill speaker form if returning speaker is in session
    speaker = CFPService.get_speaker_from_session(request)
    speaker_initial = {}
    if speaker:
        speaker_initial = {
            "full_name": speaker.full_name,
            "email": speaker.email,
            "bio": speaker.bio,
            "organisation": speaker.organisation,
            "country": speaker.country,
            "first_time_speaker": speaker.first_time_speaker,
        }

    if request.method == "POST":
        speaker_form = SpeakerForm(request.POST)
        proposal_form = ProposalForm(request.POST, cfp_settings=cfp)

        if speaker_form.is_valid() and proposal_form.is_valid():
            # Get or create speaker
            sp = CFPService.get_or_create_speaker(
                email=speaker_form.cleaned_data["email"],
                full_name=speaker_form.cleaned_data["full_name"],
                bio=speaker_form.cleaned_data["bio"],
                organisation=speaker_form.cleaned_data.get("organisation", ""),
                country=speaker_form.cleaned_data["country"],
                first_time_speaker=speaker_form.cleaned_data.get(
                    "first_time_speaker", False,
                ),
            )

            # Store speaker in session
            request.session["cfp_speaker_id"] = sp.pk

            # Build proposal
            proposal = proposal_form.save(commit=False)
            proposal.speaker = sp
            proposal.conference_year = CURRENT_YEAR

            # Determine action: Save Draft vs Submit
            action = request.POST.get("action", "draft")
            if action == "submit":
                proposal.status = Proposal.STATUS_SUBMITTED
                proposal.submitted_at = timezone.now()

            proposal.save()

            if action == "submit":
                CFPService.create_snapshot(proposal, "submission")
                CFPService.log_action(
                    proposal,
                    "Submitted proposal",
                    old_status=Proposal.STATUS_DRAFT,
                    new_status=Proposal.STATUS_SUBMITTED,
                    actor=sp.email,
                )
                CFPService.send_submission_confirmation(proposal, request)
                CFPService.send_access_link(sp, request)
                messages.success(
                    request,
                    "Your proposal has been submitted! "
                    "Check your email for an access link to manage it.",
                )
            else:
                CFPService.log_action(
                    proposal,
                    "Saved draft",
                    new_status=Proposal.STATUS_DRAFT,
                    actor=sp.email,
                )
                CFPService.send_access_link(sp, request)
                messages.success(
                    request,
                    "Draft saved. Check your email for an access link. "
                    "Remember to submit before the deadline!",
                )

            return redirect("cfp:proposal_detail", proposal_id=proposal.pk)
    else:
        speaker_form = SpeakerForm(initial=speaker_initial)
        proposal_form = ProposalForm(cfp_settings=cfp)

    return render(request, "cfp/submit.html", {
        "speaker_form": speaker_form,
        "proposal_form": proposal_form,
        "cfp": cfp,
        "conference_year": CURRENT_YEAR,
    })


def cfp_closed(request):
    """Simple notice that the CFP is closed."""
    cfp = CFPService.get_current_cfp()
    return render(request, "cfp/closed.html", {
        "cfp": cfp,
        "conference_year": CURRENT_YEAR,
    })


# ======================================================================
# SPEAKER (TOKEN-BASED AUTH) VIEWS
# ======================================================================

def cfp_access(request):
    """
    Speaker enters their email to receive an access link.
    """
    if request.method == "POST":
        form = SpeakerAccessForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"]
            try:
                speaker = Speaker.objects.get(
                    email=email, conference_year=CURRENT_YEAR,
                )
                CFPService.send_access_link(speaker, request)
                messages.success(
                    request,
                    "Access link sent! Check your email.",
                )
            except Speaker.DoesNotExist:
                # Don't reveal whether the email exists
                messages.success(
                    request,
                    "If you've submitted a proposal with this email, "
                    "you'll receive an access link shortly.",
                )
            return redirect("cfp:access")
    else:
        form = SpeakerAccessForm()

    return render(request, "cfp/access.html", {
        "form": form,
        "conference_year": CURRENT_YEAR,
    })


@speaker_required
def cfp_my_proposals(request):
    """List the current speaker's proposals."""
    speaker = request.cfp_speaker
    proposals = Proposal.objects.filter(
        speaker=speaker, conference_year=CURRENT_YEAR,
    ).order_by("-created_at")

    return render(request, "cfp/my_proposals.html", {
        "speaker": speaker,
        "proposals": proposals,
        "conference_year": CURRENT_YEAR,
        "cfp_is_open": CFPService.is_cfp_open(),
    })


@speaker_required
@proposal_owner_required
def cfp_proposal_detail(request, proposal_id):
    """View a single proposal."""
    proposal = request.cfp_proposal
    audit_logs = proposal.audit_logs.all()[:20]

    return render(request, "cfp/proposal_detail.html", {
        "proposal": proposal,
        "audit_logs": audit_logs,
        "conference_year": CURRENT_YEAR,
        "cfp_is_open": CFPService.is_cfp_open(),
    })


@cfp_open_required
@speaker_required
@proposal_owner_required
def cfp_proposal_edit(request, proposal_id):
    """Edit a proposal (only while CFP is open and proposal is editable)."""
    proposal = request.cfp_proposal
    cfp = CFPService.get_current_cfp()

    if not proposal.is_editable:
        messages.error(request, "This proposal can no longer be edited.")
        return redirect("cfp:proposal_detail", proposal_id=proposal.pk)

    if request.method == "POST":
        speaker_form = SpeakerForm(request.POST)
        proposal_form = ProposalForm(
            request.POST, instance=proposal, cfp_settings=cfp,
        )

        if speaker_form.is_valid() and proposal_form.is_valid():
            # Update speaker info
            speaker = request.cfp_speaker
            speaker.full_name = speaker_form.cleaned_data["full_name"]
            speaker.bio = speaker_form.cleaned_data["bio"]
            speaker.organisation = speaker_form.cleaned_data.get("organisation", "")
            speaker.country = speaker_form.cleaned_data["country"]
            speaker.first_time_speaker = speaker_form.cleaned_data.get(
                "first_time_speaker", False,
            )
            speaker.save()

            proposal = proposal_form.save()

            # Snapshot the edit
            CFPService.create_snapshot(proposal, "edit")
            CFPService.log_action(
                proposal,
                "Edited proposal",
                actor=speaker.email,
            )

            # Re-submit if it was already submitted
            action = request.POST.get("action", "draft")
            if action == "submit" and proposal.status == Proposal.STATUS_DRAFT:
                CFPService.submit_proposal(proposal, actor_email=speaker.email)
                CFPService.send_submission_confirmation(proposal, request)
                messages.success(request, "Proposal submitted!")
            else:
                messages.success(request, "Proposal updated.")

            return redirect("cfp:proposal_detail", proposal_id=proposal.pk)
    else:
        speaker = request.cfp_speaker
        speaker_form = SpeakerForm(initial={
            "full_name": speaker.full_name,
            "email": speaker.email,
            "bio": speaker.bio,
            "organisation": speaker.organisation,
            "country": speaker.country,
            "first_time_speaker": speaker.first_time_speaker,
        })
        proposal_form = ProposalForm(instance=proposal, cfp_settings=cfp)

    return render(request, "cfp/proposal_edit.html", {
        "speaker_form": speaker_form,
        "proposal_form": proposal_form,
        "proposal": proposal,
        "cfp": cfp,
        "conference_year": CURRENT_YEAR,
    })


@require_POST
@speaker_required
@proposal_owner_required
def cfp_proposal_withdraw(request, proposal_id):
    """Withdraw a proposal."""
    proposal = request.cfp_proposal

    if not proposal.can_withdraw:
        messages.error(request, "This proposal cannot be withdrawn.")
        return redirect("cfp:proposal_detail", proposal_id=proposal.pk)

    if not CFPService.is_cfp_open():
        messages.error(request, "Proposals cannot be withdrawn after the CFP closes.")
        return redirect("cfp:proposal_detail", proposal_id=proposal.pk)

    CFPService.withdraw_proposal(proposal, actor_email=request.cfp_speaker.email)
    messages.success(request, "Your proposal has been withdrawn.")
    return redirect("cfp:my_proposals")


@speaker_required
@proposal_owner_required
def cfp_proposal_status(request, proposal_id):
    """View the decision status of a proposal."""
    proposal = request.cfp_proposal
    return render(request, "cfp/proposal_status.html", {
        "proposal": proposal,
        "conference_year": CURRENT_YEAR,
    })


@require_POST
@speaker_required
@proposal_owner_required
def cfp_proposal_confirm(request, proposal_id):
    """Speaker confirms their accepted talk."""
    proposal = request.cfp_proposal

    if proposal.status != Proposal.STATUS_ACCEPTED:
        messages.error(request, "Only accepted proposals can be confirmed.")
        return redirect("cfp:proposal_detail", proposal_id=proposal.pk)

    CFPService.confirm_proposal(proposal, actor_email=request.cfp_speaker.email)
    messages.success(
        request,
        "Thank you for confirming! We look forward to your talk.",
    )
    return redirect("cfp:proposal_detail", proposal_id=proposal.pk)


# ======================================================================
# REVIEWER VIEWS
# ======================================================================

@role_required(Role.CFP_REVIEWER)
def review_list(request):
    """List proposals assigned to this reviewer."""
    assignments = (
        ReviewerAssignment.objects.filter(reviewer=request.user)
        .select_related("proposal__speaker", "proposal__track")
        .order_by("-assigned_at")
    )

    return render(request, "cfp/review_list.html", {
        "assignments": assignments,
        "conference_year": CURRENT_YEAR,
    })


@role_required(Role.CFP_REVIEWER)
def review_detail(request, proposal_id):
    """View proposal detail for review (with optional score form)."""
    assignment = get_object_or_404(
        ReviewerAssignment,
        reviewer=request.user,
        proposal_id=proposal_id,
    )
    proposal = assignment.proposal

    existing_review = getattr(assignment, "review", None)

    if existing_review:
        form = ReviewForm(initial={
            "score": existing_review.score,
            "comments": existing_review.comments,
        })
    else:
        form = ReviewForm()

    return render(request, "cfp/review_detail.html", {
        "assignment": assignment,
        "proposal": proposal,
        "form": form,
        "existing_review": existing_review,
        "conference_year": CURRENT_YEAR,
    })


@require_POST
@role_required(Role.CFP_REVIEWER)
def review_score(request, proposal_id):
    """Submit or update a review score."""
    assignment = get_object_or_404(
        ReviewerAssignment,
        reviewer=request.user,
        proposal_id=proposal_id,
    )

    if assignment.has_conflict:
        messages.error(request, "You have declared a conflict for this proposal.")
        return redirect("cfp:review_detail", proposal_id=proposal_id)

    form = ReviewForm(request.POST)
    if form.is_valid():
        review, created = Review.objects.update_or_create(
            assignment=assignment,
            defaults={
                "score": form.cleaned_data["score"],
                "comments": form.cleaned_data["comments"],
            },
        )
        messages.success(
            request,
            "Review submitted." if created else "Review updated.",
        )
    else:
        messages.error(request, "Please correct the errors below.")
        return render(request, "cfp/review_detail.html", {
            "assignment": assignment,
            "proposal": assignment.proposal,
            "form": form,
            "existing_review": getattr(assignment, "review", None),
            "conference_year": CURRENT_YEAR,
        })

    return redirect("cfp:review_detail", proposal_id=proposal_id)


@require_POST
@role_required(Role.CFP_REVIEWER)
def review_conflict(request, proposal_id):
    """Declare a conflict of interest."""
    assignment = get_object_or_404(
        ReviewerAssignment,
        reviewer=request.user,
        proposal_id=proposal_id,
    )
    assignment.has_conflict = True
    assignment.save()
    messages.info(request, "Conflict of interest recorded.")
    return redirect("cfp:review_list")


# ======================================================================
# CHAIR / ADMIN VIEWS
# ======================================================================

@role_required(Role.CFP_CHAIR)
def admin_dashboard(request):
    """CFP dashboard with stats and proposal list."""
    stats = CFPService.get_dashboard_stats()
    cfp = CFPService.get_current_cfp()

    proposals = (
        Proposal.objects.filter(conference_year=CURRENT_YEAR)
        .exclude(status=Proposal.STATUS_DRAFT)
        .select_related("speaker", "track")
        .order_by("-submitted_at")
    )

    # Simple filtering
    status_filter = request.GET.get("status")
    track_filter = request.GET.get("track")
    if status_filter:
        proposals = proposals.filter(status=status_filter)
    if track_filter:
        proposals = proposals.filter(track_id=track_filter)

    tracks = Track.objects.filter(
        conference_year=CURRENT_YEAR, is_active=True,
    )

    return render(request, "cfp/admin_dashboard.html", {
        "stats": stats,
        "cfp": cfp,
        "proposals": proposals,
        "tracks": tracks,
        "status_filter": status_filter,
        "track_filter": track_filter,
        "conference_year": CURRENT_YEAR,
        "status_choices": Proposal.STATUS_CHOICES,
    })


@require_POST
@role_required(Role.CFP_CHAIR)
def admin_assign(request):
    """Assign reviewers to proposals."""
    form = AssignReviewerForm(request.POST)
    if form.is_valid():
        proposal_ids = form.cleaned_data["proposal_ids"]
        reviewer_ids = form.cleaned_data["reviewer_ids"]

        created_count = 0
        for pid in proposal_ids:
            for rid in reviewer_ids:
                _, created = ReviewerAssignment.objects.get_or_create(
                    proposal_id=pid,
                    reviewer_id=rid,
                )
                if created:
                    created_count += 1

        # Move proposals to Under Review
        Proposal.objects.filter(
            id__in=proposal_ids,
            status=Proposal.STATUS_SUBMITTED,
        ).update(status=Proposal.STATUS_UNDER_REVIEW)

        messages.success(
            request,
            f"Created {created_count} reviewer assignment(s).",
        )
    else:
        messages.error(request, "Invalid assignment data.")

    return redirect("cfp:admin_dashboard")


@require_POST
@role_required(Role.CFP_CHAIR)
def admin_decisions(request):
    """Bulk accept / reject / waitlist."""
    form = BulkDecisionForm(request.POST)
    if form.is_valid():
        proposal_ids = form.cleaned_data["proposal_ids"]
        decision = form.cleaned_data["decision"]

        count = CFPService.bulk_decision(
            proposal_ids,
            decision,
            actor_email=request.user.email,
        )
        messages.success(
            request,
            f"{count} proposal(s) marked as {decision}ed.",
        )
    else:
        messages.error(request, "Invalid decision data.")

    return redirect("cfp:admin_dashboard")


@role_required(Role.CFP_CHAIR)
def admin_export(request):
    """Export accepted talks as CSV or JSON."""
    fmt = request.GET.get("format", "csv")
    content = CFPService.export_accepted_talks(fmt=fmt)

    if fmt == "json":
        response = HttpResponse(content, content_type="application/json")
        response["Content-Disposition"] = (
            f'attachment; filename="pycon_ng_{CURRENT_YEAR}_talks.json"'
        )
    else:
        response = HttpResponse(content, content_type="text/csv")
        response["Content-Disposition"] = (
            f'attachment; filename="pycon_ng_{CURRENT_YEAR}_talks.csv"'
        )
    return response


@require_POST
@role_required(Role.CFP_CHAIR)
def admin_email(request):
    """Send bulk decision emails."""
    form = BulkEmailForm(request.POST)
    if form.is_valid():
        proposal_ids = form.cleaned_data["proposal_ids"]
        template_type = form.cleaned_data["template_type"]

        sent = CFPService.send_decision_emails(proposal_ids, template_type)
        messages.success(request, f"Sent {sent} email(s).")
    else:
        messages.error(request, "Invalid email data.")

    return redirect("cfp:admin_dashboard")
