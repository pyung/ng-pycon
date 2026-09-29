"""
Tests for the call for volunteers.

Weighted towards the acceptance path, because accepting somebody has to do four
things at once and any one of them silently not happening is a volunteer who turns
up without a ticket, or without a team, or not at all. And towards the refusals:
somebody double-booked, somebody put on a shift before they were accepted, a
certificate for a shift nobody worked.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import RoleAssignment
from accounts.roles import Role, roles_for
from audit.models import AuditEntry
from editions.current import invalidate
from editions.models import Edition
from volunteers import services
from volunteers.forms import VolunteerApplicationForm
from volunteers.models import (
    Shift,
    ShiftAssignment,
    VolunteerApplication,
    VolunteerAvailability,
    VolunteerSettings,
    VolunteerTeam,
)

User = get_user_model()
YEAR = 2026


def make_edition(year=YEAR, starts=None, ends=None):
    starts = starts or (timezone.localdate() + timedelta(days=60))
    ends = ends or (starts + timedelta(days=2))
    Edition.objects.update_or_create(
        year=year,
        defaults={
            "name": f"PyCon Nigeria {year}",
            "theme": "2026",
            "is_current": True,
            "is_published": True,
            "starts_on": starts,
            "ends_on": ends,
            "venue": "Landmark Centre, Lagos",
        },
    )
    invalidate()
    return starts, ends


def make_user(email="ada@example.com", **kwargs):
    return User.objects.create_user(
        username=email.split("@")[0], email=email, password="x", **kwargs
    )


def open_call(year=YEAR, **kwargs):
    settings_obj = VolunteerSettings.for_year(year)
    settings_obj.status = VolunteerSettings.STATUS_OPEN
    settings_obj.application_deadline = timezone.now() + timedelta(days=14)
    for key, value in kwargs.items():
        setattr(settings_obj, key, value)
    settings_obj.save()
    return settings_obj


def make_team(name="Registration", year=YEAR, **kwargs):
    defaults = {"conference_year": year, "slug": name.lower().replace(" ", "-")}
    defaults.update(kwargs)
    return VolunteerTeam.objects.create(name=name, **defaults)


def make_application(user=None, year=YEAR, status=VolunteerApplication.STATUS_SUBMITTED, teams=(), **kwargs):
    user = user or make_user()
    defaults = {
        "full_name": "Ada Lovelace",
        "motivation": "I would like to help the community that helped me.",
        "status": status,
    }
    defaults.update(kwargs)
    application = VolunteerApplication.objects.create(
        user=user, conference_year=year, **defaults
    )
    if teams:
        application.teams.set(teams)
    return application


def make_shift(team, year=YEAR, hours_from_now=24, length=4, capacity=1, title="Desk"):
    start = timezone.now() + timedelta(hours=hours_from_now)
    return Shift.objects.create(
        conference_year=year,
        team=team,
        title=title,
        starts_at=start,
        ends_at=start + timedelta(hours=length),
        capacity=capacity,
    )


class SettingsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_edition()

    def setUp(self):
        invalidate()

    def test_settings_appear_on_demand_as_a_draft(self):
        """The cautious default: a call nobody has configured is not open."""
        settings_obj = VolunteerSettings.for_year(YEAR)
        self.assertEqual(settings_obj.status, VolunteerSettings.STATUS_DRAFT)
        self.assertFalse(settings_obj.is_open)
        self.assertEqual(VolunteerSettings.for_year(YEAR).pk, settings_obj.pk)

    def test_a_passed_deadline_closes_the_call_on_save(self):
        settings_obj = VolunteerSettings.for_year(YEAR)
        settings_obj.status = VolunteerSettings.STATUS_OPEN
        settings_obj.application_deadline = timezone.now() - timedelta(minutes=1)
        settings_obj.save()
        self.assertEqual(settings_obj.status, VolunteerSettings.STATUS_CLOSED)
        self.assertFalse(settings_obj.is_open)

    def test_no_deadline_means_open_while_the_status_says_so(self):
        settings_obj = VolunteerSettings.for_year(YEAR)
        settings_obj.status = VolunteerSettings.STATUS_OPEN
        settings_obj.application_deadline = None
        settings_obj.save()
        self.assertTrue(settings_obj.is_open)

    def test_availability_days_come_from_the_edition(self):
        """Moving the conference moves the question, rather than needing an edit here."""
        starts, ends = make_edition(
            starts=timezone.localdate() + timedelta(days=30),
        )
        settings_obj = VolunteerSettings.for_year(YEAR)
        settings_obj.setup_days_before = 1
        settings_obj.teardown_days_after = 1
        settings_obj.save()

        days = settings_obj.availability_days()
        # One set-up day, three conference days, one pack-down day.
        self.assertEqual(len(days), 5)
        self.assertEqual(days[0], (starts - timedelta(days=1), "Set-up"))
        self.assertEqual(days[1][1], "Conference")
        self.assertEqual(days[-1], (ends + timedelta(days=1), "Pack-down"))

    def test_no_edition_dates_means_no_availability_question(self):
        Edition.objects.filter(year=YEAR).update(starts_on=None, ends_on=None)
        invalidate()
        self.assertEqual(VolunteerSettings.for_year(YEAR).availability_days(), [])

    def test_certificates_are_shut_until_their_date(self):
        settings_obj = VolunteerSettings.for_year(YEAR)
        self.assertFalse(settings_obj.certificates_available)
        settings_obj.certificates_available_from = timezone.now() + timedelta(days=1)
        settings_obj.save()
        self.assertFalse(settings_obj.certificates_available)
        settings_obj.certificates_available_from = timezone.now() - timedelta(minutes=1)
        settings_obj.save()
        self.assertTrue(settings_obj.certificates_available)


class ApplicationFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.starts, cls.ends = make_edition()

    def setUp(self):
        invalidate()
        self.settings_obj = open_call(max_team_choices=2)
        self.registration = make_team("Registration")
        self.av = make_team("AV")
        self.hosts = make_team("Room hosts")
        self.user = make_user()

    def _payload(self, **overrides):
        first_day = self.settings_obj.availability_days()[0][0]
        data = {
            "full_name": "Ada Lovelace",
            "motivation": "I would like to help the community that helped me.",
            "experience": VolunteerApplication.EXPERIENCE_NONE,
            "teams": [self.registration.pk],
            VolunteerApplicationForm.availability_field(first_day, "morning"): "on",
        }
        data.update(overrides)
        return data

    def _form(self, **overrides):
        return VolunteerApplicationForm(
            self._payload(**overrides),
            conference_year=YEAR,
            settings_obj=self.settings_obj,
        )

    def test_a_minimal_application_is_valid(self):
        form = self._form()
        self.assertTrue(form.is_valid(), form.errors)

    def test_only_three_answers_are_actually_required(self):
        """A form that interrogates somebody offering free labour gets abandoned."""
        form = self._form()
        self.assertTrue(form.is_valid(), form.errors)
        required = [name for name, field in form.fields.items() if field.required]
        self.assertEqual(sorted(required), ["full_name", "motivation", "teams"])

    def test_leaving_the_experience_question_blank_is_fine(self):
        payload = self._payload()
        payload.pop("experience")
        form = VolunteerApplicationForm(
            payload, conference_year=YEAR, settings_obj=self.settings_obj
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["experience"], VolunteerApplication.EXPERIENCE_NONE
        )

    def test_too_many_teams_is_refused_with_the_limit(self):
        form = self._form(teams=[self.registration.pk, self.av.pk, self.hosts.pk])
        self.assertFalse(form.is_valid())
        self.assertIn("at most 2", str(form.errors))

    def test_a_one_word_motivation_is_refused(self):
        form = self._form(motivation="yes")
        self.assertFalse(form.is_valid())
        self.assertIn("a little more", str(form.errors))

    def test_no_availability_at_all_is_refused_with_a_way_round_it(self):
        payload = self._payload()
        payload = {k: v for k, v in payload.items() if not k.startswith("avail_")}
        form = VolunteerApplicationForm(
            payload, conference_year=YEAR, settings_obj=self.settings_obj
        )
        self.assertFalse(form.is_valid())
        self.assertIn("apply anyway", str(form.errors))

    def test_availability_is_saved_as_rows(self):
        form = self._form()
        self.assertTrue(form.is_valid(), form.errors)
        application = form.save(commit=False)
        application.user = self.user
        application.conference_year = YEAR
        application.save()
        form.save_m2m()
        form.save_availability(application)

        slots = list(application.availability.all())
        self.assertEqual(len(slots), 1)
        self.assertEqual(slots[0].period, "morning")
        self.assertEqual(slots[0].day, self.settings_obj.availability_days()[0][0])

    def test_unticking_a_day_removes_it(self):
        """
        The form shows the whole picture, so an unticked box means "not any more".
        Merging instead would make it impossible to take a day back.
        """
        days = self.settings_obj.availability_days()
        first, second = days[0][0], days[1][0]
        form = self._form(**{
            VolunteerApplicationForm.availability_field(first, "morning"): "on",
            VolunteerApplicationForm.availability_field(second, "afternoon"): "on",
        })
        self.assertTrue(form.is_valid(), form.errors)
        application = form.save(commit=False)
        application.user = self.user
        application.conference_year = YEAR
        application.save()
        form.save_m2m()
        form.save_availability(application)
        self.assertEqual(application.availability.count(), 2)

        # Now only the second day.
        payload = self._payload(**{
            VolunteerApplicationForm.availability_field(second, "afternoon"): "on",
        })
        payload.pop(VolunteerApplicationForm.availability_field(first, "morning"), None)
        second_form = VolunteerApplicationForm(
            payload, instance=application, conference_year=YEAR, settings_obj=self.settings_obj
        )
        self.assertTrue(second_form.is_valid(), second_form.errors)
        second_form.save()
        self.assertEqual(
            [(s.day, s.period) for s in application.availability.all()],
            [(second, "afternoon")],
        )

    def test_only_active_teams_are_offered(self):
        self.av.is_active = False
        self.av.save()
        form = VolunteerApplicationForm(conference_year=YEAR, settings_obj=self.settings_obj)
        offered = set(form.fields["teams"].queryset.values_list("name", flat=True))
        self.assertEqual(offered, {"Registration", "Room hosts"})


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SubmissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_edition()

    def setUp(self):
        invalidate()
        mail.outbox = []
        open_call()
        self.team = make_team()
        self.user = make_user()

    def test_submitting_acknowledges_and_stamps(self):
        application = make_application(
            self.user, status=VolunteerApplication.STATUS_DRAFT, teams=[self.team]
        )
        application, sent = services.submit(application)
        self.assertTrue(sent)
        self.assertEqual(application.status, VolunteerApplication.STATUS_SUBMITTED)
        self.assertIsNotNone(application.submitted_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])

    def test_submitting_twice_does_not_email_twice(self):
        """The usual cause is a double-clicked button."""
        application = make_application(
            self.user, status=VolunteerApplication.STATUS_DRAFT, teams=[self.team]
        )
        services.submit(application)
        mail.outbox = []
        application, sent = services.submit(application)
        self.assertFalse(sent)
        self.assertEqual(len(mail.outbox), 0)

    def test_one_application_per_person_per_edition(self):
        from django.db import IntegrityError, transaction

        make_application(self.user, teams=[self.team])
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_application(self.user, teams=[self.team])

    def test_the_same_person_may_apply_in_a_different_year(self):
        make_edition(2025)
        make_edition(YEAR)
        invalidate()
        make_application(self.user, year=YEAR, teams=[self.team])
        other = make_application(self.user, year=2025)
        self.assertEqual(VolunteerApplication.objects.filter(user=self.user).count(), 2)
        self.assertEqual(other.conference_year, 2025)

    def test_an_application_is_editable_until_it_is_decided(self):
        application = make_application(self.user, teams=[self.team])
        self.assertTrue(application.is_editable)
        services.decide(
            application, VolunteerApplication.STATUS_ACCEPTED, team=self.team
        )
        self.assertFalse(application.is_editable)

    def test_withdrawing_releases_any_shifts(self):
        application = make_application(self.user, teams=[self.team])
        services.decide(application, VolunteerApplication.STATUS_ACCEPTED, team=self.team)
        shift = make_shift(self.team)
        services.assign_shift(application, shift)
        self.assertEqual(shift.assigned_count, 1)

        services.withdraw(application)
        self.assertEqual(application.status, VolunteerApplication.STATUS_WITHDRAWN)
        self.assertEqual(shift.assigned_count, 0)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class DecisionTests(TestCase):
    """The acceptance path, which has to do four things at once."""

    @classmethod
    def setUpTestData(cls):
        make_edition()

    def setUp(self):
        invalidate()
        mail.outbox = []
        open_call()
        self.lead = make_user("lead@example.com")
        self.team = make_team("Registration", lead=self.lead)
        self.coordinator = make_user("coord@example.com")
        self.user = make_user()
        self.application = make_application(self.user, teams=[self.team])

    def _accept(self, **kwargs):
        return services.decide(
            self.application,
            VolunteerApplication.STATUS_ACCEPTED,
            by=self.coordinator,
            team=self.team,
            **kwargs,
        )

    def test_accepting_grants_the_volunteer_role(self):
        self._accept()
        self.assertIn(Role.VOLUNTEER, roles_for(self.user, YEAR))

    def test_accepting_issues_the_free_ticket(self):
        """
        Module 4's auto-issue requirement finally has a trigger: the machinery ran
        from a command, and what was missing was the moment to call it from.
        """
        from tickets.models import Ticket

        self._accept()
        ticket = Ticket.objects.get(user=self.user, conference_year=YEAR)
        self.assertEqual(ticket.status, Ticket.PAID)
        self.assertEqual(ticket.complimentary_reason, "volunteer")
        self.assertEqual(ticket.ticket_sales.count(), 1)
        self.assertTrue(ticket.ticket_sales.first().checkin_code)

    def test_the_ticket_carries_the_name_they_gave(self):
        self._accept()
        from tickets.models import TicketSale

        self.assertEqual(
            TicketSale.objects.get(ticket__user=self.user).full_name, "Ada Lovelace"
        )

    def test_accepting_defaults_the_lead_to_the_teams_own(self):
        self._accept()
        self.assertEqual(self.application.assigned_lead, self.lead)
        self.assertEqual(self.application.lead_contact, self.lead)

    def test_a_different_lead_can_be_named_for_a_large_team(self):
        other = make_user("other@example.com")
        self._accept(lead=other)
        self.assertEqual(self.application.assigned_lead, other)

    def test_only_one_email_goes_out_on_acceptance(self):
        """
        The acceptance email says they have a ticket, so the ticket module's own
        notification is suppressed. Two emails saying the same thing is noise.
        """
        self._accept()
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, [self.user.email])
        self.assertIn("volunteer team", message.subject)
        self.assertIn("Registration", message.body)

    def test_accepting_twice_does_not_duplicate_anything(self):
        """Happens when a coordinator fixes a team assignment."""
        from tickets.models import Ticket

        self._accept()
        mail.outbox = []
        other_team = make_team("AV")
        services.decide(
            self.application,
            VolunteerApplication.STATUS_ACCEPTED,
            by=self.coordinator,
            team=other_team,
        )
        self.assertEqual(Ticket.objects.filter(user=self.user).count(), 1)
        self.assertEqual(
            RoleAssignment.objects.filter(
                user=self.user, role=Role.VOLUNTEER.value, conference_year=YEAR
            ).count(),
            1,
        )
        self.assertEqual(self.application.assigned_team, other_team)

    def test_accepting_without_a_team_is_refused(self):
        with self.assertRaises(services.VolunteerError) as caught:
            services.decide(
                self.application, VolunteerApplication.STATUS_ACCEPTED, by=self.coordinator
            )
        self.assertIn("needs a team", str(caught.exception))

    def test_a_rejection_takes_the_role_back_and_releases_shifts(self):
        self._accept()
        shift = make_shift(self.team)
        services.assign_shift(self.application, shift)
        mail.outbox = []

        services.decide(
            self.application,
            VolunteerApplication.STATUS_NOT_SELECTED,
            by=self.coordinator,
        )
        self.assertNotIn(Role.VOLUNTEER, roles_for(self.user, YEAR))
        self.assertEqual(shift.assigned_count, 0)
        self.assertEqual(len(mail.outbox), 1)

    def test_revoking_keeps_the_record_that_it_was_granted(self):
        """Deactivated rather than deleted, so the history stays answerable."""
        self._accept()
        services.decide(
            self.application, VolunteerApplication.STATUS_WAITLISTED, by=self.coordinator
        )
        assignment = RoleAssignment.objects.get(
            user=self.user, role=Role.VOLUNTEER.value, conference_year=YEAR
        )
        self.assertFalse(assignment.is_active)

    def test_reinstating_reuses_the_same_row(self):
        self._accept()
        services.decide(
            self.application, VolunteerApplication.STATUS_WAITLISTED, by=self.coordinator
        )
        self._accept()
        assignments = RoleAssignment.objects.filter(
            user=self.user, role=Role.VOLUNTEER.value, conference_year=YEAR
        )
        self.assertEqual(assignments.count(), 1)
        self.assertTrue(assignments.first().is_active)

    def test_every_outcome_has_an_email(self):
        for status in VolunteerApplication.DECIDED_STATUSES:
            self.assertIn(status, services.DECISION_EMAILS)

    def test_a_waitlist_decision_says_to_buy_a_ticket(self):
        services.decide(
            self.application,
            VolunteerApplication.STATUS_WAITLISTED,
            by=self.coordinator,
        )
        self.assertIn("buy a ticket", mail.outbox[0].body)

    def test_the_decision_note_reaches_the_applicant(self):
        services.decide(
            self.application,
            VolunteerApplication.STATUS_NOT_SELECTED,
            by=self.coordinator,
            note="Room hosts filled up very quickly this year.",
        )
        self.assertIn("filled up very quickly", mail.outbox[0].body)

    def test_the_internal_note_never_does(self):
        self.application.internal_note = "Turned up late to the meetup."
        self.application.save()
        services.decide(
            self.application,
            VolunteerApplication.STATUS_NOT_SELECTED,
            by=self.coordinator,
        )
        for message in mail.outbox:
            haystack = message.body + "".join(
                body for body, _ in getattr(message, "alternatives", [])
            )
            self.assertNotIn("Turned up late", haystack)

    def test_the_decision_is_recorded_in_the_trail(self):
        self._accept()
        entry = AuditEntry.objects.filter(action__icontains="decided").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.new_value, "Accepted")
        self.assertIn("Registration", entry.note)

    def test_a_decision_stamp_and_the_status_never_disagree(self):
        self._accept()
        self.assertIsNotNone(self.application.decided_at)
        services.decide(
            self.application,
            VolunteerApplication.STATUS_UNDER_REVIEW,
            by=self.coordinator,
        )
        self.application.refresh_from_db()
        self.assertIsNone(self.application.decided_at)

    def test_a_ticketing_failure_does_not_lose_the_acceptance(self):
        """
        An acceptance has happened and the applicant must be told. A ticketing
        problem is an organizer's to fix, not a reason to roll back good news.
        """
        from unittest.mock import patch

        with patch(
            "tickets.issuing.issue_complimentary_ticket",
            side_effect=RuntimeError("Paystack is on fire"),
        ):
            with self.assertLogs("volunteers.services", level="ERROR"):
                self._accept()
        self.assertEqual(self.application.status, VolunteerApplication.STATUS_ACCEPTED)
        self.assertIn(Role.VOLUNTEER, roles_for(self.user, YEAR))
        self.assertEqual(len(mail.outbox), 1)


class ShiftTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_edition()

    def setUp(self):
        invalidate()
        open_call()
        self.team = make_team()
        self.coordinator = make_user("coord@example.com")
        self.user = make_user()
        self.application = make_application(self.user, teams=[self.team])
        services.decide(
            self.application,
            VolunteerApplication.STATUS_ACCEPTED,
            team=self.team,
            notify=False,
        )

    def test_a_shift_must_end_after_it_starts(self):
        start = timezone.now() + timedelta(days=1)
        shift = Shift(
            conference_year=YEAR, team=self.team, title="Backwards",
            starts_at=start, ends_at=start - timedelta(hours=1), capacity=1,
        )
        with self.assertRaises(ValidationError) as caught:
            shift.full_clean()
        self.assertIn("ends_at", caught.exception.error_dict)

    def test_a_shift_needs_at_least_one_volunteer(self):
        shift = make_shift(self.team)
        shift.capacity = 0
        with self.assertRaises(ValidationError):
            shift.full_clean()

    def test_assigning_and_the_counts_that_follow(self):
        shift = make_shift(self.team, capacity=2)
        _, created = services.assign_shift(self.application, shift, by=self.coordinator)
        self.assertTrue(created)
        self.assertEqual(shift.assigned_count, 1)
        self.assertEqual(shift.remaining, 1)
        self.assertTrue(shift.is_understaffed)
        self.assertFalse(shift.is_full)

    def test_assigning_twice_is_a_no_op(self):
        shift = make_shift(self.team, capacity=2)
        services.assign_shift(self.application, shift)
        _, created = services.assign_shift(self.application, shift)
        self.assertFalse(created)
        self.assertEqual(shift.assigned_count, 1)

    def test_somebody_not_accepted_cannot_be_rostered(self):
        other = make_application(make_user("grace@example.com"), teams=[self.team])
        shift = make_shift(self.team)
        with self.assertRaises(services.VolunteerError) as caught:
            services.assign_shift(other, shift)
        self.assertIn("has not been accepted", str(caught.exception))

    def test_a_full_shift_is_refused(self):
        shift = make_shift(self.team, capacity=1)
        services.assign_shift(self.application, shift)
        other = make_application(make_user("grace@example.com"), teams=[self.team])
        services.decide(
            other, VolunteerApplication.STATUS_ACCEPTED, team=self.team, notify=False
        )
        with self.assertRaises(services.VolunteerError) as caught:
            services.assign_shift(other, shift)
        self.assertIn("already has its 1 volunteer", str(caught.exception))

    def test_an_overlapping_shift_is_refused_by_default(self):
        """
        The check that matters: a coordinator building a roster late at night will
        double-book somebody, and the volunteer stands in the wrong room.
        """
        first = make_shift(self.team, hours_from_now=24, length=4, title="Morning")
        clashing = make_shift(self.team, hours_from_now=26, length=4, title="Overlapping")
        services.assign_shift(self.application, first)

        with self.assertRaises(services.VolunteerError) as caught:
            services.assign_shift(self.application, clashing)
        self.assertIn("overlaps", str(caught.exception))
        self.assertIn("Morning", str(caught.exception))

    def test_an_overlap_can_be_allowed_deliberately(self):
        first = make_shift(self.team, hours_from_now=24, length=4, title="Morning")
        clashing = make_shift(self.team, hours_from_now=26, length=4, title="Overlapping")
        services.assign_shift(self.application, first)
        _, created = services.assign_shift(
            self.application, clashing, allow_overlap=True
        )
        self.assertTrue(created)

    def test_adjacent_shifts_do_not_count_as_overlapping(self):
        first = make_shift(self.team, hours_from_now=24, length=4, title="Morning")
        after = make_shift(self.team, hours_from_now=28, length=4, title="Afternoon")
        self.assertFalse(first.overlaps(after))
        services.assign_shift(self.application, first)
        _, created = services.assign_shift(self.application, after)
        self.assertTrue(created)

    def test_a_shift_from_another_edition_is_refused(self):
        make_edition(2025)
        make_edition(YEAR)
        invalidate()
        other_team = make_team("Other", year=2025)
        shift = make_shift(other_team, year=2025)
        with self.assertRaises(services.VolunteerError) as caught:
            services.assign_shift(self.application, shift)
        self.assertIn("different edition", str(caught.exception))

    def test_staffing_gaps_name_what_is_short(self):
        make_shift(self.team, capacity=3, title="Short")
        filled = make_shift(self.team, capacity=1, hours_from_now=48, title="Filled")
        services.assign_shift(self.application, filled)

        gaps = services.staffing_gaps(YEAR)
        self.assertEqual([g["shift"].title for g in gaps], ["Short"])
        self.assertEqual(gaps[0]["needed"], 3)

    def test_attendance_is_separate_from_being_rostered(self):
        shift = make_shift(self.team)
        assignment, _ = services.assign_shift(self.application, shift)
        self.assertFalse(assignment.attended)
        self.assertFalse(self.application.worked_a_shift)

        services.mark_attended(assignment, by=self.coordinator)
        assignment.refresh_from_db()
        self.assertTrue(assignment.attended)
        self.assertTrue(self.application.worked_a_shift)

    def test_attendance_can_be_cleared(self):
        shift = make_shift(self.team)
        assignment, _ = services.assign_shift(self.application, shift)
        services.mark_attended(assignment, by=self.coordinator)
        services.mark_attended(assignment, by=self.coordinator, attended=False)
        assignment.refresh_from_db()
        self.assertFalse(assignment.attended)
        self.assertFalse(self.application.worked_a_shift)

    def test_unassigning_records_it(self):
        shift = make_shift(self.team)
        assignment, _ = services.assign_shift(self.application, shift)
        services.unassign_shift(assignment, by=self.coordinator)
        self.assertEqual(shift.assigned_count, 0)
        self.assertTrue(
            AuditEntry.objects.filter(action__icontains="removed from a shift").exists()
        )


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class RosterPublishingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_edition()

    def setUp(self):
        invalidate()
        mail.outbox = []
        open_call()
        self.team = make_team()
        self.rostered = make_application(make_user("on@example.com"), teams=[self.team])
        self.unrostered = make_application(make_user("off@example.com"), teams=[self.team])
        for application in (self.rostered, self.unrostered):
            services.decide(
                application,
                VolunteerApplication.STATUS_ACCEPTED,
                team=self.team,
                notify=False,
            )
        services.assign_shift(self.rostered, make_shift(self.team))
        mail.outbox = []

    def test_shifts_are_hidden_until_published(self):
        self.assertFalse(services.settings_for(YEAR).shifts_published)

    def test_publishing_tells_only_the_people_on_the_roster(self):
        """
        Telling somebody the roster is out when they are not on it is worse than
        saying nothing.
        """
        told = services.publish_shifts(YEAR)
        self.assertEqual(told, 1)
        self.assertEqual([m.to for m in mail.outbox], [["on@example.com"]])
        self.assertTrue(services.settings_for(YEAR).shifts_published)

    def test_hiding_it_again_is_silent(self):
        services.publish_shifts(YEAR)
        mail.outbox = []
        services.unpublish_shifts(YEAR)
        self.assertFalse(services.settings_for(YEAR).shifts_published)
        self.assertEqual(len(mail.outbox), 0)

    def test_publishing_with_nobody_rostered_emails_nobody(self):
        ShiftAssignment.objects.all().delete()
        told = services.publish_shifts(YEAR)
        self.assertEqual(told, 0)
        self.assertEqual(len(mail.outbox), 0)


class TeamTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_edition()

    def setUp(self):
        invalidate()
        open_call()
        self.team = make_team("Registration", target_count=3)

    def test_staffing_counts_only_accepted_volunteers(self):
        accepted = make_application(make_user("a@example.com"), teams=[self.team])
        services.decide(
            accepted, VolunteerApplication.STATUS_ACCEPTED, team=self.team, notify=False
        )
        make_application(make_user("b@example.com"), teams=[self.team])

        self.assertEqual(self.team.assigned_count, 1)
        self.assertEqual(self.team.interested_count, 2)
        self.assertEqual(self.team.shortfall, 2)
        self.assertEqual(self.team.staffing_display, "1/3")

    def test_no_target_means_no_shortfall(self):
        team = make_team("AV", target_count=0)
        self.assertEqual(team.shortfall, 0)
        self.assertEqual(team.staffing_display, "0 assigned")

    def test_a_team_slug_is_unique_within_an_edition_only(self):
        make_edition(2025)
        make_edition(YEAR)
        invalidate()
        other = make_team("Registration", year=2025)
        self.assertEqual(other.slug, self.team.slug)
        self.assertNotEqual(other.conference_year, self.team.conference_year)


class OverviewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_edition()

    def setUp(self):
        invalidate()
        open_call(target_count=5)
        self.team = make_team("Registration", target_count=2)

    def test_the_numbers_a_coordinator_checks_first(self):
        waiting = make_application(make_user("w@example.com"), teams=[self.team])
        accepted = make_application(make_user("a@example.com"), teams=[self.team])
        services.decide(
            accepted, VolunteerApplication.STATUS_ACCEPTED, team=self.team, notify=False
        )
        make_shift(self.team, capacity=2)

        overview = services.overview(YEAR)
        self.assertEqual(overview["total"], 2)
        self.assertEqual(overview["awaiting"], 1)
        self.assertEqual(overview["accepted"], 1)
        self.assertEqual(overview["target"], 5)
        self.assertEqual(overview["still_needed"], 4)
        self.assertEqual(overview["shift_count"], 1)
        self.assertEqual(len(overview["understaffed"]), 1)
        self.assertIn(accepted, overview["unassigned_accepted"])
        self.assertNotIn(waiting, overview["unassigned_accepted"])

    def test_no_target_means_no_shortfall_figure(self):
        open_call(target_count=0)
        self.assertEqual(services.overview(YEAR)["still_needed"], 0)
