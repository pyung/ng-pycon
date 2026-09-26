"""
Remind speakers who have a draft proposal that the CFP deadline is approaching.

Drafts are not reviewed, so someone who started a proposal and never pressed
submit misses out silently. Run this from cron in the last couple of weeks of
the call:

    manage.py send_cfp_reminders --days 7

Each proposal is reminded once. The record of having been reminded is the audit
trail itself, so re-running the command is safe and needs no extra column.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from audit.models import AuditEntry
from audit.services import record
from django.contrib.contenttypes.models import ContentType

from editions.current import current_year

REMINDER_ACTION = "Sent CFP deadline reminder"


class Command(BaseCommand):
    help = "Email speakers whose proposals are still drafts as the CFP deadline nears."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=7,
            help="Only remind when the deadline is within this many days (default 7).",
        )
        parser.add_argument(
            "--year",
            type=int,
            default=None,
            help="Edition to remind for (default: the current edition).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report who would be emailed without sending anything.",
        )

    def handle(self, *args, **options):
        from cfp.models import CFPSettings, Proposal
        from emails.services import send_email, site_url

        year = options["year"] or current_year()
        days = options["days"]
        dry_run = options["dry_run"]

        cfp = CFPSettings.objects.filter(conference_year=year).first()
        if cfp is None:
            self.stderr.write(f"No CFP settings for {year}; nothing to do.")
            return
        if not cfp.is_open:
            self.stdout.write(f"CFP {year} is not open; nothing to do.")
            return

        remaining = cfp.submission_deadline - timezone.now()
        if remaining.days > days:
            self.stdout.write(
                f"Deadline is {remaining.days} days away, more than the {days}-day "
                "window; nothing to do."
            )
            return

        drafts = (
            Proposal.objects
            .filter(conference_year=year, status=Proposal.STATUS_DRAFT)
            .select_related("speaker__user")
        )

        already = set(
            AuditEntry.objects
            .filter(
                action=REMINDER_ACTION,
                target_type=ContentType.objects.get_for_model(Proposal),
            )
            .values_list("target_id", flat=True)
        )

        base = site_url()
        deadline_display = cfp.submission_deadline.strftime("%d %B %Y at %H:%M")
        sent = skipped = 0

        for proposal in drafts:
            if str(proposal.pk) in already:
                skipped += 1
                continue
            recipient = proposal.speaker.email
            if not recipient:
                skipped += 1
                continue

            if dry_run:
                self.stdout.write(f"  would remind {recipient} about {proposal.title!r}")
                sent += 1
                continue

            delivered = send_email(
                template="cfp/deadline_reminder",
                to=[recipient],
                subject=f"Your PyCon Nigeria {year} proposal is still a draft",
                context={
                    "speaker_name": proposal.speaker.full_name,
                    "proposal_title": proposal.title,
                    "conference_year": year,
                    "deadline": deadline_display,
                    "proposal_url": f"{base}/cfp/proposal/{proposal.id}/edit/",
                },
                tags=["cfp", "reminder"],
                conference_year=year,
            )
            if delivered:
                record(
                    target=proposal,
                    action=REMINDER_ACTION,
                    actor_label="system",
                    note=f"Deadline {deadline_display}",
                )
                sent += 1
            else:
                skipped += 1

        verb = "would remind" if dry_run else "reminded"
        self.stdout.write(
            self.style.SUCCESS(f"{verb} {sent}; skipped {skipped} (already reminded or no address).")
        )
