"""
Permission decorators for the CFP system.

    @cfp_open_required    – CFP must be Open
    @speaker_required     – valid speaker token in session / query-param
    @proposal_owner_required – speaker must own the proposal

Reviewer and chair access is not handled here. Use
``accounts.decorators.role_required(Role.CFP_REVIEWER)`` so every module answers
the role question the same way.
"""

from functools import wraps

from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect

from .models import Proposal
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
# Speaker (token-based) guards
# ------------------------------------------------------------------

def speaker_required(view_func):
    """
    Ensure a valid speaker is associated with this session.

    Checks (in order):
      1. ``?token=<uuid>`` query parameter – validates and stores in session.
      2. ``cfp_speaker_id`` already in session.

    Falls back to the access-link request page.
    """

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        # 1. Try token from URL
        token = request.GET.get("token")
        if token:
            speaker = CFPService.authenticate_speaker(request, token)
            if speaker:
                request.cfp_speaker = speaker
                return view_func(request, *args, **kwargs)

        # 2. Try session
        speaker = CFPService.get_speaker_from_session(request)
        if speaker:
            request.cfp_speaker = speaker
            return view_func(request, *args, **kwargs)

        # 3. Not authenticated – redirect to access page
        return redirect("cfp:access")

    return _wrapped


def proposal_owner_required(view_func):
    """
    Must be called **after** ``@speaker_required``.
    Ensures the speaker owns the proposal identified by ``proposal_id``.
    """

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        proposal_id = kwargs.get("proposal_id")
        proposal = get_object_or_404(Proposal, pk=proposal_id)

        if proposal.speaker_id != request.cfp_speaker.pk:
            return HttpResponseForbidden("You do not have access to this proposal.")

        request.cfp_proposal = proposal
        return view_func(request, *args, **kwargs)

    return _wrapped
