"""
Lapse travel grant offers nobody answered, and offer the money onwards.

    python manage.py expire_grant_offers --dry-run
    python manage.py expire_grant_offers

An offer that sits unanswered holds budget that could fund somebody on the waiting
list, which is the whole reason offers have a deadline. Safe to run on a schedule and
safe to run again after it is interrupted: an already-lapsed offer is not lapsed
twice.

Run it daily once decisions start going out, alongside
``manage.py remind_grant_offers``.
"""

from django.core.management.base import BaseCommand

from editions.current import current_year
from grants import offers


class Command(BaseCommand):
    help = "Lapse overdue travel grant offers and promote from the waitlist."

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int, help="Defaults to the current edition.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would lapse without changing or sending anything.",
        )
        parser.add_argument(
            "--no-promote",
            action="store_true",
            help="Release the budget without offering it to the waiting list.",
        )

    def handle(self, *args, **options):
        year = options["year"] or current_year()
        dry_run = options["dry_run"]

        lapsed, promoted = offers.expire_overdue(
            year, dry_run=dry_run, promote=not options["no_promote"]
        )

        verb = "would lapse" if dry_run else "lapsed"
        if lapsed:
            self.stdout.write(f"\n{len(lapsed)} offer(s) {verb}:")
            for application in lapsed:
                self.stdout.write(
                    self.style.WARNING(
                        f"    {application.user.email}  "
                        f"{application.approved_amount}  "
                        f"deadline {application.acceptance_deadline:%Y-%m-%d}"
                    )
                )
        else:
            self.stdout.write("No offers are past their deadline.")

        if promoted:
            self.stdout.write(f"\n{len(promoted)} offer(s) made from the waiting list:")
            for application in promoted:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"    {application.user.email}  {application.approved_amount}"
                    )
                )

        self.stdout.write("")
        if dry_run:
            self.stdout.write("Nothing was written and nobody was emailed.")
