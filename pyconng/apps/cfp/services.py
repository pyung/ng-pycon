"""
Service layer for the CFP system.

All business logic (status transitions, email, export, snapshots)
lives here – not in views or templates.
"""

import csv
import io
import json
import logging

from django.db import transaction
from django.db.models import Avg
from django.utils import timezone

from editions.current import current_year

from .models import (
    CFPSettings,
    Proposal,
    ProposalSnapshot,
    Review,
    Speaker,
)

logger = logging.getLogger(__name__)


class CFPService:
    """Stateless helpers for the CFP workflow."""

    # ------------------------------------------------------------------
    # CFP status helpers
    # ------------------------------------------------------------------

    @staticmethod
    def get_current_cfp():
        """Return CFP settings for the current year, auto-closing if needed."""
        try:
            cfp = CFPSettings.objects.get(conference_year=current_year())
            if cfp.status == CFPSettings.STATUS_OPEN and cfp.is_past_deadline:
                cfp.status = CFPSettings.STATUS_CLOSED
                cfp.save()
            return cfp
        except CFPSettings.DoesNotExist:
            return None

    @staticmethod
    def is_cfp_open():
        cfp = CFPService.get_current_cfp()
        return cfp is not None and cfp.is_open

    # ------------------------------------------------------------------
    # Speaker helpers
    # ------------------------------------------------------------------

    @staticmethod
    def get_or_create_speaker(
        user,
        full_name,
        bio,
        organisation="",
        country="",
        first_time_speaker=False,
    ):
        """Create or update this user's speaker profile for the current year."""
        speaker, created = Speaker.objects.get_or_create(
            user=user,
            conference_year=current_year(),
            defaults={
                "full_name": full_name,
                "bio": bio,
                "organisation": organisation,
                "country": country,
                "first_time_speaker": first_time_speaker,
            },
        )
        if not created:
            speaker.full_name = full_name
            speaker.bio = bio
            speaker.organisation = organisation
            speaker.country = country
            speaker.first_time_speaker = first_time_speaker
            speaker.save()
        return speaker

    @staticmethod
    def schedule_clashes(year):
        """
        Everything wrong with the current grid.

        Three checks, each a mistake that is invisible until the day:

        * two talks in one room at overlapping times
        * one speaker due in two rooms at once
        * a talk scheduled outside the edition's own dates

        Returns a list of ``{"kind", "message", "talks"}``.
        """
        from editions.current import edition_for_year
        from program.models import Talk

        scheduled = list(
            Talk.objects.for_year(year).scheduled()
            .select_related("room").prefetch_related("speakers")
            .order_by("starts_at")
        )
        clashes = []

        def overlaps(a, b):
            return a.starts_at < b.ends_at and b.starts_at < a.ends_at

        for i, first in enumerate(scheduled):
            for second in scheduled[i + 1:]:
                if not overlaps(first, second):
                    continue
                if first.room_id == second.room_id:
                    clashes.append({
                        "kind": "room",
                        "message": (
                            f"{first.room.name}: “{first.title}” and “{second.title}” "
                            f"overlap at {first.starts_at:%a %H:%M}."
                        ),
                        "talks": [first, second],
                    })
                shared = {u.pk for u in first.speakers.all()} & {
                    u.pk for u in second.speakers.all()
                }
                if shared:
                    clashes.append({
                        "kind": "speaker",
                        "message": (
                            f"A speaker is due in two rooms at once: “{first.title}” "
                            f"and “{second.title}” at {first.starts_at:%a %H:%M}."
                        ),
                        "talks": [first, second],
                    })

        edition = edition_for_year(year)
        if edition and edition.starts_on:
            last_day = edition.ends_on or edition.starts_on
            for talk in scheduled:
                if not (edition.starts_on <= talk.day <= last_day):
                    clashes.append({
                        "kind": "date",
                        "message": (
                            f"“{talk.title}” is scheduled for {talk.day:%d %b}, outside "
                            f"the conference dates ({edition.dates_display})."
                        ),
                        "talks": [talk],
                    })

        return clashes

    @staticmethod
    def set_co_speakers(proposal, entries, request=None):
        """
        Replace a proposal's co-speakers with ``entries`` of ``(email, name)``.

        Links an account straight away where one exists for that address;
        otherwise the row stays pending and an invitation goes out. Removing a
        co-speaker from the form removes the row.

        Never invites the submitting speaker to co-present their own talk.
        """
        from django.contrib.auth import get_user_model
        from django.utils import timezone

        from .models import ProposalCoSpeaker

        User = get_user_model()
        own_email = (proposal.speaker.email or "").strip().lower()

        wanted = {}
        for raw_email, raw_name in entries:
            email = (raw_email or "").strip().lower()
            if not email or email == own_email:
                continue
            wanted[email] = (raw_name or "").strip() or email

        existing = {c.email.lower(): c for c in proposal.co_speakers.all()}

        for email in set(existing) - set(wanted):
            existing[email].delete()

        invited = []
        for email, name in wanted.items():
            account = User.objects.filter(email__iexact=email).first()
            row = existing.get(email)
            if row is None:
                row = ProposalCoSpeaker.objects.create(
                    proposal=proposal,
                    email=email,
                    display_name=name,
                    user=account,
                    linked_at=timezone.now() if account else None,
                )
                invited.append(row)
            else:
                changed = []
                if row.display_name != name:
                    row.display_name = name
                    changed.append("display_name")
                if account and not row.user_id:
                    row.user = account
                    row.linked_at = timezone.now()
                    changed += ["user", "linked_at"]
                if changed:
                    row.save(update_fields=changed)

        for row in invited:
            CFPService.send_co_speaker_invite(row, request=request)

        return proposal.co_speakers.all()

    @staticmethod
    def send_co_speaker_invite(co_speaker, request=None):
        """Tell someone they have been added to a proposal."""
        from emails.services import send_email, site_url

        base = f"{request.scheme}://{request.get_host()}" if request else site_url()
        proposal = co_speaker.proposal
        send_email(
            template="cfp/co_speaker_invite",
            to=[co_speaker.email],
            subject=f"You have been added to a PyCon Nigeria proposal: {proposal.title}",
            context={
                "co_speaker_name": co_speaker.display_name,
                "lead_speaker_name": proposal.speaker.full_name,
                "proposal_title": proposal.title,
                "conference_year": proposal.conference_year,
                "needs_account": co_speaker.user_id is None,
                "signup_url": f"{base}/accounts/signup/",
                "proposal_url": f"{base}/cfp/proposal/{proposal.id}/",
            },
            tags=["cfp", "co_speaker_invite"],
            conference_year=proposal.conference_year,
            fail_silently=True,
        )

    @staticmethod
    def get_speaker_for_user(user, conference_year=None):
        """This user's speaker profile for an edition, or None."""
        if not user or not user.is_authenticated:
            return None
        return Speaker.objects.filter(
            user=user, conference_year=conference_year or current_year(),
        ).first()

    # ------------------------------------------------------------------
    # Snapshot & audit helpers
    # ------------------------------------------------------------------

    @staticmethod
    def create_snapshot(proposal, snapshot_type="submission"):
        data = {
            "title": proposal.title,
            "abstract": proposal.abstract,
            "description": proposal.description,
            "track": proposal.track.name if proposal.track else None,
            "format": proposal.format,
            "duration": proposal.duration,
            "audience_level": proposal.audience_level,
            "prior_delivery": proposal.prior_delivery,
            "prior_delivery_link": proposal.prior_delivery_link,
            "slides_url": proposal.slides_url,
            "special_requirements": proposal.special_requirements,
            "speaker_name": proposal.speaker.full_name,
            "speaker_email": proposal.speaker.email,
        }
        return ProposalSnapshot.objects.create(
            proposal=proposal,
            data=data,
            snapshot_type=snapshot_type,
        )

    @staticmethod
    def log_action(
        proposal, action, old_status="", new_status="", actor="system", note="",
    ):
        """
        Record a proposal action on the shared audit trail.

        ``actor`` accepts either a user or a bare label (an email address, or
        "system"), so every existing call site keeps working while the ones that
        have a real account now link to it.
        """
        from audit.services import record

        is_user = hasattr(actor, "pk")
        return record(
            target=proposal,
            action=action,
            actor=actor if is_user else None,
            actor_label="" if is_user else str(actor or "system"),
            old_value=old_status,
            new_value=new_status,
            note=note,
        )

    # ------------------------------------------------------------------
    # Proposal lifecycle
    # ------------------------------------------------------------------

    @staticmethod
    @transaction.atomic
    def submit_proposal(proposal, actor_email=""):
        old_status = proposal.status
        proposal.status = Proposal.STATUS_SUBMITTED
        proposal.submitted_at = timezone.now()
        proposal.save()

        CFPService.create_snapshot(proposal, "submission")
        CFPService.log_action(
            proposal,
            "Submitted proposal",
            old_status=old_status,
            new_status=proposal.status,
            actor=actor_email or proposal.speaker.email,
        )
        return proposal

    @staticmethod
    @transaction.atomic
    def withdraw_proposal(proposal, actor_email=""):
        old_status = proposal.status
        proposal.status = Proposal.STATUS_WITHDRAWN
        proposal.save()

        CFPService.log_action(
            proposal,
            "Withdrew proposal",
            old_status=old_status,
            new_status=proposal.status,
            actor=actor_email or proposal.speaker.email,
        )
        return proposal

    @staticmethod
    @transaction.atomic
    def confirm_proposal(proposal, actor_email=""):
        """Speaker confirms their accepted talk."""
        if proposal.status != Proposal.STATUS_ACCEPTED:
            return proposal
        proposal.confirmed_at = timezone.now()
        old_status = proposal.status
        proposal.status = Proposal.STATUS_CONFIRMED
        proposal.save()

        CFPService.log_action(
            proposal,
            "Confirmed acceptance",
            old_status=old_status,
            new_status=proposal.status,
            actor=actor_email or proposal.speaker.email,
        )
        return proposal

    @staticmethod
    @transaction.atomic
    def bulk_decision(proposal_ids, decision, actor_email="system", actor=None):
        """Apply accept / reject / waitlist to a batch of proposals."""
        status_map = {
            "accept": Proposal.STATUS_ACCEPTED,
            "reject": Proposal.STATUS_REJECTED,
            "waitlist": Proposal.STATUS_WAITLISTED,
        }
        new_status = status_map.get(decision)
        if not new_status:
            raise ValueError(f"Invalid decision: {decision}")

        proposals = Proposal.objects.filter(id__in=proposal_ids)
        count = 0
        for proposal in proposals:
            old_status = proposal.status
            proposal.status = new_status
            proposal.save()
            CFPService.log_action(
                proposal,
                f"{decision.title()}ed proposal",
                old_status=old_status,
                new_status=new_status,
                actor=actor if getattr(actor, "pk", None) else actor_email,
            )
            count += 1
        return count

    # ------------------------------------------------------------------
    # Email helpers
    # ------------------------------------------------------------------

    @staticmethod
    def send_submission_confirmation(proposal, request=None):
        from emails.services import send_email

        from emails.services import site_url

        speaker = proposal.speaker
        base_url = f"{request.scheme}://{request.get_host()}" if request else site_url()
        proposal_url = f"{base_url}/cfp/proposal/{proposal.id}/"

        send_email(
            template="cfp/submission_confirmation",
            to=[speaker.email],
            subject=f"PyCon Nigeria CFP – Proposal Received: {proposal.title}",
            context={
                "speaker_name": speaker.full_name,
                "conference_year": proposal.conference_year,
                "proposal_title": proposal.title,
                "track_name": proposal.track.name if proposal.track else "N/A",
                "format_display": proposal.get_format_display(),
                "proposal_url": proposal_url,
            },
            tags=["cfp", "submission"],
            fail_silently=True,
        )

    #: Decision type -> the email it sends. Each has a template on disk, and each
    #: can be reworded in the admin by adding an EmailTemplate with that key.
    DECISION_TEMPLATES = {
        "acceptance": ("cfp/accepted", "Your PyCon Nigeria proposal was accepted"),
        "rejection": ("cfp/rejected", "About your PyCon Nigeria proposal"),
        "waitlist": ("cfp/waitlisted", "Your PyCon Nigeria proposal is on the waitlist"),
    }

    @staticmethod
    def send_decision_emails(proposal_ids, template_type, conference_year=None):
        """
        Email a decision to each proposal's speaker.

        The wording comes from the template on disk unless an organizer has
        overridden it in the admin. It used to require a database row and send
        nothing at all without one, which meant decisions reached nobody until
        somebody remembered to create the template.
        """
        from emails.services import send_email, site_url

        year = conference_year or current_year()
        chosen = CFPService.DECISION_TEMPLATES.get(template_type)
        if chosen is None:
            logger.error("Unknown decision email type %r", template_type)
            return 0
        template_key, default_subject = chosen

        proposals = Proposal.objects.filter(id__in=proposal_ids).select_related(
            "speaker__user", "track",
        )
        base = site_url()
        sent = 0
        for proposal in proposals:
            recipient = proposal.speaker.email
            if not recipient:
                logger.warning("Proposal %s has no speaker email; skipped", proposal.pk)
                continue
            try:
                delivered = send_email(
                    template=template_key,
                    to=[recipient],
                    subject=default_subject,
                    context={
                        "speaker_name": proposal.speaker.full_name,
                        "proposal_title": proposal.title,
                        "conference_year": str(proposal.conference_year),
                        "track_name": proposal.track.name if proposal.track else "",
                        "proposal_url": f"{base}/cfp/proposal/{proposal.id}/",
                    },
                    tags=["cfp", "decision", template_type],
                    conference_year=year,
                    fail_silently=False,
                )
                if delivered:
                    sent += 1
            except Exception as exc:  # noqa: BLE001 - one bad address must not stop the batch
                logger.error("Failed to send decision email to %s: %s", recipient, exc)
        return sent

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    @staticmethod
    def export_accepted_talks(conference_year=None, fmt="csv"):
        year = conference_year or current_year()
        proposals = (
            Proposal.objects.filter(
                conference_year=year,
                status__in=[Proposal.STATUS_ACCEPTED, Proposal.STATUS_CONFIRMED],
            )
            .select_related("speaker", "track")
            .order_by("track__display_order", "title")
        )

        rows = [
            {
                "title": p.title,
                "speaker": p.speaker.full_name,
                "speaker_email": p.speaker.email,
                "duration": p.duration,
                "track": p.track.name if p.track else "",
                "abstract": p.abstract,
                "format": p.get_format_display(),
                "audience_level": p.get_audience_level_display(),
            }
            for p in proposals
        ]

        if fmt == "json":
            return json.dumps(rows, indent=2)

        output = io.StringIO()
        if rows:
            writer = csv.DictWriter(output, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        return output.getvalue()

    # ------------------------------------------------------------------
    # Dashboard stats
    # ------------------------------------------------------------------

    @staticmethod
    def get_dashboard_stats(conference_year=None):
        year = conference_year or current_year()
        qs = Proposal.objects.filter(conference_year=year)

        stats = {
            "total": qs.count(),
            "draft": qs.filter(status=Proposal.STATUS_DRAFT).count(),
            "submitted": qs.filter(status=Proposal.STATUS_SUBMITTED).count(),
            "under_review": qs.filter(status=Proposal.STATUS_UNDER_REVIEW).count(),
            "accepted": qs.filter(status=Proposal.STATUS_ACCEPTED).count(),
            "rejected": qs.filter(status=Proposal.STATUS_REJECTED).count(),
            "waitlisted": qs.filter(status=Proposal.STATUS_WAITLISTED).count(),
            "withdrawn": qs.filter(status=Proposal.STATUS_WITHDRAWN).count(),
            "confirmed": qs.filter(status=Proposal.STATUS_CONFIRMED).count(),
        }

        # Average score across all reviewed proposals
        avg = Review.objects.filter(
            assignment__proposal__conference_year=year,
        ).aggregate(avg_score=Avg("score"))
        stats["avg_score"] = avg["avg_score"]

        return stats
