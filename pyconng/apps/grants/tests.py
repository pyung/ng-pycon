"""
Tests for travel grants: the budget, the offer lifecycle, payouts and reporting.

Weighted towards money leaving the account when it should not. The budget cap was
stored, displayed and never consulted, so a bulk approval could overspend in silence;
and an award had no acceptance step, so money sat committed to people who were never
coming. Both failure modes are quiet, which is why most of what follows is about
refusals rather than happy paths.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from audit.models import AuditEntry
from editions.current import invalidate
from editions.models import Edition
from grants import budget, offers, payouts, reporting
from grants.models import (
    GrantReviewerAssignment,
    GrantSettings,
    TravelGrantApplication,
    TravelGrantPayment,
    TravelGrantReview,
)
from grants.services import GrantService

User = get_user_model()
YEAR = 2026


def make_edition(year=YEAR):
    Edition.objects.update_or_create(
        year=year,
        defaults={
            "name": f"PyCon Nigeria {year}",
            "theme": "2026",
            "is_current": True,
            "is_published": True,
        },
    )
    invalidate()


def make_user(email="ada@example.com"):
    return User.objects.create_user(
        username=email.split("@")[0], email=email, password="x"
    )


def make_settings(budget_cap="1000000", per_applicant="0", acceptance_days=14, **kwargs):
    defaults = {
        "status": GrantSettings.STATUS_OPEN,
        "application_deadline": timezone.now() + timedelta(days=30),
        "max_grant_budget": Decimal(budget_cap),
        "max_per_applicant": Decimal(per_applicant),
        "acceptance_days": acceptance_days,
    }
    defaults.update(kwargs)
    obj, _ = GrantSettings.objects.update_or_create(
        conference_year=YEAR, defaults=defaults
    )
    return obj


def make_application(user=None, status=TravelGrantApplication.STATUS_UNDER_REVIEW, **kwargs):
    user = user or make_user()
    defaults = {
        "conference_year": YEAR,
        "status": status,
        "country_of_residence": "Nigeria",
        "city": "Lagos",
        "financial_need_reason": "I cannot cover the flight.",
        "employment_status": "student",
        "community_impact": "I run a local Python meetup.",
        "estimated_transport_cost": Decimal("80000"),
        "estimated_accommodation_cost": Decimal("70000"),
        "submitted_at": timezone.now(),
    }
    defaults.update(kwargs)
    return TravelGrantApplication.objects.create(user=user, **defaults)


class BudgetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_edition()

    def setUp(self):
        invalidate()
        make_settings(budget_cap="500000")

    def test_nothing_committed_means_the_whole_budget_is_left(self):
        state = budget.status(YEAR)
        self.assertEqual(state["cap"], Decimal("500000"))
        self.assertEqual(state["committed"], Decimal("0"))
        self.assertEqual(state["remaining"], Decimal("500000"))
        self.assertFalse(state["is_exhausted"])

    def test_an_unanswered_offer_holds_its_money(self):
        """
        Until the recipient replies it cannot be promised to anybody else, which is
        the whole reason offers have a deadline.
        """
        application = make_application()
        offers.make_offer(application, Decimal("200000"), notify=False)
        state = budget.status(YEAR)
        self.assertEqual(state["committed"], Decimal("200000"))
        self.assertEqual(state["remaining"], Decimal("300000"))

    def test_declined_and_lapsed_money_comes_back(self):
        application = make_application()
        offers.make_offer(application, Decimal("200000"), notify=False)
        offers.decline(application)
        self.assertEqual(budget.status(YEAR)["remaining"], Decimal("500000"))

    def test_an_award_past_the_budget_is_refused(self):
        first = make_application(make_user("a@example.com"))
        offers.make_offer(first, Decimal("400000"), notify=False)

        second = make_application(make_user("b@example.com"))
        ok, reason = budget.check_award(second, Decimal("200000"))
        self.assertFalse(ok)
        self.assertIn("past the", reason)
        self.assertIn("100,000 is left", reason)

    def test_an_award_that_exactly_fits_is_allowed(self):
        first = make_application(make_user("a@example.com"))
        offers.make_offer(first, Decimal("400000"), notify=False)
        second = make_application(make_user("b@example.com"))
        ok, _ = budget.check_award(second, Decimal("100000"))
        self.assertTrue(ok)

    def test_the_per_applicant_cap_is_enforced(self):
        make_settings(budget_cap="1000000", per_applicant="150000")
        application = make_application()
        ok, reason = budget.check_award(application, Decimal("200000"))
        self.assertFalse(ok)
        self.assertIn("per-applicant limit", reason)

    def test_re_deciding_an_award_is_measured_against_everybody_else(self):
        """
        Raising an existing award from 400,000 to 450,000 must not be checked as if
        it were 450,000 on top of the 400,000 already held.
        """
        application = make_application()
        offers.make_offer(application, Decimal("400000"), notify=False)
        ok, reason = budget.check_award(application, Decimal("450000"))
        self.assertTrue(ok, reason)

    def test_no_cap_means_no_limit_but_not_zero_remaining(self):
        """
        "No limit" and "nothing left" are opposite situations, so remaining is None
        rather than zero when no cap is set.
        """
        make_settings(budget_cap="0")
        state = budget.status(YEAR)
        self.assertFalse(state["has_cap"])
        self.assertIsNone(state["remaining"])
        self.assertFalse(state["is_exhausted"])
        ok, _ = budget.check_award(make_application(), Decimal("99999999"))
        self.assertTrue(ok)

    def test_a_zero_award_is_allowed(self):
        """How a chair records "you are in, we are not paying travel"."""
        ok, _ = budget.check_award(make_application(), Decimal("0"))
        self.assertTrue(ok)

    def test_a_negative_award_is_refused(self):
        ok, reason = budget.check_award(make_application(), Decimal("-1"))
        self.assertFalse(ok)
        self.assertIn("negative", reason)

    def test_affordable_waitlisted_stops_at_the_money(self):
        funded = make_application(make_user("funded@example.com"))
        offers.make_offer(funded, Decimal("400000"), notify=False)

        for index in range(3):
            make_application(
                make_user(f"wait{index}@example.com"),
                status=TravelGrantApplication.STATUS_WAITLISTED,
                estimated_transport_cost=Decimal("60000"),
                estimated_accommodation_cost=Decimal("0"),
            )
        affordable = budget.affordable_waitlisted(YEAR)
        # 100,000 left covers one 60,000 request, not two.
        self.assertEqual(len(affordable), 1)

    def test_affordable_waitlisted_is_ordered_by_score(self):
        reviewer = make_user("reviewer@example.com")
        low = make_application(
            make_user("low@example.com"),
            status=TravelGrantApplication.STATUS_WAITLISTED,
            estimated_transport_cost=Decimal("10000"),
            estimated_accommodation_cost=Decimal("0"),
        )
        high = make_application(
            make_user("high@example.com"),
            status=TravelGrantApplication.STATUS_WAITLISTED,
            estimated_transport_cost=Decimal("10000"),
            estimated_accommodation_cost=Decimal("0"),
        )
        for application, score in ((low, 2), (high, 5)):
            assignment = GrantReviewerAssignment.objects.create(
                reviewer=reviewer, application=application
            )
            TravelGrantReview.objects.create(
                assignment=assignment,
                need_score=score,
                impact_score=score,
                contribution_score=score,
                diversity_score=score,
            )
        order = [c["application"].pk for c in budget.affordable_waitlisted(YEAR)]
        self.assertEqual(order[0], high.pk)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class OfferTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_edition()

    def setUp(self):
        invalidate()
        mail.outbox = []
        make_settings(budget_cap="500000")
        self.chair = make_user("chair@example.com")
        self.application = make_application()

    def test_an_approval_becomes_an_offer_with_a_deadline(self):
        offers.make_offer(self.application, Decimal("100000"), by=self.chair)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, TravelGrantApplication.STATUS_APPROVED)
        self.assertIsNotNone(self.application.acceptance_deadline)
        self.assertTrue(self.application.awaiting_acceptance)
        self.assertTrue(self.application.can_accept)

    def test_the_offer_email_carries_the_deadline(self):
        offers.make_offer(self.application, Decimal("100000"), by=self.chair)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("confirm", mail.outbox[0].subject.lower())
        self.assertIn("expires", mail.outbox[0].body.lower() + mail.outbox[0].subject.lower())

    def test_no_acceptance_window_means_no_deadline(self):
        make_settings(budget_cap="500000", acceptance_days=0)
        offers.make_offer(self.application, Decimal("100000"), notify=False)
        self.application.refresh_from_db()
        self.assertIsNone(self.application.acceptance_deadline)
        self.assertFalse(self.application.offer_has_expired)
        self.assertTrue(self.application.can_accept)

    def test_an_offer_past_the_budget_is_refused(self):
        with self.assertRaises(budget.BudgetError):
            offers.make_offer(self.application, Decimal("600000"), notify=False)
        self.application.refresh_from_db()
        self.assertEqual(
            self.application.status, TravelGrantApplication.STATUS_UNDER_REVIEW
        )

    def test_accepting_fixes_the_money_and_issues_a_ticket(self):
        from tickets.models import Ticket

        offers.make_offer(self.application, Decimal("100000"), notify=False)
        mail.outbox = []
        offers.accept(self.application)

        self.application.refresh_from_db()
        self.assertEqual(self.application.status, TravelGrantApplication.STATUS_ACCEPTED)
        self.assertIsNotNone(self.application.accepted_at)

        ticket = Ticket.objects.get(user=self.application.user)
        self.assertEqual(ticket.status, Ticket.PAID)
        self.assertEqual(ticket.complimentary_reason, "grant recipient")
        # One email, not two: the confirmation already mentions the ticket.
        self.assertEqual(len(mail.outbox), 1)

    def test_accepting_twice_is_a_no_op(self):
        from tickets.models import Ticket

        offers.make_offer(self.application, Decimal("100000"), notify=False)
        offers.accept(self.application)
        offers.accept(self.application)
        self.assertEqual(Ticket.objects.filter(user=self.application.user).count(), 1)

    def test_accepting_after_the_deadline_is_refused(self):
        """
        The budget has very likely been offered onwards by then, so honouring a late
        acceptance would overspend quietly.
        """
        offers.make_offer(self.application, Decimal("100000"), notify=False)
        self.application.acceptance_deadline = timezone.now() - timedelta(minutes=1)
        self.application.save()

        self.assertTrue(self.application.offer_has_expired)
        self.assertFalse(self.application.can_accept)
        with self.assertRaises(offers.OfferError) as caught:
            offers.accept(self.application)
        self.assertIn("deadline", str(caught.exception))

    def test_accepting_something_that_is_not_an_offer_is_refused(self):
        with self.assertRaises(offers.OfferError):
            offers.accept(self.application)

    def test_declining_releases_the_money(self):
        offers.make_offer(self.application, Decimal("200000"), notify=False)
        self.assertEqual(budget.status(YEAR)["remaining"], Decimal("300000"))

        offers.decline(self.application, reason="My visa was refused.")
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, TravelGrantApplication.STATUS_DECLINED)
        self.assertEqual(self.application.decline_reason, "My visa was refused.")
        self.assertEqual(budget.status(YEAR)["remaining"], Decimal("500000"))

    def test_declining_stays_open_after_the_deadline(self):
        """
        Somebody telling us late that they cannot come is doing us a favour, and
        refusing the message because a timer ran out would be perverse.
        """
        offers.make_offer(self.application, Decimal("100000"), notify=False)
        self.application.acceptance_deadline = timezone.now() - timedelta(days=1)
        self.application.save()
        self.assertTrue(self.application.can_decline)
        offers.decline(self.application)
        self.assertEqual(self.application.status, TravelGrantApplication.STATUS_DECLINED)

    def test_an_accepted_grant_can_still_be_declined(self):
        offers.make_offer(self.application, Decimal("100000"), notify=False)
        offers.accept(self.application)
        offers.decline(self.application, reason="Something came up.")
        self.assertEqual(self.application.status, TravelGrantApplication.STATUS_DECLINED)

    def test_declining_removes_the_pending_payment(self):
        offers.make_offer(self.application, Decimal("100000"), notify=False)
        self.assertTrue(TravelGrantPayment.objects.filter(application=self.application).exists())
        offers.decline(self.application)
        self.assertFalse(TravelGrantPayment.objects.filter(application=self.application).exists())

    def test_an_offer_lapses_and_says_so_separately_from_a_decline(self):
        offers.make_offer(self.application, Decimal("100000"), notify=False)
        self.application.acceptance_deadline = timezone.now() - timedelta(minutes=1)
        self.application.save()
        mail.outbox = []

        offers.lapse(self.application)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, TravelGrantApplication.STATUS_LAPSED)
        self.assertEqual(budget.status(YEAR)["remaining"], Decimal("500000"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("expired", mail.outbox[0].subject.lower())

    def test_lapsing_an_offer_that_has_not_expired_is_refused(self):
        offers.make_offer(self.application, Decimal("100000"), notify=False)
        with self.assertRaises(offers.OfferError):
            offers.lapse(self.application)

    def test_expire_overdue_finds_only_the_overdue(self):
        live = make_application(make_user("live@example.com"))
        offers.make_offer(live, Decimal("50000"), notify=False)
        offers.make_offer(self.application, Decimal("50000"), notify=False)
        self.application.acceptance_deadline = timezone.now() - timedelta(minutes=1)
        self.application.save()

        lapsed, _ = offers.expire_overdue(YEAR)
        self.assertEqual([a.pk for a in lapsed], [self.application.pk])
        live.refresh_from_db()
        self.assertEqual(live.status, TravelGrantApplication.STATUS_APPROVED)

    def test_expiring_is_idempotent(self):
        offers.make_offer(self.application, Decimal("50000"), notify=False)
        self.application.acceptance_deadline = timezone.now() - timedelta(minutes=1)
        self.application.save()
        offers.expire_overdue(YEAR)
        lapsed, _ = offers.expire_overdue(YEAR)
        self.assertEqual(lapsed, [])

    def test_a_dry_run_changes_nothing(self):
        offers.make_offer(self.application, Decimal("50000"), notify=False)
        self.application.acceptance_deadline = timezone.now() - timedelta(minutes=1)
        self.application.save()
        mail.outbox = []

        overdue, _ = offers.expire_overdue(YEAR, dry_run=True)
        self.assertEqual(len(overdue), 1)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, TravelGrantApplication.STATUS_APPROVED)
        self.assertEqual(len(mail.outbox), 0)

    def test_every_transition_lands_on_the_audit_trail(self):
        offers.make_offer(self.application, Decimal("100000"), by=self.chair, notify=False)
        offers.accept(self.application)
        offers.decline(self.application, reason="Plans changed.")

        actions = list(
            AuditEntry.objects.filter(target_id=str(self.application.pk))
            .values_list("action", flat=True)
        )
        self.assertTrue(any("offered" in a for a in actions))
        self.assertTrue(any("accepted" in a for a in actions))
        self.assertTrue(any("declined" in a for a in actions))

    def test_a_ticketing_failure_does_not_lose_the_acceptance(self):
        from unittest.mock import patch

        offers.make_offer(self.application, Decimal("100000"), notify=False)
        with patch(
            "tickets.issuing.issue_complimentary_ticket",
            side_effect=RuntimeError("Paystack is on fire"),
        ):
            with self.assertLogs("grants.offers", level="ERROR"):
                offers.accept(self.application)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, TravelGrantApplication.STATUS_ACCEPTED)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class WaitlistPromotionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_edition()

    def setUp(self):
        invalidate()
        mail.outbox = []
        make_settings(budget_cap="200000")
        self.funded = make_application(make_user("funded@example.com"))
        self.waiting = make_application(
            make_user("waiting@example.com"),
            status=TravelGrantApplication.STATUS_WAITLISTED,
            estimated_transport_cost=Decimal("150000"),
            estimated_accommodation_cost=Decimal("0"),
        )

    def test_declining_promotes_the_next_person(self):
        offers.make_offer(self.funded, Decimal("200000"), notify=False)
        self.assertEqual(budget.status(YEAR)["remaining"], Decimal("0"))
        mail.outbox = []

        _, promoted = offers.decline(self.funded)
        self.assertEqual([a.pk for a in promoted], [self.waiting.pk])
        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.status, TravelGrantApplication.STATUS_APPROVED)
        self.assertIsNotNone(self.waiting.promoted_from_waitlist_at)
        self.assertIsNotNone(self.waiting.acceptance_deadline)

    def test_a_promoted_applicant_is_told(self):
        offers.make_offer(self.funded, Decimal("200000"), notify=False)
        mail.outbox = []
        offers.decline(self.funded)
        recipients = [set(m.to) for m in mail.outbox]
        self.assertIn({"waiting@example.com"}, recipients)
        promoted_email = next(m for m in mail.outbox if m.to == ["waiting@example.com"])
        self.assertIn("opened up", promoted_email.subject)

    def test_lapsing_promotes_too(self):
        offers.make_offer(self.funded, Decimal("200000"), notify=False)
        self.funded.acceptance_deadline = timezone.now() - timedelta(minutes=1)
        self.funded.save()
        _, promoted = offers.lapse(self.funded)
        self.assertEqual([a.pk for a in promoted], [self.waiting.pk])

    def test_promotion_can_be_switched_off_for_an_edition(self):
        make_settings(budget_cap="200000", auto_promote_waitlist=False)
        offers.make_offer(self.funded, Decimal("200000"), notify=False)
        _, promoted = offers.decline(self.funded)
        self.assertEqual(promoted, [])
        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.status, TravelGrantApplication.STATUS_WAITLISTED)

    def test_nobody_is_promoted_beyond_what_the_budget_covers(self):
        expensive = make_application(
            make_user("expensive@example.com"),
            status=TravelGrantApplication.STATUS_WAITLISTED,
            estimated_transport_cost=Decimal("900000"),
            estimated_accommodation_cost=Decimal("0"),
        )
        offers.make_offer(self.funded, Decimal("100000"), notify=False)
        promoted = offers.promote_from_waitlist(YEAR)
        self.assertNotIn(expensive.pk, [a.pk for a in promoted])

    def test_lapsing_several_offers_pools_the_freed_budget(self):
        """
        Three small offers lapsing may fund one larger applicant that none of them
        could on its own, so promotion waits until they have all been released.
        """
        make_settings(budget_cap="300000")
        # Only one person waiting, so what gets promoted is unambiguous.
        self.waiting.delete()
        smalls = []
        for index in range(3):
            application = make_application(make_user(f"small{index}@example.com"))
            offers.make_offer(application, Decimal("100000"), notify=False)
            application.acceptance_deadline = timezone.now() - timedelta(minutes=1)
            application.save()
            smalls.append(application)

        big = make_application(
            make_user("big@example.com"),
            status=TravelGrantApplication.STATUS_WAITLISTED,
            estimated_transport_cost=Decimal("250000"),
            estimated_accommodation_cost=Decimal("0"),
        )
        _, promoted = offers.expire_overdue(YEAR)
        self.assertEqual([a.pk for a in promoted], [big.pk])


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class BulkDecisionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_edition()

    def setUp(self):
        invalidate()
        mail.outbox = []
        make_settings(budget_cap="250000")
        self.chair = make_user("chair@example.com")

    def test_approving_within_budget_makes_offers(self):
        first = make_application(make_user("a@example.com"))
        count, refused = GrantService.bulk_decision(
            [first.pk], "approve", {str(first.pk): Decimal("100000")}, actor=self.chair
        )
        self.assertEqual(count, 1)
        self.assertEqual(refused, [])
        first.refresh_from_db()
        self.assertEqual(first.status, TravelGrantApplication.STATUS_APPROVED)
        self.assertIsNotNone(first.acceptance_deadline)

    def test_the_budget_stops_a_bulk_approval_and_names_who(self):
        """
        The failure this module was rebuilt to prevent: before, this would have
        approved all three and overspent by 50,000 without a word.
        """
        applications = [
            make_application(make_user(f"a{index}@example.com")) for index in range(3)
        ]
        amounts = {str(a.pk): Decimal("100000") for a in applications}
        count, refused = GrantService.bulk_decision(
            [a.pk for a in applications], "approve", amounts, actor=self.chair
        )
        self.assertEqual(count, 2)
        self.assertEqual(len(refused), 1)
        self.assertIn("past the", refused[0][1])
        self.assertEqual(budget.status(YEAR)["remaining"], Decimal("50000"))

    def test_a_refusal_leaves_that_application_untouched(self):
        applications = [
            make_application(make_user(f"a{index}@example.com")) for index in range(3)
        ]
        amounts = {str(a.pk): Decimal("100000") for a in applications}
        _, refused = GrantService.bulk_decision(
            [a.pk for a in applications], "approve", amounts, actor=self.chair
        )
        refused[0][0].refresh_from_db()
        self.assertEqual(
            refused[0][0].status, TravelGrantApplication.STATUS_UNDER_REVIEW
        )

    def test_rejecting_and_waitlisting_still_notify(self):
        rejected = make_application(make_user("r@example.com"))
        waitlisted = make_application(make_user("w@example.com"))
        GrantService.bulk_decision([rejected.pk], "reject", actor=self.chair)
        GrantService.bulk_decision([waitlisted.pk], "waitlist", actor=self.chair)
        self.assertEqual(len(mail.outbox), 2)

    def test_an_unknown_decision_does_nothing(self):
        application = make_application()
        count, refused = GrantService.bulk_decision([application.pk], "maybe")
        self.assertEqual((count, refused), (0, []))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class PayoutTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_edition()

    def setUp(self):
        invalidate()
        mail.outbox = []
        make_settings(budget_cap="500000")
        self.finance = make_user("finance@example.com")
        self.application = make_application()
        offers.make_offer(self.application, Decimal("100000"), notify=False)
        offers.accept(self.application)
        mail.outbox = []

    def test_payout_details_are_saved_and_recorded_without_the_numbers(self):
        """
        The trail is readable by anyone with admin access, so it records that details
        changed rather than what they are.
        """
        payouts.save_payout_details(
            self.application,
            {
                "payout_method": TravelGrantApplication.PAYOUT_BANK_TRANSFER,
                "bank_name": "Example Bank",
                "account_name": "Ada Lovelace",
                "account_number": "0123456789",
                "payout_notes": "",
            },
        )
        self.application.refresh_from_db()
        self.assertTrue(self.application.has_payout_details)

        entry = AuditEntry.objects.filter(action__icontains="payout details").first()
        self.assertIsNotNone(entry)
        self.assertNotIn("0123456789", entry.note)
        self.assertIn("account_number", entry.note)

    def test_marking_paid_tells_the_recipient(self):
        payouts.mark_paid(self.application, reference="TRF-1", by=self.finance)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, TravelGrantApplication.STATUS_PAID)
        self.assertEqual(len(mail.outbox), 1)

    def test_a_receipt_can_be_uploaded_by_the_recipient(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        payouts.mark_paid(self.application, by=self.finance)
        payment = payouts.upload_receipt(
            self.application,
            SimpleUploadedFile("receipt.pdf", b"%PDF-1.4 fake", content_type="application/pdf"),
            by=self.application.user,
        )
        self.assertTrue(payment.receipt)
        self.assertIsNotNone(payment.receipt_uploaded_at)

    def test_verifying_needs_a_payment_and_a_receipt(self):
        with self.assertRaises(payouts.PayoutError) as caught:
            payouts.verify_receipt(self.application, by=self.finance)
        self.assertIn("paid before verifying", str(caught.exception))

        payouts.mark_paid(self.application, by=self.finance)
        with self.assertRaises(payouts.PayoutError) as caught:
            payouts.verify_receipt(self.application, by=self.finance)
        self.assertIn("no receipt", str(caught.exception))

    def test_verified_is_a_state_beyond_paid(self):
        """
        Money leaving an account and money being accounted for are separate facts;
        collapsing them means the second never happens.
        """
        from django.core.files.uploadedfile import SimpleUploadedFile

        payouts.mark_paid(self.application, by=self.finance)
        payouts.upload_receipt(
            self.application,
            SimpleUploadedFile("receipt.pdf", b"%PDF-1.4 fake", content_type="application/pdf"),
        )
        payment = payouts.verify_receipt(self.application, by=self.finance)
        self.assertTrue(payment.is_paid)
        self.assertTrue(payment.is_verified)
        self.assertEqual(payment.receipt_verified_by, self.finance)

    def test_outstanding_receipts_lists_only_unevidenced_reimbursements(self):
        payouts.mark_paid(self.application, by=self.finance)
        self.assertEqual(
            [p.application_id for p in payouts.outstanding_receipts(YEAR)],
            [self.application.pk],
        )

    def test_a_direct_payment_is_not_chased_for_a_receipt(self):
        """Asking somebody to evidence a transaction they never handled."""
        payment = payouts.payment_for(self.application)
        payment.payment_type = TravelGrantPayment.PAYMENT_TYPE_DIRECT
        payment.save()
        payouts.mark_paid(self.application, by=self.finance)
        self.assertEqual(list(payouts.outstanding_receipts(YEAR)), [])
        payment.refresh_from_db()
        self.assertFalse(payment.needs_receipt)

    def test_awaiting_payout_details_finds_the_people_finance_must_chase(self):
        self.assertIn(
            self.application.pk,
            [a.pk for a in payouts.awaiting_payout_details(YEAR)],
        )
        payouts.save_payout_details(
            self.application,
            {
                "payout_method": TravelGrantApplication.PAYOUT_BANK_TRANSFER,
                "bank_name": "Example Bank",
                "account_name": "Ada Lovelace",
                "account_number": "0123456789",
                "payout_notes": "",
            },
        )
        self.assertNotIn(
            self.application.pk,
            [a.pk for a in payouts.awaiting_payout_details(YEAR)],
        )


class ReportingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_edition()

    def setUp(self):
        invalidate()
        make_settings(budget_cap="1000000")

    def _award(self, email, **kwargs):
        application = make_application(make_user(email), **kwargs)
        offers.make_offer(application, Decimal("100000"), notify=False)
        offers.accept(application)
        return application

    def test_only_accepted_and_paid_grants_are_reported(self):
        """
        An unanswered offer may lapse, and reporting it to a sponsor as a grant given
        is a number that quietly becomes wrong.
        """
        self._award("accepted@example.com")
        pending = make_application(make_user("pending@example.com"))
        offers.make_offer(pending, Decimal("100000"), notify=False)

        self.assertEqual(reporting.summary(YEAR)["awarded_count"], 1)

    def test_the_city_breakdown(self):
        self._award("a@example.com", city="Lagos")
        self._award("b@example.com", city="Lagos")
        self._award("c@example.com", city="Abuja")
        rows = {row["label"]: row["count"] for row in reporting.by_city(YEAR)}
        self.assertEqual(rows, {"Lagos": 2, "Abuja": 1})

    def test_the_gender_breakdown_folds_self_descriptions(self):
        """
        A report listing three people's own words beside a count of one is a report
        that identifies them.
        """
        self._award("a@example.com", gender=TravelGrantApplication.GENDER_WOMAN)
        self._award(
            "b@example.com",
            gender=TravelGrantApplication.GENDER_SELF_DESCRIBE,
            gender_self_described="Agender",
        )
        labels = [row["label"] for row in reporting.by_gender(YEAR)]
        self.assertIn("Woman", labels)
        self.assertIn("Self-described", labels)
        self.assertNotIn("Agender", labels)

    def test_unanswered_gender_is_reported_rather_than_dropped(self):
        """A report that hides its own coverage is a report that misleads."""
        self._award("a@example.com", gender=TravelGrantApplication.GENDER_WOMAN)
        self._award("b@example.com", gender="")
        rows = {row["label"]: row["count"] for row in reporting.by_gender(YEAR)}
        self.assertEqual(rows.get("Not answered"), 1)

        summary = reporting.summary(YEAR)
        self.assertEqual(summary["gender_answered"], 1)
        self.assertEqual(summary["awarded_count"], 2)

    def test_the_grant_type_breakdown(self):
        self._award("a@example.com", grant_type=TravelGrantApplication.GRANT_TYPE_TRAVEL)
        self._award("b@example.com", grant_type=TravelGrantApplication.GRANT_TYPE_BOTH)
        labels = {row["label"]: row["count"] for row in reporting.by_grant_type(YEAR)}
        self.assertEqual(labels, {"Travel only": 1, "Travel and accommodation": 1})

    def test_the_summary_counts_the_things_a_sponsor_asks_about(self):
        self._award("a@example.com", first_time_pycon=True, is_speaking=True)
        self._award("b@example.com", first_time_pycon=False, is_speaking=False)
        summary = reporting.summary(YEAR)
        self.assertEqual(summary["awarded_count"], 2)
        self.assertEqual(summary["awarded_total"], Decimal("200000"))
        self.assertEqual(summary["average_award"], Decimal("100000"))
        self.assertEqual(summary["first_time_attendees"], 1)
        self.assertEqual(summary["speakers_supported"], 1)

    def test_the_csv_carries_the_breakdowns_and_states_its_coverage(self):
        self._award("a@example.com", city="Lagos", gender=TravelGrantApplication.GENDER_MAN)
        self._award("b@example.com", city="Kano", gender="")
        body = reporting.to_csv(YEAR)
        self.assertIn("By city", body)
        self.assertIn("Lagos", body)
        self.assertIn("By gender", body)
        self.assertIn("it is optional", body)

    def test_the_export_covers_awarded_grants_with_their_type_and_city(self):
        self._award("a@example.com", city="Lagos")
        body = GrantService.export_approved_grants()
        self.assertIn("Grant type", body)
        self.assertIn("Lagos", body)
        self.assertIn("a@example.com", body)
