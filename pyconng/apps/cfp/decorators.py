"""
Permission decorators for the CFP system.

    @cfp_open_required       – CFP must be Open
    @speaker_required        – signed in; attaches this edition's speaker profile
    @proposal_owner_required – the signed-in user must own the proposal

Reviewer and chair access is not handled here. Use
``accounts.decorators.role_required(Role.CFP_REVIEWER)`` so every module answers
the role question the same way.
"""

from functools import wraps

from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect

from .models import Proposal, Speaker
from .services import CFPService


# ------------------------------------------------------------------
# Public guard
# ------------------------------------------------------------------

def cfp_open_required(view_func):
    """Redirect to /cfp/closed/ if the CFP is not currently open."""

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not CFPService.is_cfp_open():
            return redirect("cfp:closed")
        return view_func(request, *args, **kwargs)

    return _wrapped


# ------------------------------------------------------------------
# Speaker guards
# ------------------------------------------------------------------

def speaker_required(view_func):
    """
    Require a signed-in user and attach ``request.cfp_speaker`` -- their speaker
    profile for the current edition, or ``None`` if they have not created one.

    A missing profile is not an error: someone can be signed in and simply not
    have submitted anything yet.
    """

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/accounts/login/?next={request.path}")

        request.cfp_speaker = CFPService.get_speaker_for_user(request.user)
        return view_func(request, *args, **kwargs)

    return _wrapped


def proposal_owner_required(view_func):
    """
    Must be called **after** ``@speaker_required``.

    Ownership is checked against the account, not the speaker profile, so it
    holds even before a profile exists for the current edition.
    """

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        proposal_id = kwargs.get("proposal_id")
        proposal = get_object_or_404(
            Proposal.objects.select_related("speaker__user"), pk=proposal_id,
        )

        if proposal.speaker.user_id != request.user.pk:
            return HttpResponseForbidden("You do not have access to this proposal.")

        request.cfp_proposal = proposal
        return view_func(request, *args, **kwargs)

    return _wrapped
