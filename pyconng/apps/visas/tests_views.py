"""
View-level tests for visa letters.

Three questions a service test cannot answer here. Who can see a passport number.
Whether the public verification endpoint can be turned into a way of finding out who
has been issued a letter. And whether these pages are usable, which matters more
than usual: the audience includes people filling in a passport number on a phone,
and an embassy officer who has never seen this site before and has one thing to
check.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from wagtail.models import Page, Site

from accounts.models import RoleAssignment
from accounts.roles import Role
from audit.models import AuditEntry
from editions.current import invalidate
from quality.audit import audit_html, errors
from visas import services
from visas.models import VisaLetterRequest, VisaSettings
from visas.tests import (
    YEAR, give_paid_ticket, make_edition, make_request, make_user, open_visas,
)

User = get_user_model()


def build_site():
    from home.models import HomePage

    make_edition()
    root = Page.objects.get(depth=1)
    home = HomePage(title=f"PyCon Nigeria {YEAR}", slug="home-2026")
    root.add_child(instance=home)
    home.save_revision().publish()
    site = Site.objects.get(is_default_site=True)
    site.root_page = home
    site.save()
    return home


def assert_accessible(test, html):
    found = errors(audit_html(html, internal_hosts=("testserver",)))
    test.assertEqual(found, [], "\n".join(str(f) for f in found))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ApplicantViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        build_site()

    def setUp(self):
        invalidate()
        open_visas()
        self.user = make_user()
        give_paid_ticket(self.user)
        self.client.force_login(self.user)

    def _form_data(self, **overrides):
        data = {
            "passport_surname": "Lovelace",
            "passport_given_names": "Ada Augusta",
            "nationality": "Kenyan",
            "passport_number": "a012 34567",
            "passport_expiry": (timezone.localdate() + timedelta(days=900)).isoformat(),
            "date_of_birth": (timezone.localdate() - timedelta(days=11000)).isoformat(),
            "embassy_city": "Nairobi",
            "embassy_country": "Kenya",
            "arrival_on": (timezone.localdate() + timedelta(days=88)).isoformat(),
            "departure_on": (timezone.localdate() + timedelta(days=95)).isoformat(),
            "accommodation": "",
            "notes": "",
            "confirm_matches_passport": "on",
        }
        data.update(overrides)
        return data

    def test_the_landing_page_leads_with_the_deadline(self):
        response = self.client.get("/visa/")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Request yours by", body)
        assert_accessible(self, body)

    def test_the_landing_page_works_signed_out(self):
        self.client.logout()
        response = self.client.get("/visa/")
        self.assertEqual(response.status_code, 200)
        assert_accessible(self, response.content.decode())

    def test_the_request_form(self):
        response = self.client.get("/visa/request/")
        self.assertEqual(response.status_code, 200)
        assert_accessible(self, response.content.decode())

    def test_submitting_a_request(self):
        response = self.client.post("/visa/request/", self._form_data(), follow=True)
        self.assertEqual(response.status_code, 200)
        obj = VisaLetterRequest.objects.get(user=self.user)
        self.assertEqual(obj.status, VisaLetterRequest.STATUS_SUBMITTED)
        self.assertEqual(obj.basis, VisaLetterRequest.BASIS_ATTENDEE)

    def test_the_passport_number_is_normalised(self):
        """Printed in groups, typed inconsistently, matched character by character."""
        self.client.post("/visa/request/", self._form_data())
        self.assertEqual(
            VisaLetterRequest.objects.get(user=self.user).passport_number, "A01234567"
        )

    def test_the_confirmation_box_is_required(self):
        data = self._form_data()
        del data["confirm_matches_passport"]
        self.client.post("/visa/request/", data)
        self.assertFalse(VisaLetterRequest.objects.filter(user=self.user).exists())

    def test_a_passport_expiring_too_soon_is_caught_here_not_at_the_embassy(self):
        response = self.client.post(
            "/visa/request/",
            self._form_data(
                passport_expiry=(timezone.localdate() + timedelta(days=100)).isoformat()
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "months&#x27; validity")
        self.assertFalse(VisaLetterRequest.objects.filter(user=self.user).exists())

    def test_an_already_expired_passport_is_refused(self):
        response = self.client.post(
            "/visa/request/",
            self._form_data(
                passport_expiry=(timezone.localdate() - timedelta(days=1)).isoformat()
            ),
        )
        self.assertContains(response, "already expired")

    def test_departure_before_arrival_is_refused(self):
        response = self.client.post(
            "/visa/request/",
            self._form_data(
                departure_on=(timezone.localdate() + timedelta(days=80)).isoformat()
            ),
        )
        self.assertContains(response, "cannot be before arrival")

    def test_somebody_with_no_standing_is_sent_away_with_a_reason(self):
        stranger = make_user("stranger@example.com")
        self.client.force_login(stranger)
        response = self.client.get("/visa/request/", follow=True)
        self.assertContains(response, "ticket holders")

    def test_the_my_request_page(self):
        self.client.post("/visa/request/", self._form_data())
        response = self.client.get("/visa/my-request/")
        self.assertEqual(response.status_code, 200)
        assert_accessible(self, response.content.decode())

    def test_the_applicant_sees_only_a_masked_number_on_their_own_page(self):
        """
        Their own passport number, but a shoulder on a bus is still a shoulder, and
        they have the passport if they need to check it.
        """
        self.client.post("/visa/request/", self._form_data())
        body = self.client.get("/visa/my-request/").content.decode()
        self.assertNotIn("A01234567", body)
        self.assertIn("A", body)

    def test_a_request_can_be_corrected_until_it_is_decided(self):
        self.client.post("/visa/request/", self._form_data())
        self.client.post("/visa/request/", self._form_data(passport_given_names="Ada"))
        obj = VisaLetterRequest.objects.get(user=self.user)
        self.assertEqual(obj.passport_given_names, "Ada")

    def test_a_decided_request_cannot_be_edited(self):
        self.client.post("/visa/request/", self._form_data())
        obj = VisaLetterRequest.objects.get(user=self.user)
        services.approve(obj, by=make_user("officer@example.com"), notify=False)
        response = self.client.get("/visa/request/", follow=True)
        self.assertContains(response, "no longer be edited")

    def test_withdrawing(self):
        self.client.post("/visa/request/", self._form_data())
        obj = VisaLetterRequest.objects.get(user=self.user)
        response = self.client.post(f"/visa/{obj.pk}/withdraw/", follow=True)
        self.assertEqual(response.status_code, 200)
        obj.refresh_from_db()
        self.assertEqual(obj.status, VisaLetterRequest.STATUS_WITHDRAWN)

    def test_withdrawing_is_post_only(self):
        obj = make_request(self.user)
        self.assertEqual(
            self.client.get(f"/visa/{obj.pk}/withdraw/").status_code, 405
        )

    def test_nobody_withdraws_somebody_elses_request(self):
        other = make_user("other@example.com")
        give_paid_ticket(other)
        obj = make_request(other)
        self.assertEqual(
            self.client.post(f"/visa/{obj.pk}/withdraw/").status_code, 404
        )


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class LetterDownloadTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        build_site()

    def setUp(self):
        invalidate()
        open_visas()
        self.user = make_user()
        give_paid_ticket(self.user)
        self.officer = make_user("officer@example.com")
        RoleAssignment.objects.create(
            user=self.officer, role=Role.VISA_OFFICER.value, conference_year=YEAR
        )
        self.request_obj = make_request(self.user)
        services.approve(self.request_obj, by=self.officer, notify=False)

    def test_the_applicant_downloads_their_letter(self):
        self.client.force_login(self.user)
        response = self.client.get(f"/visa/{self.request_obj.pk}/letter.pdf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF-"))
        self.assertIn(self.request_obj.reference, response["Content-Disposition"])

    def test_the_letter_is_not_cached(self):
        """It carries a passport number, and a shared machine's cache is not the place."""
        self.client.force_login(self.user)
        response = self.client.get(f"/visa/{self.request_obj.pk}/letter.pdf")
        self.assertIn("no-store", response["Cache-Control"])

    def test_the_officer_can_download_it_too(self):
        self.client.force_login(self.officer)
        self.assertEqual(
            self.client.get(f"/visa/{self.request_obj.pk}/letter.pdf").status_code, 200
        )

    def test_nobody_else_can(self):
        self.client.force_login(make_user("nosy@example.com"))
        self.assertEqual(
            self.client.get(f"/visa/{self.request_obj.pk}/letter.pdf").status_code, 404
        )

    def test_an_anonymous_visitor_is_sent_to_sign_in(self):
        response = self.client.get(f"/visa/{self.request_obj.pk}/letter.pdf")
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_downloads_are_counted_and_recorded(self):
        self.client.force_login(self.user)
        self.client.get(f"/visa/{self.request_obj.pk}/letter.pdf")
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.letter_downloads, 1)
        self.assertTrue(
            AuditEntry.objects.filter(
                action="Downloaded visa invitation letter"
            ).exists()
        )

    def test_there_is_nothing_to_download_before_approval(self):
        other = make_user("pending@example.com")
        give_paid_ticket(other)
        pending = make_request(other)
        self.client.force_login(other)
        response = self.client.get(f"/visa/{pending.pk}/letter.pdf", follow=True)
        self.assertContains(response, "no letter to download")


class OfficerViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        build_site()

    def setUp(self):
        invalidate()
        open_visas()
        self.officer = make_user("officer@example.com")
        RoleAssignment.objects.create(
            user=self.officer, role=Role.VISA_OFFICER.value, conference_year=YEAR
        )
        self.applicant = make_user()
        give_paid_ticket(self.applicant)
        self.request_obj = make_request(self.applicant)
        self.client.force_login(self.officer)

    def test_the_queue(self):
        response = self.client.get("/visa/queue/")
        self.assertEqual(response.status_code, 200)
        assert_accessible(self, response.content.decode())

    def test_the_queue_never_shows_a_whole_passport_number(self):
        body = self.client.get("/visa/queue/").content.decode()
        self.assertNotIn("A01234567", body)

    def test_the_queue_filters_by_status(self):
        services.approve(self.request_obj, by=self.officer, notify=False)
        response = self.client.get("/visa/queue/", {"status": "submitted"})
        self.assertEqual(len(response.context["requests"]), 0)
        response = self.client.get("/visa/queue/", {"status": "approved"})
        self.assertEqual(len(response.context["requests"]), 1)

    def test_a_nonsense_filter_shows_everything_rather_than_erroring(self):
        response = self.client.get("/visa/queue/", {"status": "banana"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_status"], "")

    def test_the_detail_page(self):
        response = self.client.get(f"/visa/queue/{self.request_obj.pk}/")
        self.assertEqual(response.status_code, 200)
        assert_accessible(self, response.content.decode())

    def test_the_detail_page_shows_the_masked_number_by_default(self):
        body = self.client.get(f"/visa/queue/{self.request_obj.pk}/").content.decode()
        self.assertNotIn("A01234567", body)
        self.assertIn("Show the full number", body)

    def test_revealing_the_number_returns_it_and_records_the_read(self):
        response = self.client.post(f"/visa/queue/{self.request_obj.pk}/passport/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["passport_number"], "A01234567")
        self.assertTrue(
            AuditEntry.objects.filter(action="Viewed full passport number").exists()
        )

    def test_revealing_is_post_only(self):
        """A GET is followed by prefetchers and crawlers; a read must be deliberate."""
        self.assertEqual(
            self.client.get(f"/visa/queue/{self.request_obj.pk}/passport/").status_code,
            405,
        )

    def test_revealing_purged_details_says_they_are_gone(self):
        self.request_obj.purge_details()
        response = self.client.post(f"/visa/queue/{self.request_obj.pk}/passport/")
        self.assertEqual(response.status_code, 410)

    def test_approving_from_the_page(self):
        response = self.client.post(
            f"/visa/queue/{self.request_obj.pk}/",
            {"decision": "approve", "note": ""},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.request_obj.refresh_from_db()
        self.assertTrue(self.request_obj.is_approved)

    def test_rejecting_without_a_reason_is_refused_by_the_form(self):
        response = self.client.post(
            f"/visa/queue/{self.request_obj.pk}/", {"decision": "reject", "note": ""}
        )
        self.assertContains(response, "Say why")
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status, VisaLetterRequest.STATUS_SUBMITTED)

    def test_the_page_warns_when_the_applicant_has_stopped_being_eligible(self):
        from tickets.models import Ticket

        Ticket.objects.filter(user=self.applicant).update(status=Ticket.REFUNDED)
        response = self.client.get(f"/visa/queue/{self.request_obj.pk}/")
        self.assertContains(response, "no longer eligible")

    def test_the_page_warns_when_their_standing_has_changed(self):
        from visas.tests import make_speaker

        make_speaker(self.applicant)
        response = self.client.get(f"/visa/queue/{self.request_obj.pk}/")
        self.assertContains(response, "standing has changed")

    def test_the_queue_warns_when_the_deadline_leaves_no_time(self):
        open_visas(
            embassy_lead_days=90,
            request_deadline=timezone.now() + timedelta(days=80),
        )
        from editions.models import Edition

        Edition.objects.filter(year=YEAR).update(
            starts_on=timezone.localdate() + timedelta(days=60),
            ends_on=timezone.localdate() + timedelta(days=62),
        )
        invalidate()
        response = self.client.get("/visa/queue/")
        self.assertContains(response, "later than the lead time allows")

    def test_purging_from_the_queue(self):
        response = self.client.post("/visa/queue/purge/", follow=True)
        self.assertContains(response, "Passport details deleted")
        self.request_obj.refresh_from_db()
        self.assertFalse(self.request_obj.details_retained)

    def test_purging_is_post_only(self):
        self.assertEqual(self.client.get("/visa/queue/purge/").status_code, 405)


class PermissionTests(TestCase):
    """Passport data is the most sensitive thing on the site. Who reaches it matters."""

    @classmethod
    def setUpTestData(cls):
        build_site()

    def setUp(self):
        invalidate()
        open_visas()
        self.applicant = make_user()
        give_paid_ticket(self.applicant)
        self.request_obj = make_request(self.applicant)

    def _officer_urls(self):
        return [
            "/visa/queue/",
            f"/visa/queue/{self.request_obj.pk}/",
        ]

    def test_an_applicant_cannot_reach_the_queue(self):
        self.client.force_login(self.applicant)
        for url in self._officer_urls():
            with self.subTest(url=url):
                self.assertNotEqual(self.client.get(url).status_code, 200)

    def test_an_organizer_cannot_read_passport_data(self):
        """
        Different from the volunteer pages on purpose: coordinating a conference is
        organisational work; reading passport numbers is not, and the role for it is
        given to named people.
        """
        organizer = make_user("organizer@example.com")
        RoleAssignment.objects.create(
            user=organizer, role=Role.ORGANIZER.value, conference_year=YEAR
        )
        self.client.force_login(organizer)
        for url in self._officer_urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(
            self.client.post(f"/visa/queue/{self.request_obj.pk}/passport/").status_code,
            403,
        )

    def test_a_superuser_is_not_waved_through(self):
        root = make_user("root@example.com", is_superuser=True, is_staff=True)
        self.client.force_login(root)
        self.assertEqual(self.client.get("/visa/queue/").status_code, 403)

    def test_an_anonymous_visitor_is_sent_to_sign_in(self):
        response = self.client.get("/visa/queue/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])


class VerificationTests(TestCase):
    """The only page here without a login, which is the point and also the risk."""

    @classmethod
    def setUpTestData(cls):
        build_site()

    def setUp(self):
        invalidate()
        open_visas()
        self.applicant = make_user()
        give_paid_ticket(self.applicant)
        self.request_obj = make_request(self.applicant)
        services.approve(
            self.request_obj, by=make_user("officer@example.com"), notify=False
        )

    def _check(self, reference=None, surname="Lovelace"):
        return self.client.post(
            "/visa/verify/",
            {"reference": reference or self.request_obj.reference, "surname": surname},
        )

    def test_the_page_is_public(self):
        response = self.client.get("/visa/verify/")
        self.assertEqual(response.status_code, 200)
        assert_accessible(self, response.content.decode())

    def test_a_genuine_letter_is_confirmed(self):
        response = self._check()
        self.assertContains(response, "genuine and valid")
        self.assertContains(response, self.request_obj.reference)
        assert_accessible(self, response.content.decode())

    def test_the_confirmation_never_shows_the_passport_number(self):
        """
        The officer reading it is holding the passport. Publishing the number to
        anyone who can name a reference and a surname would be indefensible.
        """
        body = self._check().content.decode()
        self.assertNotIn("A01234567", body)

    def test_the_reference_alone_is_not_enough(self):
        """Otherwise the endpoint is a way of finding out who was issued a letter."""
        response = self._check(surname="Wrong")
        self.assertContains(response, "No letter matches")

    def test_the_reference_is_case_and_space_insensitive(self):
        response = self._check(
            reference=f"  {self.request_obj.reference.lower()}  ", surname="lovelace"
        )
        self.assertContains(response, "genuine and valid")

    def test_an_unknown_reference_reports_no_match(self):
        response = self._check(reference="PYNG-2026-ZZZZZZ")
        self.assertContains(response, "No letter matches")

    def test_a_withdrawn_letter_reports_as_no_longer_valid(self):
        """
        Materially different information to an embassy than "never existed", and the
        reason the reference is kept when a letter is withdrawn.
        """
        services.reject(
            self.request_obj,
            by=make_user("officer2@example.com"),
            note="Issued in error.",
            notify=False,
        )
        response = self._check()
        self.assertContains(response, "no longer valid")

    def test_a_letter_still_verifies_after_the_passport_details_are_deleted(self):
        """An embassy may check months later, long after the data is gone."""
        self.request_obj.purge_details()
        response = self._check(surname="Lovelace")
        self.assertContains(response, "genuine and valid")

    def test_verification_can_be_switched_off_for_an_edition(self):
        settings_obj = VisaSettings.for_year(YEAR)
        settings_obj.verification_enabled = False
        settings_obj.save()
        response = self.client.get("/visa/verify/")
        self.assertContains(response, "not available for this edition")
        self.assertNotContains(response, "Check this letter")
