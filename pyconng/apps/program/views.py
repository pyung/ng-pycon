"""Personal schedule actions."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from .models import SavedTalk, Talk


def _wants_json(request):
    return (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in request.headers.get("Accept", "")
    )


@require_POST
@login_required
def toggle_saved_talk(request, talk_id):
    """
    Add or remove a talk from the signed-in visitor's schedule.

    POST only, so a crawler cannot change someone's schedule by following a
    link. Answers JSON to a fetch and redirects a plain form post back to where
    it came from, so it works without JavaScript.
    """
    talk = get_object_or_404(Talk.objects.published(), pk=talk_id)

    existing = SavedTalk.objects.filter(user=request.user, talk=talk).first()
    if existing:
        existing.delete()
        saved = False
    else:
        SavedTalk.objects.create(user=request.user, talk=talk)
        saved = True

    if _wants_json(request):
        return JsonResponse({
            "saved": saved,
            "talk_id": talk.pk,
            "count": SavedTalk.objects.filter(
                user=request.user, talk__conference_year=talk.conference_year,
            ).count(),
        })

    messages.success(
        request,
        f"Added “{talk.title}” to your schedule." if saved
        else f"Removed “{talk.title}” from your schedule.",
    )
    return redirect(request.POST.get("next") or request.headers.get("Referer") or "/")
