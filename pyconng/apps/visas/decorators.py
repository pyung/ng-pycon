"""
View guards for the visa module.

    @visa_officer_required   - read passport data, decide requests

One role, granted to named people and implied by nothing -- see the note beside
``Role.VISA_OFFICER``. An Organizer is deliberately *not* accepted here, which is
different from how the volunteer views work: coordinating volunteers is
organisational work, and reading passport numbers is not.
"""

from functools import wraps

from django.shortcuts import redirect

from accounts.decorators import role_required
from accounts.roles import Role

#: Decide requests and read passport details.
visa_officer_required = role_required(Role.VISA_OFFICER)


def login_required(view_func):
    """Require authentication, sending anonymous visitors to sign in."""

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("login")
        return view_func(request, *args, **kwargs)

    return _wrapped
