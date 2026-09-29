"""
View-level tests for the call for volunteers.

Two things a service test cannot cover: who is allowed to see what, and whether the
pages a volunteer actually reads say the right thing at each stage. The new pages
also go through the module 1 accessibility audit, since a volunteer checking a shift
is doing it on a phone, standing up, probably in a hurry.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from wagtail.models import Page, Site

from accounts.models import RoleAssignment
from accounts.roles import Role
from editions.current import invalidate
from editions.models import Edition
from quality.audit import audit_html, errors
from volunteers import services
from volunteers.forms import VolunteerApplicationForm
from volunteers.models import (
    Shift,
    VolunteerApplication,
    VolunteerSettings,
    VolunteerTeam,
)

User = get_user_model()
YEAR = 2026


def build_site():
    from home.models import HomePage

    starts = timezone.localdate() + timedelta(days=60)
    Edition.objects.update_or_create(
        year=YEAR,
        defaults={
            "name": f"PyCon Nigeria {YEAR}",
            "theme": "2026",
            "is_current": True,
            "is_published": True,
            "starts_on": starts,
            "ends_on": starts + timedelta(days=2),
            "venue": "Landmark Centre, Lagos",
        },
    )
    invalidate()

    root = Page.objects.get(depth=1)
    home = HomePage(title=f"PyCon Nigeria {YEAR}", slug="home-2026")
    root.add_child(instance=home)
    home.save_revision().publish()

    site = Site.objects.get(is_default_site=True)
    site.root_page = home
    site.save()
    return home


def open_call(**kwargs):
    settings_obj = VolunteerSettings.for_year(YEAR)
    settings_obj.status = VolunteerSettings.STATUS_OPEN
    settings_obj.application_deadline = timezone.now() + timedelta(days=14)
    for key, value in kwargs.items():
        setattr(settings_obj, key, value)
    settings_obj.save()
    return settings_obj


def make_user(email, **kwargs):
    return User.objects.create_user(
        username=email.split("@")[0], email=email, password="secret", **kwargs
    )


def make_team(name="Registration", **kwargs):
    defaults = {"conference_year": YEAR, "slug": name.lower().replace(" ", "-")}
    defaults.update(kwargs)
    return VolunteerTeam.objects.create(name=name, **defaults)


def make_shift(team, hours_from_now=24, length=4, capacity=1, title="Desk"):
    start = timezone.now() + timedelta(hours=hours_from_now)
    return Shift.objects.create(
        conference_year=YEAR, team=team, title=title,
        starts_at=start, ends_at=start + timedelta(hours=length), capacity=capacity,
    )


def accepted_application(user, team, name="Ada Lovelace"):
    application = VolunteerApplication.objects.create(
        user=user, conference_year=YEAR, full_name=name,
        motivation="I would like to help the community that helped me.",
        status=VolunteerApplication.STATUS_SUBMITTED,
    )
    application.teams.set([team])
    services.decide(
        application, VolunteerApplication.STATUS_ACCEPTED, team=team, notify=False
    )
    return application


def assert_accessible(test, html):
    found = errors(audit_html(html, internal_hosts=("testserver",)))
    test.assertEqual(found, [], "\n".join(str(f) for f in found))


class LandingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        build_site()

    def setUp(self):
        invalidate()

    def test_the_landing_page_needs_no_account(self):
        """Somebody deciding whether to volunteer should not have to sign up to read."""
        open_call()
        make_team("Registration", description="Badges and the front desk")
        response = self.client.get("/volunteers/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Volunteer at PyCon Nigeria")
        self.assertContains(response, "Badges and the front desk")
        self.assertContains(response, "Apply to volunteer")

    def test_a_closed_call_says_so_instead_of_offering_a_form(self):
        make_team()
        response = self.client.get("/volunteers/")
        self.assertContains(response, "Applications are not open")
        self.assertNotContains(response, "Apply to volunteer")

    def test_a_past_deadline_names_the_date(self):
        settings_obj = VolunteerSettings.for_year(YEAR)
        settings_obj.status = VolunteerSettings.STATUS_OPEN
        settings_obj.application_deadline = timezone.now() - timedelta(days=2)
        settings_obj.save()
        self.assertContains(self.client.get("/volunteers/"), "They closed on")

    def test_applying_while_closed_is_diverted(self):
        response = self.client.get("/volunteers/apply/")
        # Login first, then the closed page.
        self.assertEqual(response.status_code, 302)
        self.client.force_login(make_user("ada@example.com"))
        self.assertRedirects(self.client.get("/volunteers/apply/"), "/volunteers/closed/")

    def test_the_landing_page_has_no_accessibility_errors(self):
        open_call()
        make_team()
        assert_accessible(self, self.client.get("/volunteers/").content.decode())


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ApplyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        build_site()

    def setUp(self):
        invalidate()
        self.settings_obj = open_call()
        self.team = make_team()
        self.user = make_user("ada@example.com")
        self.client.force_login(self.user)

    def _payload(self, **overrides):
        first_day = self.settings_obj.availability_days()[0][0]
        data = {
            "full_name": "Ada Lovelace",
            "motivation": "I would like to help the community that helped me.",
            "experience": VolunteerApplication.EXPERIENCE_NONE,
            "teams": [self.team.pk],
            VolunteerApplicationForm.availability_field(first_day, "morning"): "on",
        }
        data.update(overrides)
        return data

    def test_the_form_shows_the_availability_grid_from_the_edition(self):
        response = self.client.get("/volunteers/apply/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "When you could work")
        self.assertContains(response, "Set-up")
        self.assertContains(response, "Pack-down")

    def test_no_edition_dates_says_so_rather_than_showing_nothing(self):
        Edition.objects.filter(year=YEAR).update(starts_on=None, ends_on=None)
        invalidate()
        response = self.client.get("/volunteers/apply/")
        self.assertContains(response, "dates are not set yet")

    def test_applying_lands_them_on_their_own_page(self):
        response = self.client.post("/volunteers/apply/", self._payload(), follow=True)
        self.assertContains(response, "your application is in")
        application = VolunteerApplication.objects.get()
        self.assertEqual(application.status, VolunteerApplication.STATUS_SUBMITTED)
        self.assertEqual(application.availability.count(), 1)

    def test_a_bad_application_comes_back_with_the_reason(self):
        response = self.client.post(
            "/volunteers/apply/", self._payload(motivation="no"), follow=True
        )
        self.assertContains(response, "a little more")
        self.assertFalse(VolunteerApplication.objects.exists())

    def test_the_same_page_edits_an_undecided_application(self):
        self.client.post("/volunteers/apply/", self._payload())
        response = self.client.get("/volunteers/apply/")
        self.assertContains(response, "Edit your application")

        self.client.post("/volunteers/apply/", self._payload(full_name="Ada L."))
        self.assertEqual(VolunteerApplication.objects.get().full_name, "Ada L.")
        self.assertEqual(VolunteerApplication.objects.count(), 1)

    def test_a_decided_application_can_no_longer_be_edited(self):
        self.client.post("/volunteers/apply/", self._payload())
        application = VolunteerApplication.objects.get()
        services.decide(
            application, VolunteerApplication.STATUS_ACCEPTED, team=self.team, notify=False
        )
        response = self.client.get("/volunteers/apply/", follow=True)
        self.assertContains(response, "can no longer be edited")

    def test_the_form_has_no_accessibility_errors(self):
        assert_accessible(self, self.client.get("/volunteers/apply/").content.decode())


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MyApplicationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        build_site()

    def setUp(self):
        invalidate()
        open_call()
        self.team = make_team("Registration", lead=make_user("lead@example.com"))
        self.user = make_user("ada@example.com")
        self.client.force_login(self.user)

    def test_no_application_sends_them_to_the_landing_page(self):
        self.assertRedirects(
            self.client.get("/volunteers/my-application/"), "/volunteers/"
        )

    def test_an_accepted_volunteer_sees_their_team_lead_and_ticket(self):
        accepted_application(self.user, self.team)
        response = self.client.get("/volunteers/my-application/")
        self.assertContains(response, "Accepted")
        self.assertContains(response, "Registration")
        self.assertContains(response, "lead@example.com")
        self.assertContains(response, "Your ticket")
        self.assertContains(response, "Open your ticket and QR code")

    def test_shifts_stay_hidden_until_the_roster_is_published(self):
        """A volunteer who reads a draft roster turns up at the wrong hour."""
        application = accepted_application(self.user, self.team)
        services.assign_shift(application, make_shift(self.team, title="Front desk"))

        response = self.client.get("/volunteers/my-application/")
        self.assertContains(response, "still being built")
        self.assertNotContains(response, "Front desk")

        services.publish_shifts(YEAR)
        response = self.client.get("/volunteers/my-application/")
        self.assertContains(response, "Front desk")

    def test_withdrawing_needs_a_post(self):
        accepted_application(self.user, self.team)
        self.assertEqual(self.client.get("/volunteers/withdraw/").status_code, 405)

    def test_withdrawing_works_and_says_so(self):
        accepted_application(self.user, self.team)
        response = self.client.post("/volunteers/withdraw/", follow=True)
        self.assertContains(response, "has been withdrawn")
        self.assertEqual(
            VolunteerApplication.objects.get().status,
            VolunteerApplication.STATUS_WITHDRAWN,
        )

    def test_the_page_has_no_accessibility_errors(self):
        application = accepted_application(self.user, self.team)
        services.assign_shift(application, make_shift(self.team))
        services.publish_shifts(YEAR)
        assert_accessible(
            self, self.client.get("/volunteers/my-application/").content.decode()
        )


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class CertificateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        build_site()

    def setUp(self):
        invalidate()
        open_call()
        self.team = make_team()
        self.user = make_user("ada@example.com")
        self.client.force_login(self.user)
        self.application = accepted_application(self.user, self.team)

    def test_a_certificate_needs_the_coordinator_to_open_them(self):
        response = self.client.get("/volunteers/certificate/", follow=True)
        self.assertContains(response, "not available yet")

    def test_a_certificate_needs_a_shift_actually_worked(self):
        """One for somebody who did not come devalues everyone else's."""
        open_call(certificates_available_from=timezone.now() - timedelta(days=1))
        services.assign_shift(self.application, make_shift(self.team))
        response = self.client.get("/volunteers/certificate/", follow=True)
        self.assertContains(response, "do not show a completed shift")

    def test_a_volunteer_who_worked_gets_one(self):
        open_call(certificates_available_from=timezone.now() - timedelta(days=1))
        assignment, _ = services.assign_shift(self.application, make_shift(self.team))
        services.mark_attended(assignment)

        response = self.client.get("/volunteers/certificate/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Certificate of Participation")
        self.assertContains(response, "Ada Lovelace")
        self.assertContains(response, "Landmark Centre, Lagos")

    def test_somebody_not_accepted_has_no_certificate(self):
        services.decide(
            self.application, VolunteerApplication.STATUS_NOT_SELECTED, notify=False
        )
        open_call(certificates_available_from=timezone.now() - timedelta(days=1))
        self.assertEqual(self.client.get("/volunteers/certificate/").status_code, 404)

    def test_the_certificate_has_no_accessibility_errors(self):
        open_call(certificates_available_from=timezone.now() - timedelta(days=1))
        assignment, _ = services.assign_shift(self.application, make_shift(self.team))
        services.mark_attended(assignment)
        assert_accessible(
            self, self.client.get("/volunteers/certificate/").content.decode()
        )


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class CoordinatorAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        build_site()

    def setUp(self):
        invalidate()
        open_call()
        self.team = make_team()
        self.applicant = make_user("ada@example.com")
        self.application = VolunteerApplication.objects.create(
            user=self.applicant, conference_year=YEAR, full_name="Ada Lovelace",
            motivation="I would like to help.",
            status=VolunteerApplication.STATUS_SUBMITTED,
        )

    def _urls(self):
        return [
            "/volunteers/coordinate/",
            "/volunteers/coordinate/shifts/",
            "/volunteers/coordinate/export/",
            f"/volunteers/coordinate/{self.application.pk}/",
        ]

    def test_an_applicant_cannot_reach_the_coordinator_pages(self):
        self.client.force_login(self.applicant)
        for url in self._urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_a_coordinator_can(self):
        coordinator = make_user("coord@example.com")
        RoleAssignment.objects.create(
            user=coordinator, role=Role.VOLUNTEER_CHAIR.value, conference_year=YEAR
        )
        self.client.force_login(coordinator)
        for url in self._urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_an_organizer_can_too(self):
        """On a small team somebody senior always ends up covering this."""
        organizer = make_user("org@example.com")
        RoleAssignment.objects.create(
            user=organizer, role=Role.ORGANIZER.value, conference_year=YEAR
        )
        self.client.force_login(organizer)
        self.assertEqual(self.client.get("/volunteers/coordinate/").status_code, 200)

    def test_a_plain_volunteer_cannot(self):
        """Holding the volunteer role is not the same as coordinating them."""
        volunteer = make_user("vol@example.com")
        RoleAssignment.objects.create(
            user=volunteer, role=Role.VOLUNTEER.value, conference_year=YEAR
        )
        self.client.force_login(volunteer)
        self.assertEqual(self.client.get("/volunteers/coordinate/").status_code, 403)

    def test_anonymous_visitors_are_sent_to_log_in(self):
        self.assertEqual(self.client.get("/volunteers/coordinate/").status_code, 302)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class CoordinatorWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        build_site()

    def setUp(self):
        invalidate()
        open_call(target_count=4)
        self.lead = make_user("lead@example.com")
        self.team = make_team("Registration", lead=self.lead, target_count=2)
        self.coordinator = make_user("coord@example.com")
        RoleAssignment.objects.create(
            user=self.coordinator, role=Role.VOLUNTEER_CHAIR.value, conference_year=YEAR
        )
        self.applicant = make_user("ada@example.com")
        self.application = VolunteerApplication.objects.create(
            user=self.applicant, conference_year=YEAR, full_name="Ada Lovelace",
            motivation="I would like to help the community that helped me.",
            accessibility_needs="I cannot stand for more than an hour at a time.",
            status=VolunteerApplication.STATUS_SUBMITTED,
        )
        self.application.teams.set([self.team])
        self.client.force_login(self.coordinator)

    def _accept(self):
        """Accept the application setUp already made, rather than making another."""
        services.decide(
            self.application,
            VolunteerApplication.STATUS_ACCEPTED,
            team=self.team,
            notify=False,
        )
        return self.application

    def test_the_dashboard_shows_what_is_waiting_and_what_is_short(self):
        make_shift(self.team, capacity=3, title="Front desk")
        response = self.client.get("/volunteers/coordinate/")
        self.assertContains(response, "awaiting a decision")
        self.assertContains(response, "still needed")
        self.assertContains(response, "shifts short")
        self.assertContains(response, "Front desk")

    def test_accessibility_needs_are_shown_before_a_decision(self):
        """Read before assigning a shift, not after."""
        response = self.client.get(f"/volunteers/coordinate/{self.application.pk}/")
        self.assertContains(response, "cannot stand for more than an hour")
        self.assertContains(response, "work comfortably")

    def test_accepting_from_the_page_does_the_whole_thing(self):
        from tickets.models import Ticket

        response = self.client.post(
            f"/volunteers/coordinate/{self.application.pk}/",
            {
                "decision": VolunteerApplication.STATUS_ACCEPTED,
                "team": self.team.pk,
                "note": "Delighted to have you.",
            },
            follow=True,
        )
        self.assertContains(response, "role and free ticket have been issued")
        self.application.refresh_from_db()
        self.assertTrue(self.application.is_accepted)
        self.assertEqual(self.application.assigned_lead, self.lead)
        self.assertTrue(Ticket.objects.filter(user=self.applicant).exists())

    def test_accepting_without_a_team_is_refused_on_the_page(self):
        response = self.client.post(
            f"/volunteers/coordinate/{self.application.pk}/",
            {"decision": VolunteerApplication.STATUS_ACCEPTED},
        )
        self.assertContains(response, "Pick a team")
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, VolunteerApplication.STATUS_SUBMITTED)

    def test_an_overlapping_assignment_is_refused_with_the_clash_named(self):
        accepted = self._accept()
        first = make_shift(self.team, hours_from_now=24, title="Morning desk")
        clashing = make_shift(self.team, hours_from_now=26, title="Overlapping desk")
        services.assign_shift(accepted, first)

        response = self.client.post(
            f"/volunteers/coordinate/{accepted.pk}/",
            {"action": "assign_shift", "shift": clashing.pk},
            follow=True,
        )
        self.assertContains(response, "overlaps")
        self.assertContains(response, "Morning desk")

    def test_the_override_lets_it_through(self):
        accepted = self._accept()
        first = make_shift(self.team, hours_from_now=24, title="Morning desk")
        clashing = make_shift(self.team, hours_from_now=26, title="Overlapping desk")
        services.assign_shift(accepted, first)

        self.client.post(
            f"/volunteers/coordinate/{accepted.pk}/",
            {"action": "assign_shift", "shift": clashing.pk, "allow_overlap": "1"},
            follow=True,
        )
        self.assertEqual(accepted.shift_assignments.count(), 2)

    def test_attendance_can_be_toggled_from_the_page(self):
        accepted = self._accept()
        assignment, _ = services.assign_shift(accepted, make_shift(self.team))
        self.client.post(
            f"/volunteers/coordinate/{accepted.pk}/",
            {"action": "attendance", "assignment": assignment.pk, "attended": "1"},
        )
        assignment.refresh_from_db()
        self.assertTrue(assignment.attended)

    def test_a_shift_can_be_added_and_removed(self):
        start = timezone.now() + timedelta(days=2)
        response = self.client.post(
            "/volunteers/coordinate/shifts/",
            {
                "team": self.team.pk,
                "title": "Saturday morning desk",
                "starts_at": start.strftime("%Y-%m-%dT%H:%M"),
                "ends_at": (start + timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M"),
                "location": "Main entrance",
                "capacity": 2,
                "notes": "",
            },
            follow=True,
        )
        self.assertContains(response, "Added Saturday morning desk")
        shift = Shift.objects.get()

        response = self.client.post(
            "/volunteers/coordinate/shifts/",
            {"action": "delete", "shift": shift.pk},
            follow=True,
        )
        self.assertContains(response, "Removed Saturday morning desk")
        self.assertFalse(Shift.objects.exists())

    def test_publishing_the_roster_reports_how_many_were_told(self):
        accepted = self._accept()
        services.assign_shift(accepted, make_shift(self.team))
        response = self.client.post(
            "/volunteers/coordinate/shifts/", {"action": "publish"}, follow=True
        )
        self.assertContains(response, "1 volunteer has been told")

    def test_publishing_with_nobody_rostered_says_that_instead(self):
        response = self.client.post(
            "/volunteers/coordinate/shifts/", {"action": "publish"}, follow=True
        )
        self.assertContains(response, "Nobody has a shift yet")

    def test_the_shift_page_puts_available_volunteers_first(self):
        accepted = self._accept()
        shift = make_shift(self.team, capacity=2)
        from volunteers.models import VolunteerAvailability

        VolunteerAvailability.objects.create(
            application=accepted, day=shift.starts_at.date(), period="morning"
        )
        response = self.client.get(f"/volunteers/coordinate/shifts/{shift.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ada Lovelace")
        self.assertContains(response, "Said they were free")
        self.assertTrue(response.context["candidates"][0]["said_available"])

    def test_the_export_carries_what_the_day_needs_and_not_the_motivation(self):
        """A spreadsheet passed around a venue is the wrong place for it."""
        accepted = self._accept()
        accepted.tshirt_size = "l"
        accepted.dietary_requirements = "Vegetarian"
        accepted.emergency_contact_name = "Grace Hopper"
        accepted.save()

        response = self.client.get("/volunteers/coordinate/export/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        body = response.content.decode()
        self.assertIn("Ada Lovelace", body)
        self.assertIn("Vegetarian", body)
        self.assertIn("Grace Hopper", body)
        self.assertIn("Registration", body)
        self.assertNotIn("community that helped me", body)

    def test_filtering_the_queue_by_status(self):
        accepted_application(make_user("grace@example.com"), self.team, name="Grace Hopper")
        response = self.client.get("/volunteers/coordinate/?status=accepted")
        self.assertContains(response, "Grace Hopper")
        self.assertNotContains(response, "Ada Lovelace")

    def test_the_coordinator_pages_have_no_accessibility_errors(self):
        accepted = self._accept()
        shift = make_shift(self.team, capacity=2)
        services.assign_shift(accepted, shift)
        for url in (
            "/volunteers/coordinate/",
            "/volunteers/coordinate/shifts/",
            f"/volunteers/coordinate/shifts/{shift.pk}/",
            f"/volunteers/coordinate/{accepted.pk}/",
        ):
            with self.subTest(url=url):
                assert_accessible(self, self.client.get(url).content.decode())


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class DashboardIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        build_site()

    def setUp(self):
        invalidate()
        open_call()
        self.team = make_team()
        self.user = make_user("ada@example.com")
        self.client.force_login(self.user)

    def test_the_dashboard_offers_volunteering_while_the_call_is_open(self):
        response = self.client.get("/dashboard/")
        self.assertContains(response, "Apply to volunteer")

    def test_the_dashboard_shows_the_status_once_they_have_applied(self):
        accepted_application(self.user, self.team)
        response = self.client.get("/dashboard/")
        self.assertContains(response, "Accepted")
        self.assertContains(response, "Registration")

    def test_the_dashboard_shows_the_next_shift_once_published(self):
        application = accepted_application(self.user, self.team)
        services.assign_shift(application, make_shift(self.team, hours_from_now=48))
        services.assign_shift(application, make_shift(self.team, hours_from_now=96))
        services.publish_shifts(YEAR)

        response = self.client.get("/dashboard/")
        self.assertContains(response, "Next shift")
        self.assertContains(response, "and 1 more")

    def test_a_closed_call_offers_the_explanation_instead(self):
        VolunteerSettings.objects.filter(conference_year=YEAR).update(
            status=VolunteerSettings.STATUS_CLOSED
        )
        response = self.client.get("/dashboard/")
        self.assertContains(response, "not open at the moment")

    def test_the_coordinator_section_appears_only_for_a_coordinator(self):
        response = self.client.get("/dashboard/")
        self.assertNotContains(response, "Volunteer Coordinator")

        RoleAssignment.objects.create(
            user=self.user, role=Role.VOLUNTEER_CHAIR.value, conference_year=YEAR
        )
        response = self.client.get("/dashboard/")
        self.assertContains(response, "Volunteer Coordinator")
