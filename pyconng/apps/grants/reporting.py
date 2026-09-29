"""
Grant reporting: the breakdowns sponsors and the PSF ask for.

Until now there was a CSV of approved grants and nothing else, so the reports the
requirement names could not be produced at all -- not because the query was hard, but
because gender was never collected. It is collected now, optionally and
self-describable, and used only here.

Two rules about that. Nobody has to answer it to get a grant, and nothing in the
review queue shows it: a breakdown for a funder is a legitimate use, and steering a
decision is not. "Not answered" is reported as its own figure rather than quietly
dropped, because a report that hides its own coverage is a report that misleads.
"""

from decimal import Decimal

from django.db.models import Count, Sum

from editions.current import current_year

from . import budget
from .models import TravelGrantApplication


def awarded(year=None):
    """
    Applications that actually cost money: accepted or paid.

    Deliberately not "approved". An unanswered offer may lapse, and reporting it to a
    sponsor as a grant given is a number that quietly becomes wrong.
    """
    return TravelGrantApplication.objects.filter(
        conference_year=year or current_year(),
        status__in=(
            TravelGrantApplication.STATUS_ACCEPTED,
            TravelGrantApplication.STATUS_PAID,
        ),
    )


def _breakdown(queryset, field, labeller=None):
    """``[{key, label, count, amount}]`` grouped by ``field``, largest first."""
    rows = (
        queryset.values(field)
        .annotate(count=Count("id"), amount=Sum("approved_amount"))
        .order_by("-count")
    )
    out = []
    for row in rows:
        key = row[field] or ""
        out.append(
            {
                "key": key,
                "label": labeller(key) if labeller else (key or "Not answered"),
                "count": row["count"],
                "amount": row["amount"] or Decimal("0"),
            }
        )
    return out


def by_city(year=None):
    return _breakdown(awarded(year), "city", lambda key: key or "Not given")


def by_country(year=None):
    return _breakdown(awarded(year), "country_of_residence", lambda key: key or "Not given")


def by_grant_type(year=None):
    labels = dict(TravelGrantApplication.GRANT_TYPE_CHOICES)
    return _breakdown(awarded(year), "grant_type", lambda key: labels.get(key, "Not set"))


def by_gender(year=None):
    """
    The gender breakdown, with self-descriptions folded into one row.

    Folded on purpose: a funder wants a distribution, and a report listing three
    people's own words about themselves beside a count of one is a report that
    identifies them.
    """
    labels = dict(TravelGrantApplication.GENDER_CHOICES)

    def label_for(key):
        if not key:
            return "Not answered"
        if key == TravelGrantApplication.GENDER_SELF_DESCRIBE:
            return "Self-described"
        return labels.get(key, key)

    return _breakdown(awarded(year), "gender", label_for)


def summary(year=None):
    """Everything a report page or an export needs, in one call."""
    year = year or current_year()
    granted = awarded(year)
    all_applications = TravelGrantApplication.objects.filter(
        conference_year=year
    ).exclude(status=TravelGrantApplication.STATUS_DRAFT)

    total_awarded = granted.aggregate(total=Sum("approved_amount"))["total"] or Decimal("0")
    first_timers = granted.filter(first_time_pycon=True).count()
    speakers = granted.filter(is_speaking=True).count()
    count = granted.count()

    return {
        "year": year,
        "applications": all_applications.count(),
        "awarded_count": count,
        "awarded_total": total_awarded,
        "average_award": (total_awarded / count) if count else Decimal("0"),
        "first_time_attendees": first_timers,
        "speakers_supported": speakers,
        "budget": budget.status(year),
        "by_city": by_city(year),
        "by_country": by_country(year),
        "by_gender": by_gender(year),
        "by_grant_type": by_grant_type(year),
        # Said plainly, because a breakdown whose coverage is unstated invites being
        # read as complete.
        "gender_answered": granted.exclude(gender="").count(),
    }


def to_csv(year=None):
    """The breakdowns as a CSV a sponsor report can be built from."""
    import csv
    import io

    data = summary(year)
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([f"PyCon Nigeria {data['year']} travel grants"])
    writer.writerow([])
    writer.writerow(["Applications received", data["applications"]])
    writer.writerow(["Grants awarded", data["awarded_count"]])
    writer.writerow(["Total awarded", data["awarded_total"]])
    writer.writerow(["Average award", f"{data['average_award']:.2f}"])
    writer.writerow(["First-time attendees supported", data["first_time_attendees"]])
    writer.writerow(["Speakers supported", data["speakers_supported"]])
    if data["budget"]["has_cap"]:
        writer.writerow(["Budget", data["budget"]["cap"]])
        writer.writerow(["Committed", data["budget"]["committed"]])
        writer.writerow(["Remaining", data["budget"]["remaining"]])

    for title, rows in (
        ("By city", data["by_city"]),
        ("By country", data["by_country"]),
        ("By gender", data["by_gender"]),
        ("By grant type", data["by_grant_type"]),
    ):
        writer.writerow([])
        writer.writerow([title, "Grants", "Amount"])
        for row in rows:
            writer.writerow([row["label"], row["count"], row["amount"]])

    writer.writerow([])
    writer.writerow([
        f"Gender was answered by {data['gender_answered']} of "
        f"{data['awarded_count']} recipients; it is optional."
    ])
    return output.getvalue()
