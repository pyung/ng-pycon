"""
Issue tickets to everyone who should not pay for one.

    python manage.py issue_free_tickets --dry-run
    python manage.py issue_free_tickets
    python manage.py issue_free_tickets --reason speaker --year 2027

Safe to run repeatedly: somebody who already has a ticket for a reason is skipped,
so the usual output after the first run is a list of skips. Run it after speaker
confirmations close, after grant decisions, and once more the week before the
event to catch late additions.
"""

from django.core.management.base import BaseCommand, CommandError

from editions.current import current_year
from tickets.issuing import ELIGIBILITY, IssueError, issue_for_reason


class Command(BaseCommand):
    help = "Issue complimentary tickets to speakers, volunteers and grant recipients."

    def add_arguments(self, parser):
        parser.add_argument(
            "--year",
            type=int,
            help="Edition to issue for. Defaults to the current one.",
        )
        parser.add_argument(
            "--reason",
            action="append",
            choices=sorted(ELIGIBILITY),
            help="Only this category. Repeatable. Defaults to all of them.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report who would get a ticket without issuing any.",
        )
        parser.add_argument(
            "--quiet-emails",
            action="store_true",
            help="Issue the tickets without telling anybody. For a backfill.",
        )

    def handle(self, *args, **options):
        year = options["year"] or current_year()
        reasons = options["reason"] or sorted(ELIGIBILITY)
        dry_run = options["dry_run"]
        notify = not options["quiet_emails"]

        total_issued = 0
        total_skipped = 0

        for reason in reasons:
            try:
                issued, skipped = issue_for_reason(
                    reason, year, dry_run=dry_run, notify=notify
                )
            except IssueError as exc:
                raise CommandError(str(exc)) from exc

            total_issued += len(issued)
            total_skipped += len(skipped)

            verb = "would get" if dry_run else "issued"
            self.stdout.write(f"\n{reason} ({year})")
            if not issued and not skipped:
                self.stdout.write("    nobody eligible")
                continue
            for user in issued:
                self.stdout.write(
                    self.style.SUCCESS(f"    {verb}  {user.email}")
                )
            for user in skipped:
                self.stdout.write(f"    has one  {user.email}")

        self.stdout.write("")
        if dry_run:
            self.stdout.write(
                f"{total_issued} ticket(s) would be issued, {total_skipped} already there. "
                f"Nothing was written."
            )
            return
        self.stdout.write(
            f"{total_issued} ticket(s) issued, {total_skipped} already there."
        )
        if total_issued and not notify:
            self.stdout.write(
                self.style.WARNING(
                    "Nobody was emailed. They will not know they have a ticket."
                )
            )
