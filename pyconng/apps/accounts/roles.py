"""
Role resolution for PyCon Nigeria.

One question, one answer: ``roles_for(user, year)`` returns every role a person
holds for a given edition. The dashboard, the view decorators and the admin all
go through it, so there is a single place to change what a role means.

Roles come from two places, and the distinction is the point:

**Granted** roles are conferred by an organizer and stored as
:class:`accounts.models.RoleAssignment` rows -- reviewer, chair, finance,
volunteer, sponsor contact, organizer.

**Derived** roles are computed from records that already exist: a paid ticket
makes someone an attendee, an accepted proposal makes them a speaker, a
submitted application makes them a grant applicant. These are deliberately
*not* stored. A stored copy would have to be kept in step with every Paystack
webhook and every decision email, and the day it falls out of step nothing
tells you.

One person can hold any number of roles, and roles are scoped per edition: a
speaker in 2026 can be a plain attendee in 2027. An assignment with
``conference_year=None`` applies to every edition, which is how a standing role
like Organizer is expressed.
"""

from django.db import models
from django.db.models import Q

from editions.current import current_year


class Role(models.TextChoices):
    # --- Derived from records; never stored as an assignment ---
    ATTENDEE = "attendee", "Attendee"
    SPEAKER = "speaker", "Speaker"
    GRANT_APPLICANT = "grant_applicant", "Grant applicant"
    SUPER_ADMIN = "super_admin", "Super-admin"

    # --- Granted by an organizer ---
    CFP_REVIEWER = "cfp_reviewer", "CFP reviewer"
    CFP_CHAIR = "cfp_chair", "CFP chair"
    GRANT_REVIEWER = "grant_reviewer", "Grant reviewer"
    GRANT_CHAIR = "grant_chair", "Grant chair"
    FINANCE = "finance", "Finance"
    VOLUNTEER = "volunteer", "Volunteer"
    SPONSOR_CONTACT = "sponsor_contact", "Sponsor contact"
    ORGANIZER = "organizer", "Organizer"
    COC_TEAM = "coc_team", "Code of Conduct team"
    VOLUNTEER_CHAIR = "volunteer_chair", "Volunteer coordinator"


#: Computed from other records, so they can never be granted by hand.
DERIVED_ROLES = frozenset({
    Role.ATTENDEE,
    Role.SPEAKER,
    Role.GRANT_APPLICANT,
    Role.SUPER_ADMIN,
})

#: Everything an organizer can confer. Used for the assignment model's choices.
GRANTABLE_ROLES = frozenset(Role) - DERIVED_ROLES


#: Holding the key role implies holding the values too. CFP and grant reviewing
#: stay separate on purpose -- a CFP chair gains no say over travel grants.
#:
#: COC_TEAM is implied by nothing, including SUPER_ADMIN. A Code of Conduct report
#: may be about an organizer, so membership of that team has to be granted to a
#: named person rather than falling out of being senior. The same reasoning keeps
#: superusers out of the review queues.
IMPLIES = {
    Role.CFP_CHAIR: frozenset({Role.CFP_REVIEWER}),
    Role.GRANT_CHAIR: frozenset({Role.GRANT_REVIEWER, Role.FINANCE}),
    Role.SUPER_ADMIN: frozenset({Role.ORGANIZER}),
}

#: VOLUNTEER_CHAIR implies nothing, and nothing implies it. It is not a senior
#: version of VOLUNTEER: a coordinator reviews applications and builds rosters,
#: and is frequently not working a shift themselves. Volunteer views accept the
#: coordinator or an Organizer, which is stated at each view rather than hidden
#: in an implication, because the two roles reach those pages for different
#: reasons.


def grantable_choices():
    """``choices`` for a role field an organizer fills in."""
    return [(r.value, r.label) for r in Role if r in GRANTABLE_ROLES]


def _expand(roles):
    """Add every role implied by the ones given, transitively."""
    out = set(roles)
    pending = list(out)
    while pending:
        for implied in IMPLIES.get(pending.pop(), ()):
            if implied not in out:
                out.add(implied)
                pending.append(implied)
    return out


def _roles_implying(role):
    """Every role whose holder also counts as holding ``role``."""
    out = {role}
    pending = [role]
    while pending:
        target = pending.pop()
        for candidate, implied in IMPLIES.items():
            if target in implied and candidate not in out:
                out.add(candidate)
                pending.append(candidate)
    return out


class RoleSet(frozenset):
    """
    The roles one person holds for one edition.

    Supports ``Role.CFP_CHAIR in roles``, ``roles.has(a, b)`` for "any of", and
    ``roles.is_cfp_chair`` so templates can read it without a custom filter.
    """

    def has(self, *roles):
        """True if any of ``roles`` is held."""
        return not self.isdisjoint({str(r) for r in roles})

    def labels(self):
        """Human-readable role names, for display."""
        return [Role(r).label for r in Role if r in self]

    def __getattr__(self, name):
        if name.startswith("is_"):
            value = name[3:]
            if value in Role.values:
                return value in self
        raise AttributeError(name)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def _granted(user, year):
    """Active assignments for this edition, plus any that span all editions."""
    from .models import RoleAssignment

    rows = (
        RoleAssignment.objects
        .filter(user=user, is_active=True)
        .filter(Q(conference_year=year) | Q(conference_year__isnull=True))
        .values_list("role", flat=True)
    )
    return set(rows)


def _derived(user, year):
    """Roles implied by records the person already has for this edition."""
    found = set()

    from tickets.models import Ticket
    if Ticket.objects.filter(
        user=user, status=Ticket.PAID, conference_year=year,
    ).exists():
        found.add(Role.ATTENDEE)

    # A speaker is someone with an accepted or confirmed proposal this edition.
    from cfp.models import Proposal
    if Proposal.objects.filter(
        speaker__user=user,
        conference_year=year,
        status__in=(Proposal.STATUS_ACCEPTED, Proposal.STATUS_CONFIRMED),
    ).exists():
        found.add(Role.SPEAKER)

    from grants.models import TravelGrantApplication
    if (
        TravelGrantApplication.objects
        .filter(user=user, conference_year=year)
        .exclude(status=TravelGrantApplication.STATUS_DRAFT)
        .exists()
    ):
        found.add(Role.GRANT_APPLICANT)

    return found


def roles_for(user, year=None):
    """
    Every role ``user`` holds for ``year`` (defaults to the current edition).

    Returns an empty :class:`RoleSet` for anonymous users, so callers can read
    it without checking ``is_authenticated`` first.
    """
    if user is None or not user.is_authenticated:
        return RoleSet()

    if year is None:
        year = current_year()

    found = _granted(user, year) | _derived(user, year)

    if user.is_superuser:
        found.add(Role.SUPER_ADMIN)
    if user.is_staff:
        found.add(Role.ORGANIZER)

    return RoleSet(str(r) for r in _expand(found))


def users_with_role(role, year=None):
    """
    Users holding ``role`` for ``year``, honouring implications -- asking for
    grant reviewers also returns grant chairs.

    Granted roles only; a derived role is a property of records, not a list of
    people, so query those records directly instead.
    """
    from django.contrib.auth import get_user_model
    from .models import RoleAssignment

    if year is None:
        year = current_year()

    holders = (
        RoleAssignment.objects
        .filter(role__in=[str(r) for r in _roles_implying(role)], is_active=True)
        .filter(Q(conference_year=year) | Q(conference_year__isnull=True))
        .values("user_id")
    )
    return (
        get_user_model().objects
        .filter(pk__in=holders)
        .order_by("first_name", "last_name", "email")
    )
