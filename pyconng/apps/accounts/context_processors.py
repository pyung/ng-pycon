"""Make the current user's roles available to every template as ``roles``."""

from .roles import roles_for


def user_roles(request):
    user = getattr(request, "user", None)
    return {"roles": roles_for(user)}
