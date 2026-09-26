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

from pyconng.context_processors import CURRENT_YEAR

from .models import (
    CFPSettings,
    EmailTemplate,
    Proposal,
    ProposalAuditLog,
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
            cfp = CFPSettings.objects.get(conference_year=CURRENT_YEAR)
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
            conference_year=CURRENT_YEAR,
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
    def get_speaker_for_user(user, conference_year=None):
        """This user's speaker profile for an edition, or None."""
        if not user or not user.is_authenticated:
            return None
        return Speaker.objects.filter(
            user=user, conference_year=conference_year or CURRENT_YEAR,
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
        return ProposalAuditLog.objects.create(
            proposal=proposal,
            action=action,
            old_status=old_status,
            new_status=new_status,
            actor=actor,
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
    def bulk_decision(proposal_ids, decision, actor_email="system"):
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
                actor=actor_email,
            )
            count += 1
        return count

    # ------------------------------------------------------------------
    # Email helpers
    # ------------------------------------------------------------------

    @staticmethod
    def send_submission_confirmation(proposal, request=None):
        from emails.services import send_email

        speaker = proposal.speaker
        base_url = ""
        if request:
            base_url = f"{request.scheme}://{request.get_host()}"
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

    @staticmethod
    def send_decision_emails(proposal_ids, template_type, conference_year=None):
        from emails.services import send_email

        year = conference_year or CURRENT_YEAR
        try:
            template = EmailTemplate.objects.get(
                template_type=template_type, conference_year=year,
            )
        except EmailTemplate.DoesNotExist:
            logger.warning(
                "No email template found for type=%s year=%s", template_type, year,
            )
            return 0

        proposals = Proposal.objects.filter(id__in=proposal_ids).select_related(
            "speaker", "track",
        )
        sent = 0
        for proposal in proposals:
            context = {
                "speaker_name": proposal.speaker.full_name,
                "proposal_title": proposal.title,
                "conference_year": str(proposal.conference_year),
            }
            try:
                subject, body = template.render(context)
                send_email(
                    template="cfp/decision",
                    to=[proposal.speaker.email],
                    subject=subject,
                    context={"rendered_subject": subject, "rendered_body": body},
                    tags=["cfp", "decision", template_type],
                    fail_silently=False,
                )
                sent += 1
            except Exception as exc:
                logger.error(
                    "Failed to send email to %s: %s", proposal.speaker.email, exc,
                )
        return sent

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    @staticmethod
    def export_accepted_talks(conference_year=None, fmt="csv"):
        year = conference_year or CURRENT_YEAR
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
        year = conference_year or CURRENT_YEAR
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
