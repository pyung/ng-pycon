"""
The grant budget, enforced rather than displayed.

The requirement was explicitly "so you can't overspend", and until now
``max_grant_budget`` was stored, shown on a dashboard, and never once consulted:
``bulk_decision`` would approve any amount. A cap that is only displayed is not a
cap, and the failure mode is silent -- nobody notices until the money is gone.

Two rules, both applied at the moment of decision:

* no single award above ``max_per_applicant``;
* no award that would take the committed total past ``max_grant_budget``.

"Committed" counts approved offers as firmly as accepted ones. An offer nobody has
answered still holds its money, because it cannot be promised to anybody else until
they reply -- which is the whole reason offers have a deadline.
"""

from decimal import Decimal

from django.db.models import Sum

from editions.current import current_year

from .models import GrantSettings, TravelGrantApplication


class BudgetError(Exception):
    """An award was refused by the budget, with a reason worth showing somebody."""


def settings_for(year=None):
    """The edition's grant settings, or None when none has been created."""
    return GrantSettings.objects.filter(
        conference_year=year or current_year()
    ).first()


def committed(year=None, exclude_pk=None):
    """
    Total money already promised for the edition.

    ``exclude_pk`` leaves one application out, so re-deciding an existing award is
    checked against everybody else rather than against itself -- otherwise raising
    an approved grant from 50,000 to 60,000 would be measured as if it were a new
    60,000 on top of the old 50,000.
    """
    year = year or current_year()
    queryset = TravelGrantApplication.objects.filter(
        conference_year=year,
        status__in=TravelGrantApplication.COMMITTED_STATUSES,
    )
    if exclude_pk is not None:
        queryset = queryset.exclude(pk=exclude_pk)
    return queryset.aggregate(total=Sum("approved_amount"))["total"] or Decimal("0")


def status(year=None):
    """
    Where the budget stands, as one dict for a dashboard or a decision screen.

    ``remaining`` is the figure the requirement asked for and the dashboard never
    had. It is None when no cap is set, which the template must distinguish from
    zero: "no limit" and "nothing left" are opposite situations.
    """
    year = year or current_year()
    settings_obj = settings_for(year)
    cap = Decimal(settings_obj.max_grant_budget) if settings_obj else Decimal("0")
    spent = committed(year)

    has_cap = cap > 0
    remaining = (cap - spent) if has_cap else None
    return {
        "year": year,
        "settings": settings_obj,
        "cap": cap if has_cap else None,
        "has_cap": has_cap,
        "committed": spent,
        "remaining": remaining,
        "per_applicant_cap": (
            Decimal(settings_obj.max_per_applicant)
            if settings_obj and settings_obj.max_per_applicant > 0
            else None
        ),
        "percent_used": (
            int(min(spent / cap * 100, 100)) if has_cap and cap > 0 else None
        ),
        "is_exhausted": bool(has_cap and remaining is not None and remaining <= 0),
        "is_overspent": bool(has_cap and remaining is not None and remaining < 0),
    }


def check_award(application, amount, year=None):
    """
    Whether ``amount`` may be awarded to ``application``.

    Returns ``(ok, reason)`` rather than raising, because the caller is usually
    deciding on a list of twenty applications and needs to report which ones were
    refused rather than stopping at the first.
    """
    amount = Decimal(amount or 0)
    if amount < 0:
        return False, "A grant cannot be a negative amount."
    if amount == 0:
        # Permitted: approving a zero-value grant is how a chair records "you are in
        # the programme, we are not paying travel" without inventing a status.
        return True, ""

    state = status(year or application.conference_year)
    per_applicant = state["per_applicant_cap"]
    if per_applicant is not None and amount > per_applicant:
        return False, (
            f"{amount:,.0f} is above the {per_applicant:,.0f} per-applicant limit "
            f"for this edition."
        )

    if not state["has_cap"]:
        return True, ""

    already = committed(state["year"], exclude_pk=application.pk)
    remaining = state["cap"] - already
    if amount > remaining:
        return False, (
            f"{amount:,.0f} would take the total to {already + amount:,.0f}, past "
            f"the {state['cap']:,.0f} budget. {max(remaining, Decimal('0')):,.0f} is "
            f"left."
        )
    return True, ""


def assert_award(application, amount, year=None):
    """Raise :class:`BudgetError` if the award is not allowed. For single decisions."""
    ok, reason = check_award(application, amount, year)
    if not ok:
        raise BudgetError(reason)
    return Decimal(amount or 0)


def affordable_waitlisted(year=None, limit=None):
    """
    Waitlisted applications the remaining budget could cover, best-scored first.

    Ordered by review score because that is the order the panel decided, and falling
    back to how long they have waited when nothing has been scored. Each candidate's
    request is measured against the budget left *after* the ones above it, so the
    list is one a chair can approve straight down rather than a list of maybes.
    """
    year = year or current_year()
    state = status(year)
    waitlisted = (
        TravelGrantApplication.objects.filter(
            conference_year=year, status=TravelGrantApplication.STATUS_WAITLISTED
        )
        .select_related("user")
        .order_by("submitted_at")
    )

    scored = sorted(
        waitlisted,
        key=lambda a: (-(a.average_score or 0), a.submitted_at or a.created_at),
    )

    remaining = state["remaining"]
    per_applicant = state["per_applicant_cap"]
    affordable = []
    for application in scored:
        wanted = Decimal(application.approved_amount or application.total_requested or 0)
        if per_applicant is not None:
            wanted = min(wanted, per_applicant)
        if wanted <= 0:
            continue
        if remaining is not None:
            if wanted > remaining:
                continue
            remaining -= wanted
        affordable.append({"application": application, "amount": wanted})
        if limit is not None and len(affordable) >= limit:
            break
    return affordable
