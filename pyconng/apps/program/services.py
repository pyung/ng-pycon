"""Building the programme, the schedule grid, and the speakers listing."""

import logging

from django.db import transaction

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Auto-generating the programme from accepted CFP data
# ---------------------------------------------------------------------------

@transaction.atomic
def build_programme_from_cfp(year, actor=None):
    """
    Create or refresh ``Talk`` records from the edition's accepted proposals.

    Idempotent: a talk already linked to a proposal is updated rather than
    duplicated, so a chair can re-run this after a late acceptance without
    tidying up afterwards.

    Scheduling is never touched. Room and time are a human decision, and
    re-running this must not wipe a grid somebody spent an afternoon on.

    Returns ``{"created", "updated", "skipped"}``.
    """
    from audit.services import record
    from cfp.models import Proposal

    from .models import Talk

    accepted = (
        Proposal.objects
        .filter(
            conference_year=year,
            status__in=(Proposal.STATUS_ACCEPTED, Proposal.STATUS_CONFIRMED),
        )
        .select_related("speaker__user", "track")
    )

    created = updated = skipped = 0
    for proposal in accepted:
        talk = Talk.objects.filter(proposal=proposal).first()
        fields = {
            "conference_year": proposal.conference_year,
            "title": proposal.title,
            "abstract": _strip_html(proposal.abstract),
            "speaker_names": proposal.speaker.full_name,
            "track_name": proposal.track.name if proposal.track else "",
            "duration": proposal.duration,
            "slides_url": proposal.slides_url or "",
        }

        if talk is None:
            talk = Talk.objects.create(proposal=proposal, is_published=False, **fields)
            if proposal.speaker.user_id:
                talk.speakers.add(proposal.speaker.user)
            created += 1
            continue

        # Only rewrite what the CFP owns; leave scheduling and publication alone.
        changed = [f for f, v in fields.items() if getattr(talk, f) != v]
        if not changed:
            skipped += 1
            continue
        for field, value in fields.items():
            setattr(talk, field, value)
        talk.save(update_fields=list(fields) + ["updated_at"])
        if proposal.speaker.user_id:
            talk.speakers.add(proposal.speaker.user)
        updated += 1

    result = {"created": created, "updated": updated, "skipped": skipped}
    if created or updated:
        from editions.current import edition_for_year

        record(
            target=edition_for_year(year) or accepted.first(),
            action="Built programme from accepted proposals",
            actor=actor,
            note=f"{created} created, {updated} updated, {skipped} unchanged",
            conference_year=year,
        )
    return result


def _strip_html(value):
    """Proposal abstracts are rich text; Talk.abstract is plain."""
    from django.utils.html import strip_tags

    return strip_tags(value or "").strip()


# ---------------------------------------------------------------------------
# The schedule grid
# ---------------------------------------------------------------------------

def schedule_grid(year, only_talk_ids=None):
    """
    The published schedule as days of rows of per-room cells.

    ``[{"day", "rows": [{"time", "cells": [talk or None, ...]}]}]`` with cells in
    the same order as the rooms returned alongside, so a template can render a
    table without doing any lookups of its own.

    Pass ``only_talk_ids`` to narrow it to somebody's personal schedule.
    """
    from .models import Room, Talk

    rooms = list(
        Room.objects.filter(conference_year=year, is_published=True)
        .order_by("display_order", "name")
    )
    if not rooms:
        return [], []

    talks = (
        Talk.objects.published().for_year(year).scheduled()
        .select_related("room").prefetch_related("speakers")
    )
    if only_talk_ids is not None:
        talks = talks.filter(pk__in=only_talk_ids)
    talks = talks.order_by("starts_at", "room__display_order")

    room_index = {room.pk: i for i, room in enumerate(rooms)}

    # day -> start time -> row of cells
    days = {}
    for talk in talks:
        day = talk.day
        slot = talk.starts_at
        rows = days.setdefault(day, {})
        cells = rows.setdefault(slot, [None] * len(rooms))
        position = room_index.get(talk.room_id)
        if position is None:
            # A talk in a room that is no longer published: keep it out of the
            # grid rather than dropping it silently into the wrong column.
            continue
        cells[position] = talk

    grid = [
        {
            "day": day,
            "rows": [
                {"time": slot, "cells": rows[slot]} for slot in sorted(rows)
            ],
        }
        for day in sorted(d for d in days if d is not None)
    ]
    return rooms, grid


# ---------------------------------------------------------------------------
# The speakers listing
# ---------------------------------------------------------------------------

def speakers_for(year):
    """
    Everyone speaking at an edition, with their talks.

    Built from talks rather than from CFP records, so archived editions whose
    speakers never had accounts still appear. Where a talk is linked to an
    account, the CFP speaker profile supplies the bio, organisation and country.
    """
    from cfp.models import Speaker

    from .models import Talk

    talks = (
        Talk.objects.published().for_year(year)
        .prefetch_related("speakers")
        .order_by("starts_at", "display_order", "title")
    )

    profiles = {
        s.user_id: s
        for s in Speaker.objects.filter(conference_year=year).select_related("user")
        if s.user_id
    }

    people = {}
    for talk in talks:
        linked = list(talk.speakers.all())
        if linked:
            for user in linked:
                key = ("user", user.pk)
                entry = people.setdefault(key, _person_from_user(user, profiles.get(user.pk)))
                entry["talks"].append(talk)
        else:
            for raw in (talk.speaker_names or "").split(","):
                name = raw.strip()
                if not name:
                    continue
                key = ("name", name.lower())
                entry = people.setdefault(key, {
                    "name": name, "bio": "", "organisation": "", "country": "",
                    "user": None, "talks": [],
                })
                entry["talks"].append(talk)

    return sorted(people.values(), key=lambda p: p["name"].lower())


def _person_from_user(user, profile):
    return {
        "name": (
            (profile.full_name if profile else "")
            or user.get_full_name()
            or user.email
            or user.username
        ),
        "bio": profile.bio if profile else "",
        "organisation": profile.organisation if profile else "",
        "country": profile.country if profile else "",
        "user": user,
        "talks": [],
    }
