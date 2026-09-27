"""
The public reporting form.

No login required, by design: requiring an account to report a breach of the Code
of Conduct excludes exactly the people most likely to need it -- a first-time
attendee, somebody's guest, a volunteer who has not signed up yet.
"""

import logging

from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from editions.current import current_year

from .forms import IncidentReportForm
from .models import CodeOfConductPage
from .services import submit_report

logger = logging.getLogger(__name__)

#: Where the reference is kept between the POST and the confirmation page. In the
#: session rather than the URL: a reference in a query string ends up in browser
#: history, in a proxy log, and in whatever the next person to use that laptop sees.
SESSION_KEY = "conduct_report_reference"


def _code_of_conduct_page():
    """The live Code of Conduct page, if there is one."""
    return CodeOfConductPage.objects.live().first()


@require_http_methods(["GET", "POST"])
def report(request):
    """Show and accept the reporting form."""
    user = request.user if request.user.is_authenticated else None
    page = _code_of_conduct_page()

    if request.method == "POST":
        form = IncidentReportForm(request.POST, user=user)
        if form.is_valid():
            saved = submit_report(
                data=form.report_data(), user=user, year=current_year()
            )
            request.session[SESSION_KEY] = saved.reference
            return redirect("conduct:report_submitted")
    else:
        form = IncidentReportForm(user=user)

    return render(
        request,
        "conduct/report.html",
        {
            "form": form,
            "coc_page": page,
            "response_promise": getattr(page, "response_promise", ""),
            "conference_year": current_year(),
        },
    )


def report_submitted(request):
    """
    Confirm, and show the reference once.

    Popped from the session rather than left there, so a shared machine does not
    keep showing it. Someone who reloads or arrives here directly is told plainly
    that the reference is gone rather than shown a blank space.
    """
    reference = request.session.pop(SESSION_KEY, None)
    return render(
        request,
        "conduct/report_submitted.html",
        {
            "reference": reference,
            "coc_page": _code_of_conduct_page(),
            "conference_year": current_year(),
        },
    )
