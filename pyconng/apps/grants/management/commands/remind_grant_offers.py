"""
Remind recipients whose travel grant offer is about to expire.

    python manage.py remind_grant_offers --dry-run
    python manage.py remind_grant_offers --days 3

Worth sending, because the usual reason an offer lapses is not indifference but an
email read on a phone and forgotten. Run it daily; the ``--days`` window means each
person is reminded once as they cross it rather than every day.
"""

from django.core.management.base import BaseCommand

from editions.current import current_year
from grants.emails import send_offer_reminder
from grants.models import TravelGrantApplication


class Command(BaseCommand):
    help = "Email travel grant recipients whose offer expires soon."

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int, help="Defaults to the current edition.")
        parser.add_argument(
            "--days",
            type=int,
            default=3,
            help="Remind offers expiring in this many days or fewer. Default 3.",
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Report without sending."
        )

    def handle(self, *args, **options):
        year = options["year"] or current_year()
        window = options["days"]
        dry_run = options["dry_run"]

        candidates = [
            application
            for application in TravelGrantApplication.objects.filter(
                conference_year=year,
                status__in=TravelGrantApplication.AWAITING_ACCEPTANCE_STATUSES,
                acceptance_deadline__isnull=False,
            ).select_related("user")
            if not application.offer_has_expired
            and (application.days_left_to_accept or 0) <= window
        ]

        for application in candidates:
            if not dry_run:
                send_offer_reminder(application)
            self.stdout.write(
                f"    {application.user.email}  "
                f"{application.days_left_to_accept} day(s) left  "
                f"{application.approved_amount}"
            )

        self.stdout.write("")
        verb = "would be reminded" if dry_run else "reminded"
        self.stdout.write(f"{len(candidates)} recipient(s) {verb}.")
        if dry_run:
            self.stdout.write("Nothing was sent.")
