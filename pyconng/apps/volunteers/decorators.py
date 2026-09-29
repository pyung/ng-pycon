"""
Guards for the volunteer views.

    @volunteers_open_required     - applications must be open
    @coordinator_required         - the volunteer coordinator, or an organizer

Two roles rather than one on purpose. A volunteer coordinator is the person who
does this work; an Organizer reaches the same pages because on a small team
somebody senior always ends up covering it. Stated here rather than hidden in
``accounts.roles.IMPLIES``, because the two arrive for different reasons and a
reader of the roles module should not have to infer that.
"""

from functools import wraps

from django.shortcuts import redirect

from accounts.decorators import role_required
from accounts.roles import Role

from .services import is_open

#: Reviewing applications, assigning teams, and building the roster.
coordinator_required = role_required(Role.VOLUNTEER_CHAIR, Role.ORGANIZER)


def volunteers_open_required(view_func):
    """Send people to the closed page rather than a form that cannot be submitted."""

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not is_open():
            return redirect("volunteers:closed")
        return view_func(request, *args, **kwargs)

    return _wrapped
