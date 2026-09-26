"""
Permission decorators for the Travel Grant system.

    @grant_open_required     - Applications must be open
    @login_required          - User must be logged in (Django auth)
    @grant_reviewer_required - Grant reviewer (or chair)
    @grant_chair_required    - Grant chair only
    @grant_finance_required  - Finance team (or chair)

The role guards are thin wrappers over ``accounts.decorators.role_required`` so
the grant module and the CFP module answer the role question the same way. Grant
chairs imply reviewer and finance, which is declared once in
``accounts.roles.IMPLIES`` rather than re-checked here.
"""

from functools import wraps

from django.shortcuts import redirect

from accounts.decorators import role_required
from accounts.roles import Role

from .services import GrantService


def grant_open_required(view_func):
    """Redirect to grants:closed if applications are not open."""

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not GrantService.is_grant_open():
            return redirect("grants:closed")
        return view_func(request, *args, **kwargs)

    return _wrapped


def login_required(view_func):
    """Require Django authentication."""

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("login")
        return view_func(request, *args, **kwargs)

    return _wrapped


#: Reviewer access. A grant chair holds this by implication.
grant_reviewer_required = role_required(Role.GRANT_REVIEWER)

#: Chair-only access: assigning reviewers and making decisions.
grant_chair_required = role_required(Role.GRANT_CHAIR)

#: Finance access. A grant chair holds this by implication.
grant_finance_required = role_required(Role.FINANCE)
