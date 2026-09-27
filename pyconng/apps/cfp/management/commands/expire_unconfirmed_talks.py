"""
Release accepted talks nobody confirmed by the deadline.

A slot held by a speaker who has gone quiet is a slot the waitlist cannot have.
Run this after the confirmation deadline:

    manage.py expire_unconfirmed_talks --dry-run
    manage.py expire_unconfirmed_talks

Proposals move to "lapsed" rather than "withdrawn": the speaker did not decline,
they simply did not answer, and the record should say which.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from editions.current import current_year


class Command(BaseCommand):
    help = "Move accepted-but-unconfirmed proposals to lapsed after the deadline."

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int, default=None)
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would change without changing it.",
        )

    def handle(self, *args, **options):
        from cfp.models import CFPSettings, Proposal
        from cfp.services import CFPService
        from emails.services import send_email

        year = options["year"] or current_year()
        dry_run = options["dry_run"]

        cfp = CFPSettings.objects.filter(conference_year=year).first()
        if cfp is None:
            self.stderr.write(f"No CFP settings for {year}.")
            return
        if not cfp.confirmation_deadline:
            self.stderr.write(
                f"CFP {year} has no confirmation deadline set, so nothing can lapse."
            )
            return
        if timezone.now() < cfp.confirmation_deadline:
            self.stdout.write(
                f"Confirmation deadline is {cfp.confirmation_deadline:%d %b %Y %H:%M}, "
                "which has not passed yet. Nothing to do."
            )
            return

        stale = (
            Proposal.objects
            .filter(
                conference_year=year,
                status=Proposal.STATUS_ACCEPTED,
                confirmed_at__isnull=True,
            )
            .select_related("speaker__user")
        )

        deadline_display = cfp.confirmation_deadline.strftime("%d %B %Y at %H:%M")
        count = 0
        for proposal in stale:
            if dry_run:
                self.stdout.write(f"  would lapse: {proposal.title!r} ({proposal.speaker.email})")
                count += 1
                continue

            old_status = proposal.status
            proposal.status = Proposal.STATUS_LAPSED
            proposal.save(update_fields=["status", "updated_at"])
            CFPService.log_action(
                proposal,
                "Lapsed: not confirmed by the deadline",
                old_status=old_status,
                new_status=proposal.status,
                actor="system",
                note=f"Deadline {deadline_display}",
            )
            if proposal.speaker.email:
                send_email(
                    template="cfp/confirmation_lapsed",
                    to=[proposal.speaker.email],
                    subject=f"Your PyCon Nigeria {year} talk slot has been released",
                    context={
                        "speaker_name": proposal.speaker.full_name,
                        "proposal_title": proposal.title,
                        "conference_year": year,
                        "deadline": deadline_display,
                    },
                    tags=["cfp", "lapsed"],
                    conference_year=year,
                )
            count += 1

        verb = "would lapse" if dry_run else "lapsed"
        self.stdout.write(self.style.SUCCESS(f"{verb} {count} proposal(s)."))
