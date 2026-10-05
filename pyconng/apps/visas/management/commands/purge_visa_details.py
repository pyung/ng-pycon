"""
Delete passport details whose retention period has elapsed.

    python manage.py purge_visa_details --dry-run
    python manage.py purge_visa_details

Safe to run daily from the day the module ships: it does nothing until the
conference is over and the retention window in the settings has passed. Run it on a
schedule rather than by hand, because retention that depends on somebody
remembering is retention that does not happen.
"""

from django.core.management.base import BaseCommand

from editions.current import current_year
from visas import retention
from visas.models import VisaSettings


class Command(BaseCommand):
    help = "Delete visa passport details past their retention date."

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int, help="Defaults to the current edition.")
        parser.add_argument(
            "--all-years",
            action="store_true",
            help="Every edition that has settings, not just the current one.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be deleted and change nothing.",
        )

    def handle(self, *args, **options):
        if options["all_years"]:
            years = list(
                VisaSettings.objects.values_list("conference_year", flat=True)
            )
        else:
            years = [options.get("year") or current_year()]

        total = 0
        for year in sorted(years):
            count = retention.purge_year(year, dry_run=options["dry_run"])
            total += count
            if count:
                verb = "would be deleted for" if options["dry_run"] else "deleted for"
                self.stdout.write(
                    self.style.SUCCESS(f"{year}: details {verb} {count} request(s).")
                )
            else:
                settings_obj = VisaSettings.for_year(year)
                due_on = settings_obj.purge_on_or_after()
                when = f" Next due {due_on}." if due_on else " The edition has no dates set."
                self.stdout.write(f"{year}: nothing to delete.{when}")

        if options["dry_run"]:
            self.stdout.write("\nNothing was written.")
        elif not total:
            self.stdout.write("\nNothing needed deleting.")
