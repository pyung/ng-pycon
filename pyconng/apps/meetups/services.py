"""Promoting a meetup talk into the conference CFP."""

import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from editions.current import current_year

logger = logging.getLogger(__name__)


class MeetupService:

    @staticmethod
    @transaction.atomic
    def promote_talk_to_cfp(talk, actor=None, request=None, conference_year=None):
        """
        Create a draft conference proposal from a meetup talk.

        The proposal is left as a **draft** on purpose: the speaker owns their
        submission, so we pre-fill what the meetup already told us and let them
        complete the parts only they can answer (track, length, level) before
        submitting.

        Raises ValidationError when the talk has no account to attach a proposal
        to. Returns the existing proposal if it was already promoted, so the
        action is safe to repeat.
        """
        from cfp.models import Proposal
        from cfp.services import CFPService

        if talk.promoted_proposal_id:
            return talk.promoted_proposal

        if not talk.user_id:
            raise ValidationError(
                f"'{talk.title}' has no linked account. Proposals belong to an "
                "account, so link the speaker to a user before promoting."
            )

        year = conference_year or current_year()

        speaker = CFPService.get_speaker_for_user(talk.user, year)
        if speaker is None:
            speaker = CFPService.get_or_create_speaker(
                user=talk.user,
                full_name=talk.speaker_name or talk.user.get_full_name(),
                bio="",
                organisation="",
                country="",
                first_time_speaker=False,
            )

        proposal = Proposal.objects.create(
            speaker=speaker,
            title=talk.title,
            abstract=talk.abstract or "",
            description=(
                f"Promoted from the {talk.meetup.city} meetup on "
                f"{talk.meetup.starts_at:%d %B %Y}."
            ),
            format=Proposal.FORMAT_TALK,
            duration=MeetupService._default_duration(year),
            audience_level=Proposal.AUDIENCE_INTERMEDIATE,
            status=Proposal.STATUS_DRAFT,
            conference_year=year,
            slides_url=talk.slides_url or "",
            prior_delivery=True,
            prior_delivery_link=talk.recording_url or "",
        )

        talk.promoted_proposal = proposal
        talk.promoted_at = timezone.now()
        talk.promoted_by = actor if actor and actor.is_authenticated else None
        talk.save(update_fields=["promoted_proposal", "promoted_at", "promoted_by"])

        CFPService.log_action(
            proposal,
            "Promoted from meetup talk",
            new_status=Proposal.STATUS_DRAFT,
            actor=actor,
            note=f"{talk.meetup.title} ({talk.meetup.city})",
        )

        MeetupService._notify_promoted_speaker(talk, proposal, request)
        return proposal

    @staticmethod
    def _default_duration(year):
        """First allowed duration for the edition, falling back to 30 minutes."""
        from cfp.models import CFPSettings

        settings_obj = CFPSettings.objects.filter(conference_year=year).first()
        if settings_obj and settings_obj.allowed_durations:
            return settings_obj.allowed_durations[0]
        return 30

    @staticmethod
    def _notify_promoted_speaker(talk, proposal, request=None):
        from emails.services import send_email

        if not talk.user or not talk.user.email:
            return

        base_url = ""
        if request:
            base_url = f"{request.scheme}://{request.get_host()}"

        send_email(
            template="meetups/talk_promoted",
            to=[talk.user.email],
            subject=f"Your meetup talk could be a PyCon Nigeria talk: {talk.title}",
            context={
                "speaker_name": talk.speaker_name or talk.user.get_full_name(),
                "talk_title": talk.title,
                "meetup_title": talk.meetup.title,
                "meetup_city": talk.meetup.city,
                "conference_year": proposal.conference_year,
                "proposal_url": f"{base_url}/cfp/proposal/{proposal.id}/edit/",
            },
            tags=["meetups", "promotion"],
            fail_silently=True,
        )
