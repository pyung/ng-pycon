"""
Tell the waiting list when places open up.

    python manage.py notify_ticket_waitlist --dry-run
    python manage.py notify_ticket_waitlist

Places come back through refunds and released allocations, so this is worth running
on a schedule once a type has sold out. Only tells as many people as there are
places, and marks each one told as it goes, so an interrupted run does not email
anybody twice.
"""

from django.core.management.base import BaseCommand

from editions.current import current_year
from tickets.models import TicketSettings, TicketType
from tickets.waitlist import notify_available, waiting_for


class Command(BaseCommand):
    help = "Email the ticket waitlist when places become available."

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int, help="Defaults to the current edition.")
        parser.add_argument(
            "--limit",
            type=int,
            help="Most people to tell per ticket type. Defaults to the places free.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report who would be told, without sending or marking anything.",
        )

    def handle(self, *args, **options):
        year = options["year"] or current_year()
        dry_run = options["dry_run"]

        settings_obj = TicketSettings.for_year(year)
        if not settings_obj.waitlist_enabled:
            self.stdout.write(
                self.style.WARNING(
                    f"The waitlist is switched off for {year}. Nothing to do."
                )
            )
            return

        told_total = 0
        for ticket_type in TicketType.objects.active().for_year(year):
            waiting = waiting_for(ticket_type, year).count()
            free = ticket_type.remaining_count
            if not waiting:
                continue
            if free <= 0:
                self.stdout.write(
                    f"  {ticket_type.name}: {waiting} waiting, no places free"
                )
                continue

            told = notify_available(
                ticket_type, limit=options["limit"], dry_run=dry_run
            )
            told_total += len(told)
            verb = "would tell" if dry_run else "told"
            self.stdout.write(
                self.style.SUCCESS(
                    f"  {ticket_type.name}: {free} free, {waiting} waiting, "
                    f"{verb} {len(told)}"
                )
            )
            for entry in told:
                self.stdout.write(f"      {entry.email}")

        self.stdout.write("")
        if dry_run:
            self.stdout.write(f"{told_total} would be told. Nothing was sent.")
        else:
            self.stdout.write(f"{told_total} told.")
