"""
Who may request an invitation letter, and on what basis.

An invitation letter is a document an embassy relies on. It says a named person is
expected at a real event, and if that turns out not to be true, the next applicant
from Nigeria is believed a little less. So eligibility is read from records the
applicant cannot edit -- a paid ticket, an accepted proposal, an awarded grant --
and never from a box they tick.

The basis is also what the letter *says about them*, which is the second reason not
to take their word for it: "an accepted speaker at PyCon Nigeria 2027" is a claim we
are making on our own letterhead.

One deliberate loosening: a grant offer counts before it is accepted. The acceptance
window is a fortnight and an embassy needs three months, so requiring the acceptance
first would lock out exactly the people this module exists for -- somebody who cannot
afford the flight and also cannot afford to wait.
"""

from dataclasses import dataclass

from editions.current import current_year

from .models import VisaLetterRequest

#: Bases in the order a letter should prefer them. A speaker who also holds a ticket
#: is described as a speaker: it is the stronger statement and the true one.
PRECEDENCE = (
    VisaLetterRequest.BASIS_SPEAKER,
    VisaLetterRequest.BASIS_GRANTEE,
    VisaLetterRequest.BASIS_ORGANIZER,
    VisaLetterRequest.BASIS_ATTENDEE,
)


@dataclass(frozen=True)
class Assessment:
    """Whether somebody may request a letter, and how to say so."""

    ok: bool
    basis: str = ""
    reason: str = ""
    #: Every basis found, for the officer's queue -- a speaker who also bought a
    #: ticket is a stronger case than a speaker who did not, and worth seeing.
    all_bases: tuple = ()

    @property
    def basis_label(self):
        if not self.basis:
            return ""
        return dict(VisaLetterRequest.BASIS_CHOICES)[self.basis]


def _bases(user, year):
    """Every basis this person genuinely holds for ``year``."""
    found = []

    from cfp.models import Proposal
    if Proposal.objects.filter(
        speaker__user=user,
        conference_year=year,
        status__in=(Proposal.STATUS_ACCEPTED, Proposal.STATUS_CONFIRMED),
    ).exists():
        found.append(VisaLetterRequest.BASIS_SPEAKER)

    # Committed, not merely awarded: an approved offer counts, for the lead-time
    # reason in this module's docstring.
    from grants.models import TravelGrantApplication
    if TravelGrantApplication.objects.filter(
        user=user,
        conference_year=year,
        status__in=TravelGrantApplication.COMMITTED_STATUSES,
    ).exists():
        found.append(VisaLetterRequest.BASIS_GRANTEE)

    from accounts.roles import Role, roles_for
    roles = roles_for(user, year)
    if roles.has(Role.ORGANIZER, Role.VOLUNTEER):
        found.append(VisaLetterRequest.BASIS_ORGANIZER)

    from tickets.models import Ticket
    if Ticket.objects.filter(
        user=user, conference_year=year, status=Ticket.PAID
    ).exists():
        found.append(VisaLetterRequest.BASIS_ATTENDEE)

    return found


def assess(user, year=None):
    """
    Whether ``user`` may request a letter for ``year``, and on what basis.

    The refusal text is written to be read by the person refused, so it says what
    would make them eligible rather than only that they are not.
    """
    if user is None or not user.is_authenticated:
        return Assessment(
            ok=False, reason="Please sign in to request an invitation letter."
        )

    year = year or current_year()
    found = _bases(user, year)
    if not found:
        return Assessment(
            ok=False,
            reason=(
                "Invitation letters go to people we can confirm are attending: "
                "ticket holders, accepted speakers, travel grant recipients and the "
                "organizing team. Buy a ticket or wait for your proposal or grant "
                "decision, and this page will open for you."
            ),
        )

    ordered = tuple(b for b in PRECEDENCE if b in found)
    return Assessment(ok=True, basis=ordered[0], all_bases=ordered)


def basis_for(request_obj):
    """
    Re-read the basis for an existing request, for the officer reviewing it.

    Read again at decision time rather than trusted from submission, because things
    change in between: a refunded ticket or a lapsed grant offer means the claim on
    the letterhead is no longer true, and that is exactly what the officer is there
    to catch.
    """
    return assess(request_obj.user, request_obj.conference_year)
