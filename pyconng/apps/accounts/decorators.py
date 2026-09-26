"""
View guards built on :func:`accounts.roles.roles_for`.

    @role_required(Role.CFP_REVIEWER)              # holds the role
    @role_required(Role.FINANCE, Role.GRANT_CHAIR) # holds either

Every guarded view gets ``request.roles`` (a :class:`~accounts.roles.RoleSet`)
and ``request.role_year``, so it never has to resolve roles a second time.

Superusers are not waved through. They hold Super-admin and Organizer, and
nothing else unless it was granted -- a review queue should contain the people
who were actually asked to review.
"""

from functools import wraps

from django.http import HttpResponseForbidden
from django.shortcuts import redirect

from editions.current import current_year

from .roles import Role, roles_for


def attach_roles(view_func):
    """Resolve roles onto the request without requiring any of them."""

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not hasattr(request, "roles"):
            request.roles = roles_for(request.user)
            request.role_year = current_year()
        return view_func(request, *args, **kwargs)

    return _wrapped


def role_required(*roles, year=None):
    """
    Require any one of ``roles``. Anonymous users are sent to log in; a signed-in
    user without the role gets 403.
    """
    if not roles:
        raise ValueError("role_required needs at least one role")

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect("login")

            resolved_year = year if year is not None else current_year()
            held = roles_for(request.user, resolved_year)

            if not held.has(*roles):
                wanted = ", ".join(Role(r).label for r in roles)
                return HttpResponseForbidden(
                    f"This page needs the {wanted} role. "
                    "Ask an organizer if you should have it."
                )

            request.roles = held
            request.role_year = resolved_year
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator
