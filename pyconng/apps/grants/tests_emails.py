from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings


def _make_application(user, year=2026):
    from grants.models import TravelGrantApplication
    return TravelGrantApplication.objects.create(
        user=user,
        conference_year=year,
        country_of_residence="NG",
        city="Lagos",
        financial_need_reason="Cannot afford travel.",
        employment_status="employed",
        community_impact="Will share learnings.",
        estimated_transport_cost=Decimal("100"),
        estimated_accommodation_cost=Decimal("200"),
    )


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class GrantSubmissionEmailTests(TestCase):
    def setUp(self):
        mail.outbox = []
        User = get_user_model()
        self.user = User.objects.create_user(
            username="ada", email="ada@example.com", password="pw",
            first_name="Ada", last_name="Lovelace",
        )
        self.application = _make_application(self.user)

    def test_on_application_submitted_sends_email(self):
        from grants.services import GrantService
        GrantService.on_application_submitted(self.application)
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ["ada@example.com"])
        self.assertIn("Application Received", msg.subject)
        self.assertEqual(len(msg.alternatives), 1)
        html, _ = msg.alternatives[0]
        self.assertIn("Ada Lovelace", html)
        self.assertEqual(msg.tags, ["grants", "submission"])


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class GrantDecisionEmailTests(TestCase):
    def setUp(self):
        mail.outbox = []
        User = get_user_model()
        self.user = User.objects.create_user(
            username="ada", email="ada@example.com", password="pw",
            first_name="Ada", last_name="Lovelace",
        )
        self.application = _make_application(self.user)

    def test_bulk_decision_approve_sends_approved_email(self):
        from grants.services import GrantService
        GrantService.bulk_decision(
            [self.application.id], decision="approve",
            approved_amounts={str(self.application.id): Decimal("500")},
        )
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ["ada@example.com"])
        self.assertIn("Approved", msg.subject)
        html, _ = msg.alternatives[0]
        self.assertIn("Ada Lovelace", html)
        self.assertIn("500", html)
        self.assertEqual(msg.tags, ["grants", "decision", "approved"])

    def test_bulk_decision_reject_sends_rejected_email(self):
        from grants.services import GrantService
        GrantService.bulk_decision([self.application.id], decision="reject")
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ["ada@example.com"])
        self.assertEqual(msg.tags, ["grants", "decision", "rejected"])

    def test_bulk_decision_waitlist_tells_the_applicant(self):
        """
        This test used to assert that a waitlisted applicant hears nothing, which
        was the bug rather than the requirement: send_grant_decision returned
        early for any status but approved or rejected, so someone waitlisted was
        left waiting with no word at all. They get the waitlist email now.
        """
        from grants.services import GrantService
        GrantService.bulk_decision([self.application.id], decision="waitlist")
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ["ada@example.com"])
        self.assertEqual(msg.tags, ["grants", "decision", "waitlisted"])
