"""
View-level tests for travel grants.

Three things the service tests cannot cover: who is allowed to see what, whether an
applicant can actually answer an offer from the page in front of them, and whether
those pages are accessible. The last matters more here than almost anywhere else on
the site -- a grant applicant is, by definition, someone for whom the conference is
only possible with help, and the form is long.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from wagtail.models import Page, Site

from accounts.models import RoleAssignment
from accounts.roles import Role
from editions.current import invalidate
from editions.models import Edition
from grants import budget, offers, payouts
from grants.models import (
    GrantReviewerAssignment,
    GrantSettings,
    TravelGrantApplication,
    TravelGrantPayment,
)
from quality.audit import audit_html, errors

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


def open_grants(**kwargs):
    defaults = {
        "status": GrantSettings.STATUS_OPEN,
        "application_deadline": timezone.now() + timedelta(days=14),
        "max_grant_budget": Decimal("1000000"),
        "max_per_applicant": Decimal("0"),
        "acceptance_days": 14,
    }
    defaults.update(kwargs)
    obj, _ = GrantSettings.objects.update_or_create(
        conference_year=YEAR, defaults=defaults
    )
    return obj


def make_user(email, **kwargs):
    return User.objects.create_user(
        username=email.split("@")[0], email=email, password="secret", **kwargs
    )


def make_application(user, status=TravelGrantApplication.STATUS_UNDER_REVIEW, **kwargs):
    defaults = {
        "conference_year": YEAR,
        "status": status,
        "country_of_residence": "Nigeria",
        "city": "Lagos",
        "financial_need_reason": "I cannot cover the flight from Kano.",
        "employment_status": "student",
        "community_impact": "I run a local Python meetup.",
        "estimated_transport_cost": Decimal("80000"),
        "estimated_accommodation_cost": Decimal("70000"),
        "submitted_at": timezone.now(),
    }
    defaults.update(kwargs)
    return TravelGrantApplication.objects.create(user=user, **defaults)


def assert_accessible(test, html):
    found = errors(audit_html(html, internal_hosts=("testserver",)))
    test.assertEqual(found, [], "\n".join(str(f) for f in found))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class OfferResponseViewTests(TestCase):
    """The applicant answering their offer, which is the new path through the module."""

    @classmethod
    def setUpTestData(cls):
        build_site()

    def setUp(self):
        invalidate()
        open_grants()
        self.user = make_user("ada@example.com")
        self.application = make_application(self.user)
        offers.make_offer(self.application, Decimal("150000"), notify=False)
        self.client.force_login(self.user)

    def test_the_page_shows_the_offer_and_its_deadline(self):
        response = self.client.get("/grants/my-application/")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("150,000", body)
        self.assertIn("Accept", body)
        assert_accessible(self, body)

    def test_accepting_from_the_page_works(self):
        from tickets.models import Ticket

        response = self.client.post(
            f"/grants/{self.application.pk}/accept/", follow=True
        )
        self.assertEqual(response.status_code, 200)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, TravelGrantApplication.STATUS_ACCEPTED)
        self.assertTrue(Ticket.objects.filter(user=self.user).exists())

    def test_accepting_is_post_only(self):
        """A confirmation reachable by a crawler or a prefetch is not a confirmation."""
        response = self.client.get(f"/grants/{self.application.pk}/accept/")
        self.assertEqual(response.status_code, 405)

    def test_accepting_an_expired_offer_says_so_instead_of_failing(self):
        self.application.acceptance_deadline = timezone.now() - timedelta(minutes=1)
        self.application.save()
        response = self.client.post(
            f"/grants/{self.application.pk}/accept/", follow=True
        )
        self.assertContains(response, "deadline")
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, TravelGrantApplication.STATUS_APPROVED)

    def test_the_decline_page_asks_for_a_reason(self):
        response = self.client.get(f"/grants/{self.application.pk}/decline/")
        self.assertEqual(response.status_code, 200)
        assert_accessible(self, response.content.decode())

    def test_declining_releases_the_money(self):
        response = self.client.post(
            f"/grants/{self.application.pk}/decline/",
            {"reason": "My visa was refused."},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, TravelGrantApplication.STATUS_DECLINED)
        self.assertEqual(budget.status(YEAR)["remaining"], Decimal("1000000"))

    def test_declining_says_whether_somebody_else_got_it(self):
        open_grants(max_grant_budget=Decimal("150000"))
        make_application(
            make_user("next@example.com"),
            status=TravelGrantApplication.STATUS_WAITLISTED,
            estimated_transport_cost=Decimal("100000"),
            estimated_accommodation_cost=Decimal("0"),
        )
        response = self.client.post(
            f"/grants/{self.application.pk}/decline/", {"reason": ""}, follow=True
        )
        self.assertContains(response, "waiting list")

    def test_nobody_can_answer_somebody_elses_offer(self):
        intruder = make_user("intruder@example.com")
        self.client.force_login(intruder)
        for path in ("accept", "decline"):
            with self.subTest(path=path):
                response = self.client.post(f"/grants/{self.application.pk}/{path}/")
                self.assertEqual(response.status_code, 404)

    def test_an_anonymous_visitor_is_sent_to_sign_in(self):
        self.client.logout()
        response = self.client.post(f"/grants/{self.application.pk}/accept/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class PayoutViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        build_site()

    def setUp(self):
        invalidate()
        open_grants()
        self.user = make_user("ada@example.com")
        self.application = make_application(self.user)
        offers.make_offer(self.application, Decimal("150000"), notify=False)
        offers.accept(self.application)
        self.client.force_login(self.user)

    def _details(self, **overrides):
        data = {
            "payout_method": TravelGrantApplication.PAYOUT_BANK_TRANSFER,
            "bank_name": "Example Bank",
            "account_name": "Ada Lovelace",
            "account_number": "0123456789",
            "payout_notes": "",
        }
        data.update(overrides)
        return data

    def test_the_page_asks_for_payout_details_once_a_grant_is_accepted(self):
        response = self.client.get("/grants/my-application/")
        self.assertTrue(response.context["needs_payout_details"])
        assert_accessible(self, response.content.decode())

    def test_submitting_payout_details(self):
        response = self.client.post(
            f"/grants/{self.application.pk}/payout/", self._details(), follow=True
        )
        self.assertEqual(response.status_code, 200)
        self.application.refresh_from_db()
        self.assertTrue(self.application.has_payout_details)

    def test_bank_details_require_an_account_number(self):
        response = self.client.post(
            f"/grants/{self.application.pk}/payout/",
            self._details(account_number=""),
            follow=True,
        )
        self.application.refresh_from_db()
        self.assertFalse(self.application.has_payout_details)
        self.assertEqual(response.status_code, 200)

    def test_the_page_stops_asking_once_details_are_in(self):
        self.client.post(f"/grants/{self.application.pk}/payout/", self._details())
        response = self.client.get("/grants/my-application/")
        self.assertFalse(response.context["needs_payout_details"])

    def test_a_receipt_can_be_uploaded_once_paid(self):
        payouts.mark_paid(self.application)
        response = self.client.post(
            f"/grants/{self.application.pk}/receipt/",
            {"receipt": SimpleUploadedFile(
                "receipt.pdf", b"%PDF-1.4 fake", content_type="application/pdf"
            )},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(TravelGrantPayment.objects.get(
            application=self.application
        ).receipt)

    def test_an_executable_is_not_accepted_as_a_receipt(self):
        payouts.mark_paid(self.application)
        response = self.client.post(
            f"/grants/{self.application.pk}/receipt/",
            {"receipt": SimpleUploadedFile(
                "receipt.exe", b"MZ\x90\x00", content_type="application/octet-stream"
            )},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(TravelGrantPayment.objects.get(
            application=self.application
        ).receipt)

    def test_somebody_else_cannot_post_a_receipt_against_your_grant(self):
        payouts.mark_paid(self.application)
        self.client.force_login(make_user("intruder@example.com"))
        response = self.client.post(
            f"/grants/{self.application.pk}/receipt/",
            {"receipt": SimpleUploadedFile("r.pdf", b"%PDF", content_type="application/pdf")},
        )
        self.assertEqual(response.status_code, 404)

    def test_payout_details_are_refused_before_an_award(self):
        other = make_user("hopeful@example.com")
        pending = make_application(other)
        self.client.force_login(other)
        response = self.client.post(
            f"/grants/{pending.pk}/payout/", self._details(), follow=True
        )
        self.assertContains(response, "nothing to pay out")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ChairViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        build_site()

    def setUp(self):
        invalidate()
        open_grants(max_grant_budget=Decimal("200000"))
        self.chair = make_user("chair@example.com")
        RoleAssignment.objects.create(
            user=self.chair, role=Role.GRANT_CHAIR.value, conference_year=YEAR
        )
        self.client.force_login(self.chair)
        self.application = make_application(make_user("ada@example.com"))

    def test_the_dashboard_shows_the_budget(self):
        offers.make_offer(self.application, Decimal("150000"), notify=False)
        response = self.client.get("/grants/admin/")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("150,000", body)
        self.assertIn("50,000", body)
        assert_accessible(self, body)

    def test_a_bulk_approval_past_the_budget_is_reported_not_silent(self):
        second = make_application(make_user("ben@example.com"))
        response = self.client.post(
            "/grants/admin/decisions/",
            {
                "application_ids": f"{self.application.pk},{second.pk}",
                "decision": "approve",
                f"amount_{self.application.pk}": "150000",
                f"amount_{second.pk}": "150000",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # Named, so a chair deciding twenty knows which ones did not fit.
        self.assertIn("ben@example.com", body)
        self.assertIn("past the", body)
        self.assertEqual(
            TravelGrantApplication.objects.filter(
                status=TravelGrantApplication.STATUS_APPROVED
            ).count(),
            1,
        )

    def test_promoting_from_the_waiting_list(self):
        waiting = make_application(
            make_user("waiting@example.com"),
            status=TravelGrantApplication.STATUS_WAITLISTED,
            estimated_transport_cost=Decimal("100000"),
            estimated_accommodation_cost=Decimal("0"),
        )
        response = self.client.post("/grants/admin/promote/", follow=True)
        self.assertContains(response, "offer(s) made from the waiting list")
        waiting.refresh_from_db()
        self.assertEqual(waiting.status, TravelGrantApplication.STATUS_APPROVED)

    def test_promoting_with_nothing_to_promote_says_why(self):
        response = self.client.post("/grants/admin/promote/", follow=True)
        self.assertContains(response, "Nothing to promote")

    def test_expiring_overdue_offers(self):
        offers.make_offer(self.application, Decimal("150000"), notify=False)
        self.application.acceptance_deadline = timezone.now() - timedelta(minutes=1)
        self.application.save()
        response = self.client.post("/grants/admin/expire-offers/", follow=True)
        self.assertContains(response, "lapsed")
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, TravelGrantApplication.STATUS_LAPSED)

    def test_expiring_when_nothing_is_overdue(self):
        response = self.client.post("/grants/admin/expire-offers/", follow=True)
        self.assertContains(response, "No offers are past their deadline")

    def test_both_budget_actions_are_post_only(self):
        for path in ("/grants/admin/promote/", "/grants/admin/expire-offers/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 405)

    def test_the_report_page(self):
        offers.make_offer(self.application, Decimal("150000"), notify=False)
        offers.accept(self.application)
        response = self.client.get("/grants/admin/reports/")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Lagos", body)
        assert_accessible(self, body)

    def test_the_report_csv(self):
        response = self.client.get("/grants/admin/reports/export/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("grant-report.csv", response["Content-Disposition"])

    def test_the_approved_grants_export(self):
        offers.make_offer(self.application, Decimal("150000"), notify=False)
        offers.accept(self.application)
        response = self.client.get("/grants/admin/export/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("ada@example.com", response.content.decode())


class ReviewQueueTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        build_site()

    def setUp(self):
        invalidate()
        open_grants()
        self.reviewer = make_user("reviewer@example.com")
        RoleAssignment.objects.create(
            user=self.reviewer, role=Role.GRANT_REVIEWER.value, conference_year=YEAR
        )
        self.client.force_login(self.reviewer)

        self.travel = make_application(
            make_user("travel@example.com"),
            grant_type=TravelGrantApplication.GRANT_TYPE_TRAVEL,
        )
        self.both = make_application(
            make_user("both@example.com"),
            grant_type=TravelGrantApplication.GRANT_TYPE_BOTH,
        )
        for application in (self.travel, self.both):
            GrantReviewerAssignment.objects.create(
                reviewer=self.reviewer, application=application
            )

    def test_the_queue_lists_everything_assigned(self):
        response = self.client.get("/grants/review/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["assignments"]), 2)
        assert_accessible(self, response.content.decode())

    def test_the_grant_type_filter_narrows_the_queue(self):
        response = self.client.get(
            "/grants/review/", {"type": TravelGrantApplication.GRANT_TYPE_TRAVEL}
        )
        assignments = list(response.context["assignments"])
        self.assertEqual([a.application_id for a in assignments], [self.travel.pk])

    def test_an_unknown_filter_value_hides_nothing_dangerous(self):
        """
        Reviewers share these links. A bad one should show an empty queue, not an
        error page and not the unfiltered list pretending to be filtered.
        """
        response = self.client.get("/grants/review/", {"type": "nonsense"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["assignments"]), 0)

    def test_the_queue_never_shows_gender(self):
        """
        Collected for reporting to funders, and deliberately invisible to the panel
        that decides. It appears on the chair's report page and nowhere else.
        """
        self.travel.gender = TravelGrantApplication.GENDER_WOMAN
        self.travel.save()
        body = self.client.get("/grants/review/").content.decode()
        self.assertNotIn("Woman", body)
        body = self.client.get(f"/grants/review/{self.travel.pk}/").content.decode()
        self.assertNotIn("Woman", body)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class FinanceViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        build_site()

    def setUp(self):
        invalidate()
        open_grants()
        self.finance = make_user("finance@example.com")
        RoleAssignment.objects.create(
            user=self.finance, role=Role.FINANCE.value, conference_year=YEAR
        )
        self.application = make_application(make_user("ada@example.com"))
        offers.make_offer(self.application, Decimal("150000"), notify=False)
        offers.accept(self.application)
        self.client.force_login(self.finance)

    def test_the_finance_list(self):
        response = self.client.get("/grants/finance/")
        self.assertEqual(response.status_code, 200)
        assert_accessible(self, response.content.decode())

    def test_the_detail_page_shows_the_payout_state(self):
        response = self.client.get(f"/grants/finance/{self.application.pk}/")
        self.assertEqual(response.status_code, 200)
        assert_accessible(self, response.content.decode())

    def test_verifying_a_receipt(self):
        payouts.mark_paid(self.application, by=self.finance)
        payouts.upload_receipt(
            self.application,
            SimpleUploadedFile("r.pdf", b"%PDF-1.4", content_type="application/pdf"),
        )
        response = self.client.post(
            f"/grants/finance/{self.application.pk}/verify/", follow=True
        )
        self.assertContains(response, "Receipt verified")
        payment = TravelGrantPayment.objects.get(application=self.application)
        self.assertTrue(payment.is_verified)

    def test_verifying_without_a_receipt_says_so(self):
        payouts.mark_paid(self.application, by=self.finance)
        response = self.client.post(
            f"/grants/finance/{self.application.pk}/verify/", follow=True
        )
        self.assertContains(response, "no receipt")

    def test_verifying_is_post_only(self):
        self.assertEqual(
            self.client.get(f"/grants/finance/{self.application.pk}/verify/").status_code,
            405,
        )


class PermissionTests(TestCase):
    """Who can reach what. Grant data is financial and personal in equal measure."""

    @classmethod
    def setUpTestData(cls):
        build_site()

    def setUp(self):
        invalidate()
        open_grants()
        self.application = make_application(make_user("ada@example.com"))

    def _chair_only_urls(self):
        return [
            "/grants/admin/",
            "/grants/admin/reports/",
            "/grants/admin/reports/export/",
            f"/grants/admin/{self.application.pk}/",
        ]

    def test_an_applicant_cannot_reach_the_chair_pages(self):
        self.client.force_login(make_user("nosy@example.com"))
        for url in self._chair_only_urls():
            with self.subTest(url=url):
                self.assertNotEqual(self.client.get(url).status_code, 200)

    def test_a_reviewer_cannot_reach_the_chair_pages(self):
        reviewer = make_user("reviewer@example.com")
        RoleAssignment.objects.create(
            user=reviewer, role=Role.GRANT_REVIEWER.value, conference_year=YEAR
        )
        self.client.force_login(reviewer)
        for url in self._chair_only_urls():
            with self.subTest(url=url):
                self.assertNotEqual(self.client.get(url).status_code, 200)

    def test_a_reviewer_cannot_make_decisions(self):
        reviewer = make_user("reviewer@example.com")
        RoleAssignment.objects.create(
            user=reviewer, role=Role.GRANT_REVIEWER.value, conference_year=YEAR
        )
        self.client.force_login(reviewer)
        self.client.post(
            "/grants/admin/decisions/",
            {
                "application_ids": str(self.application.pk),
                "decision": "approve",
                f"amount_{self.application.pk}": "1000",
            },
        )
        self.application.refresh_from_db()
        self.assertEqual(
            self.application.status, TravelGrantApplication.STATUS_UNDER_REVIEW
        )

    def test_a_chair_reaches_the_reviewer_and_finance_pages_by_implication(self):
        chair = make_user("chair@example.com")
        RoleAssignment.objects.create(
            user=chair, role=Role.GRANT_CHAIR.value, conference_year=YEAR
        )
        self.client.force_login(chair)
        for url in ("/grants/review/", "/grants/finance/") + tuple(self._chair_only_urls()):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_a_superuser_is_not_waved_through_the_review_queue(self):
        """
        Consistent with the CFP: review access is a role somebody is given, not a
        side effect of being able to log into the admin.
        """
        root = make_user("root@example.com", is_superuser=True, is_staff=True)
        self.client.force_login(root)
        self.assertNotEqual(self.client.get("/grants/review/").status_code, 200)

    def test_finance_cannot_make_decisions(self):
        """
        Separation of duties: the person who moves the money is not the person who
        decides who gets it.
        """
        finance = make_user("finance@example.com")
        RoleAssignment.objects.create(
            user=finance, role=Role.FINANCE.value, conference_year=YEAR
        )
        self.client.force_login(finance)
        self.assertNotEqual(self.client.get("/grants/admin/").status_code, 200)
        self.assertEqual(self.client.get("/grants/finance/").status_code, 200)
