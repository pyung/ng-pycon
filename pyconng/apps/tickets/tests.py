"""
Tests for ticketing: what is on sale, what it costs, and what happens afterwards.

Weighted towards the arithmetic and the boundaries, because this is the part of the
site that takes money. A sale window that is off by one direction sells tickets
nobody meant to sell; a fixed discount larger than the order turns a purchase into
a refund; a refund that does not release its place quietly shrinks the venue.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from editions.current import invalidate
from editions.models import Edition
from tickets import checkin as checkin_service
from tickets import waitlist as waitlist_service
from tickets.forms import PurchaseForm, TicketTransferForm
from tickets.invoices import issue_invoice, next_number
from tickets.issuing import (
    REASON_GRANT,
    REASON_SPEAKER,
    REASON_SPONSOR,
    IssueError,
    complimentary_type,
    issue_complimentary_ticket,
    issue_for_reason,
    issue_sponsor_ticket,
)
from tickets.models import (
    Coupon,
    Invoice,
    Refund,
    Ticket,
    TicketSale,
    TicketSettings,
    TicketType,
    TicketWaitlistEntry,
)
from tickets.refunds import RefundError, decide_refund, mark_paid, request_refund

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


def make_user(email="ada@example.com", **kwargs):
    return User.objects.create_user(
        username=email.split("@")[0], email=email, password="x", **kwargs
    )


def make_type(name="Personal", **kwargs):
    defaults = {
        "conference_year": YEAR,
        "price": Decimal("20000"),
        "early_bird_price": Decimal("0"),
        "early_bird_count": 0,
        "regular_count": 50,
        "is_active": True,
    }
    defaults.update(kwargs)
    return TicketType.objects.create(name=name, **defaults)


def make_paid_order(user, ticket_type, quantity=1, amount=None, discount=0, places=True):
    from tickets.utils import generate_order_code

    amount = Decimal(amount if amount is not None else ticket_type.price * quantity)
    ticket = Ticket.objects.create(
        order=generate_order_code(),
        user=user,
        ticket_type=ticket_type,
        quantity=quantity,
        amount=amount,
        total_amount=amount,
        discount_amount=Decimal(discount),
        status=Ticket.PAID,
        date_paid=timezone.now(),
        conference_year=YEAR,
        created_tickets=places,
    )
    if places:
        for index in range(quantity):
            TicketSale.objects.create(
                ticket=ticket,
                user=user,
                attendee_email=user.email,
                full_name=f"Attendee {index + 1}",
            )
    return ticket


class SaleWindowTests(TestCase):
    """Inventory controls: when a type can be bought, and why not when it cannot."""

    @classmethod
    def setUpTestData(cls):
        make_edition()

    def setUp(self):
        invalidate()

    def test_a_type_with_no_dates_is_on_sale(self):
        self.assertEqual(make_type().sale_state, "on_sale")

    def test_a_window_that_has_not_opened_says_so(self):
        ticket_type = make_type(sales_start_at=timezone.now() + timedelta(days=2))
        self.assertEqual(ticket_type.sale_state, "not_yet")
        self.assertFalse(ticket_type.is_on_sale)

    def test_a_window_that_has_closed_says_so(self):
        ticket_type = make_type(sales_end_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(ticket_type.sale_state, "closed")

    def test_a_window_open_right_now_is_on_sale(self):
        ticket_type = make_type(
            sales_start_at=timezone.now() - timedelta(days=1),
            sales_end_at=timezone.now() + timedelta(days=1),
        )
        self.assertEqual(ticket_type.sale_state, "on_sale")

    def test_sold_out_beats_an_open_window(self):
        ticket_type = make_type(regular_count=1)
        make_paid_order(make_user(), ticket_type)
        self.assertEqual(ticket_type.sale_state, "sold_out")

    def test_an_inactive_or_invite_only_type_is_not_on_sale(self):
        self.assertEqual(make_type("Hidden", is_active=False).sale_state, "inactive")
        self.assertEqual(
            make_type("Comp", availability=TicketType.INVITE).sale_state, "invite_only"
        )

    def test_a_sold_out_type_still_reports_its_price(self):
        """
        current_price used to return zero when sold out, which is why the purchase
        page could not show what a ticket costs while saying it was gone.
        """
        ticket_type = make_type(regular_count=1)
        make_paid_order(make_user(), ticket_type)
        self.assertTrue(ticket_type.is_sold_out)
        self.assertEqual(ticket_type.current_price, Decimal("20000"))

    def test_the_queryset_and_the_property_agree(self):
        on_sale = make_type("Open")
        make_type("Later", sales_start_at=timezone.now() + timedelta(days=1))
        make_type("Over", sales_end_at=timezone.now() - timedelta(days=1))
        make_type("Invite", availability=TicketType.INVITE)
        make_type("Off", is_active=False)

        names = set(TicketType.objects.on_sale().for_year(YEAR).values_list("name", flat=True))
        self.assertEqual(names, {on_sale.name})
        for ticket_type in TicketType.objects.for_year(YEAR):
            with self.subTest(name=ticket_type.name):
                self.assertEqual(
                    ticket_type.is_on_sale, ticket_type.name in names
                )

    def test_closing_a_window_before_it_opens_is_refused(self):
        ticket_type = make_type(
            sales_start_at=timezone.now() + timedelta(days=2),
            sales_end_at=timezone.now() + timedelta(days=1),
        )
        with self.assertRaises(ValidationError):
            ticket_type.full_clean()

    def test_verification_without_a_prompt_is_refused(self):
        ticket_type = make_type(requires_verification=True, verification_prompt="")
        with self.assertRaises(ValidationError) as caught:
            ticket_type.full_clean()
        self.assertIn("verification_prompt", caught.exception.error_dict)


class EarlyBirdTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_edition()

    def setUp(self):
        invalidate()

    def test_early_bird_applies_while_the_count_lasts(self):
        ticket_type = make_type(
            early_bird_price=Decimal("15000"), early_bird_count=2, regular_count=10
        )
        self.assertEqual(ticket_type.current_price, Decimal("15000"))
        make_paid_order(make_user("a@example.com"), ticket_type, quantity=2)
        self.assertFalse(ticket_type.early_bird_remaining)
        self.assertEqual(ticket_type.current_price, Decimal("20000"))

    def test_early_bird_also_ends_on_its_date(self):
        """
        A count alone keeps the cheap price alive for months when sales are slow.
        Whichever limit comes first ends it.
        """
        ticket_type = make_type(
            early_bird_price=Decimal("15000"),
            early_bird_count=50,
            early_bird_ends_at=timezone.now() - timedelta(minutes=1),
        )
        self.assertFalse(ticket_type.early_bird_remaining)
        self.assertEqual(ticket_type.current_price, Decimal("20000"))

    def test_a_future_early_bird_date_keeps_the_price(self):
        ticket_type = make_type(
            early_bird_price=Decimal("15000"),
            early_bird_count=50,
            early_bird_ends_at=timezone.now() + timedelta(days=5),
        )
        self.assertTrue(ticket_type.early_bird_remaining)
        self.assertEqual(ticket_type.current_price, Decimal("15000"))

    def test_no_early_bird_price_means_no_early_bird(self):
        ticket_type = make_type(early_bird_price=Decimal("0"), early_bird_count=10)
        self.assertFalse(ticket_type.early_bird_remaining)


class CouponTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_edition()

    def setUp(self):
        invalidate()
        self.user = make_user()

    def test_a_percentage_discount(self):
        coupon = Coupon.objects.create(
            code="TEN", percentage=10, conference_year=YEAR, max_usage=5
        )
        self.assertEqual(coupon.discount_for(Decimal("20000")), Decimal("2000.00"))

    def test_a_fixed_discount(self):
        coupon = Coupon.objects.create(
            code="FIVEK",
            discount_type=Coupon.FIXED,
            amount=Decimal("5000"),
            conference_year=YEAR,
            max_usage=5,
        )
        self.assertEqual(coupon.discount_for(Decimal("20000")), Decimal("5000"))

    def test_a_fixed_discount_never_exceeds_the_order(self):
        """Otherwise a large code turns a purchase into a payment out."""
        coupon = Coupon.objects.create(
            code="BIG",
            discount_type=Coupon.FIXED,
            amount=Decimal("50000"),
            conference_year=YEAR,
        )
        self.assertEqual(coupon.discount_for(Decimal("20000")), Decimal("20000"))
        self.assertEqual(coupon.discount_for(Decimal("0")), Decimal("0"))

    def test_the_validity_window(self):
        now = timezone.now()
        future = Coupon.objects.create(
            code="SOON", percentage=10, conference_year=YEAR,
            valid_from=now + timedelta(days=1),
        )
        past = Coupon.objects.create(
            code="GONE", percentage=10, conference_year=YEAR,
            valid_until=now - timedelta(minutes=1),
        )
        current = Coupon.objects.create(
            code="NOW", percentage=10, conference_year=YEAR,
            valid_from=now - timedelta(days=1), valid_until=now + timedelta(days=1),
        )
        self.assertFalse(future.is_valid)
        self.assertFalse(past.is_valid)
        self.assertTrue(current.is_valid)

    def test_the_manual_kill_switch_still_works(self):
        coupon = Coupon.objects.create(
            code="STOP", percentage=10, conference_year=YEAR, expired=True
        )
        self.assertFalse(coupon.is_valid)

    def test_the_overall_usage_cap(self):
        coupon = Coupon.objects.create(
            code="ONCE", percentage=10, conference_year=YEAR, max_usage=1
        )
        ticket_type = make_type()
        ticket = make_paid_order(self.user, ticket_type)
        ticket.coupon = coupon
        ticket.save()
        self.assertEqual(coupon.usage_count, 1)
        self.assertFalse(coupon.is_valid)

    def test_the_per_person_cap(self):
        coupon = Coupon.objects.create(
            code="PERSONAL", percentage=10, conference_year=YEAR,
            max_usage=100, max_per_user=1,
        )
        ticket_type = make_type()
        other = make_user("grace@example.com")
        ticket = make_paid_order(self.user, ticket_type)
        ticket.coupon = coupon
        ticket.save()

        self.assertFalse(coupon.usable_by(self.user))
        self.assertTrue(coupon.usable_by(other))

    def test_restricting_a_coupon_to_particular_types(self):
        """What makes a sponsor code usable: all of one type, nothing off another."""
        student = make_type("Student", price=Decimal("5000"))
        company = make_type("Company", price=Decimal("80000"))
        coupon = Coupon.objects.create(
            code="STUDENTS", percentage=100, conference_year=YEAR, max_usage=100
        )
        coupon.ticket_types.add(student)

        self.assertTrue(coupon.applies_to(student))
        self.assertFalse(coupon.applies_to(company))

        unrestricted = Coupon.objects.create(
            code="ALL", percentage=10, conference_year=YEAR
        )
        self.assertTrue(unrestricted.applies_to(student))
        self.assertTrue(unrestricted.applies_to(company))

    def test_nonsense_discounts_are_refused(self):
        with self.assertRaises(ValidationError):
            Coupon(code="A", percentage=0, conference_year=YEAR).full_clean()
        with self.assertRaises(ValidationError):
            Coupon(code="B", percentage=150, conference_year=YEAR).full_clean()
        with self.assertRaises(ValidationError):
            Coupon(
                code="C", discount_type=Coupon.FIXED, amount=Decimal("0"),
                conference_year=YEAR,
            ).full_clean()
        now = timezone.now()
        with self.assertRaises(ValidationError):
            Coupon(
                code="D", percentage=10, conference_year=YEAR,
                valid_from=now + timedelta(days=2), valid_until=now,
            ).full_clean()


class PurchaseFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_edition()

    def setUp(self):
        invalidate()
        self.user = make_user()
        self.personal = make_type("Personal", price=Decimal("20000"), regular_count=20)

    def _post(self, **data):
        payload = {"Personal": 1}
        payload.update(data)
        return PurchaseForm(payload, conference_year=YEAR, user=self.user)

    def test_a_simple_order_records_its_price(self):
        form = self._post()
        self.assertTrue(form.is_valid(), form.errors)
        ticket, total = form.save(self.user)
        self.assertEqual(total, Decimal("20000"))
        self.assertEqual(ticket.amount, Decimal("20000"))
        self.assertEqual(ticket.discount_amount, Decimal("0"))

    def test_more_than_the_per_order_cap_is_refused(self):
        self.personal.max_per_order = 2
        self.personal.save()
        form = self._post(Personal=3)
        self.assertFalse(form.is_valid())
        self.assertIn("At most 2", str(form.errors))

    def test_more_than_is_left_is_refused(self):
        self.personal.regular_count = 2
        self.personal.save()
        form = self._post(Personal=3)
        self.assertFalse(form.is_valid())
        self.assertIn("Only 2", str(form.errors))

    def test_a_type_outside_its_window_has_no_field_at_all(self):
        """
        Belt and braces: a posted quantity for a closed type is ignored rather than
        honoured, because the field does not exist on the form.
        """
        make_type("Later", sales_start_at=timezone.now() + timedelta(days=1))
        form = PurchaseForm(
            {"Personal": 1, "Later": 5}, conference_year=YEAR, user=self.user
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertNotIn("Later", form.fields)
        ticket, total = form.save(self.user)
        self.assertEqual(total, Decimal("20000"))
        self.assertEqual(Ticket.objects.count(), 1)

    def test_a_percentage_coupon_is_recorded_on_the_order(self):
        Coupon.objects.create(
            code="TEN", percentage=10, conference_year=YEAR, max_usage=10
        )
        form = self._post(Personal=2, coupon="ten")
        self.assertTrue(form.is_valid(), form.errors)
        ticket, total = form.save(self.user)
        self.assertEqual(ticket.discount_amount, Decimal("4000.00"))
        self.assertEqual(ticket.amount, Decimal("36000.00"))
        self.assertEqual(total, Decimal("36000.00"))
        self.assertEqual(ticket.coupon.code, "TEN")

    def test_a_fixed_coupon_is_recorded_on_the_order(self):
        Coupon.objects.create(
            code="FIVEK", discount_type=Coupon.FIXED, amount=Decimal("5000"),
            conference_year=YEAR, max_usage=10,
        )
        form = self._post(coupon="FIVEK")
        self.assertTrue(form.is_valid(), form.errors)
        ticket, total = form.save(self.user)
        self.assertEqual(ticket.discount_amount, Decimal("5000"))
        self.assertEqual(total, Decimal("15000"))

    def test_a_restricted_coupon_discounts_only_its_own_type(self):
        student = make_type("Student", price=Decimal("5000"), regular_count=10)
        coupon = Coupon.objects.create(
            code="STU", percentage=50, conference_year=YEAR, max_usage=10
        )
        coupon.ticket_types.add(student)

        form = PurchaseForm(
            {"Personal": 1, "Student": 1, "coupon": "STU"},
            conference_year=YEAR, user=self.user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save(self.user)
        by_type = {t.ticket_type.name: t for t in Ticket.objects.all()}
        self.assertEqual(by_type["Student"].discount_amount, Decimal("2500.00"))
        self.assertEqual(by_type["Personal"].discount_amount, Decimal("0"))

    def test_an_invalid_code_leaves_the_order_intact_at_full_price(self):
        form = self._post(coupon="NOSUCHCODE")
        self.assertTrue(form.is_valid(), form.errors)
        ticket, total = form.save(self.user)
        self.assertEqual(total, Decimal("20000"))
        self.assertIsNone(ticket.coupon)

    def test_a_concession_rate_demands_its_evidence(self):
        make_type(
            "Student",
            price=Decimal("5000"),
            regular_count=10,
            requires_verification=True,
            verification_prompt="Your institution and student ID",
        )
        form = PurchaseForm({"Student": 1}, conference_year=YEAR, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn("institution", str(form.errors))

        ok = PurchaseForm(
            {"Student": 1, "verification_reference": "Unilag, 123456"},
            conference_year=YEAR, user=self.user,
        )
        self.assertTrue(ok.is_valid(), ok.errors)
        ticket, _ = ok.save(self.user)
        self.assertEqual(ticket.verification_status, Ticket.VERIFICATION_PENDING)
        self.assertEqual(ticket.verification_reference, "Unilag, 123456")

    def test_a_rate_that_needs_no_checking_is_not_flagged(self):
        form = self._post()
        self.assertTrue(form.is_valid(), form.errors)
        ticket, _ = form.save(self.user)
        self.assertEqual(ticket.verification_status, Ticket.VERIFICATION_NOT_REQUIRED)

    def test_company_details_are_collected_without_being_required(self):
        form = self._post(company_name="Acme Ltd", buyer_tax_id="TIN-1")
        self.assertTrue(form.is_valid(), form.errors)
        form.save(self.user)
        self.assertEqual(form.billing_details()["company_name"], "Acme Ltd")

        plain = self._post()
        self.assertTrue(plain.is_valid(), plain.errors)
        self.assertEqual(plain.billing_details()["company_name"], "")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class IssuingTests(TestCase):
    """Tickets for people who should not pay: speakers, volunteers, grants, sponsors."""

    @classmethod
    def setUpTestData(cls):
        make_edition()

    def setUp(self):
        invalidate()
        mail.outbox = []
        self.user = make_user()

    def test_a_complimentary_ticket_is_paid_and_has_a_place(self):
        """
        Paid, because there is nothing to pay. An issued one would sit in the
        purchase flow waiting for money that never comes, and admit nobody.
        """
        ticket, created = issue_complimentary_ticket(
            user=self.user, year=YEAR, reason=REASON_SPEAKER
        )
        self.assertTrue(created)
        self.assertEqual(ticket.status, Ticket.PAID)
        self.assertEqual(ticket.amount, Decimal("0"))
        self.assertTrue(ticket.is_complimentary)
        self.assertEqual(ticket.ticket_sales.count(), 1)
        self.assertTrue(ticket.ticket_sales.first().checkin_code)

    def test_issuing_twice_for_the_same_reason_is_a_no_op(self):
        first, created_first = issue_complimentary_ticket(
            user=self.user, year=YEAR, reason=REASON_SPEAKER
        )
        second, created_second = issue_complimentary_ticket(
            user=self.user, year=YEAR, reason=REASON_SPEAKER
        )
        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Ticket.objects.count(), 1)

    def test_two_different_reasons_are_two_tickets(self):
        """A speaker who also won a grant is eligible twice; that is not a bug."""
        issue_complimentary_ticket(user=self.user, year=YEAR, reason=REASON_SPEAKER)
        issue_complimentary_ticket(user=self.user, year=YEAR, reason=REASON_GRANT)
        self.assertEqual(Ticket.objects.count(), 2)

    def test_the_recipient_is_told(self):
        issue_complimentary_ticket(user=self.user, year=YEAR, reason=REASON_SPEAKER)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["ada@example.com"])

    def test_notification_can_be_suppressed_for_a_backfill(self):
        issue_complimentary_ticket(
            user=self.user, year=YEAR, reason=REASON_SPEAKER, notify=False
        )
        self.assertEqual(len(mail.outbox), 0)

    def test_an_unknown_reason_is_refused(self):
        with self.assertRaises(IssueError):
            issue_complimentary_ticket(user=self.user, year=YEAR, reason="because")

    def test_the_complimentary_type_is_created_on_demand_and_never_for_sale(self):
        ticket_type = complimentary_type(YEAR)
        self.assertEqual(ticket_type.availability, TicketType.INVITE)
        self.assertEqual(ticket_type.price, Decimal("0"))
        self.assertFalse(ticket_type.is_on_sale)
        # And only ever one of them.
        self.assertEqual(complimentary_type(YEAR).pk, ticket_type.pk)

    def test_a_complimentary_ticket_does_not_appear_on_the_purchase_page(self):
        issue_complimentary_ticket(user=self.user, year=YEAR, reason=REASON_SPEAKER)
        names = set(
            TicketType.objects.on_sale().for_year(YEAR).values_list("name", flat=True)
        )
        self.assertNotIn("Complimentary", names)

    def test_confirmed_speakers_are_found_and_issued(self):
        from cfp.models import CFPSettings, Proposal, Speaker

        CFPSettings.objects.create(
            conference_year=YEAR,
            status=CFPSettings.STATUS_OPEN,
            submission_deadline=timezone.now() + timedelta(days=5),
        )
        speaker = Speaker.objects.create(
            user=self.user, full_name="Ada Lovelace", conference_year=YEAR
        )
        Proposal.objects.create(
            speaker=speaker,
            title="A talk",
            abstract="About things.",
            description="At length.",
            format=Proposal.FORMAT_CHOICES[0][0],
            duration=30,
            audience_level=Proposal.AUDIENCE_CHOICES[0][0],
            status=Proposal.STATUS_CONFIRMED,
            conference_year=YEAR,
        )
        issued, skipped = issue_for_reason(REASON_SPEAKER, YEAR)
        self.assertEqual([u.pk for u in issued], [self.user.pk])
        self.assertEqual(skipped, [])

        # Running again issues nothing further.
        issued_again, skipped_again = issue_for_reason(REASON_SPEAKER, YEAR)
        self.assertEqual(issued_again, [])
        self.assertEqual([u.pk for u in skipped_again], [self.user.pk])

    def test_an_accepted_but_unconfirmed_speaker_gets_nothing_yet(self):
        """
        Accepted is not confirmed. An accepted speaker who never replies has their
        slot released, and issuing a ticket meanwhile means chasing it back.
        """
        from cfp.models import CFPSettings, Proposal, Speaker

        CFPSettings.objects.create(
            conference_year=YEAR,
            status=CFPSettings.STATUS_OPEN,
            submission_deadline=timezone.now() + timedelta(days=5),
        )
        speaker = Speaker.objects.create(
            user=self.user, full_name="Ada Lovelace", conference_year=YEAR
        )
        Proposal.objects.create(
            speaker=speaker,
            title="A talk",
            abstract="About things.",
            description="At length.",
            format=Proposal.FORMAT_CHOICES[0][0],
            duration=30,
            audience_level=Proposal.AUDIENCE_CHOICES[0][0],
            status=Proposal.STATUS_ACCEPTED,
            conference_year=YEAR,
        )
        issued, _ = issue_for_reason(REASON_SPEAKER, YEAR)
        self.assertEqual(issued, [])

    def test_volunteers_come_from_their_role_assignment(self):
        from accounts.models import RoleAssignment
        from accounts.roles import Role

        RoleAssignment.objects.create(
            user=self.user, role=Role.VOLUNTEER.value, conference_year=YEAR
        )
        issued, _ = issue_for_reason("volunteer", YEAR)
        self.assertEqual([u.pk for u in issued], [self.user.pk])

    def test_a_dry_run_writes_nothing(self):
        from accounts.models import RoleAssignment
        from accounts.roles import Role

        RoleAssignment.objects.create(
            user=self.user, role=Role.VOLUNTEER.value, conference_year=YEAR
        )
        issued, _ = issue_for_reason("volunteer", YEAR, dry_run=True)
        self.assertEqual(len(issued), 1)
        self.assertEqual(Ticket.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SponsorAllocationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_edition()

    def setUp(self):
        invalidate()
        mail.outbox = []
        from sponsors.models import Sponsor, SponsorTier

        tier = SponsorTier.objects.create(name="Gold", slug="gold", display_order=1)
        self.sponsor = Sponsor.objects.create(
            name="Acme Ltd",
            tier=tier,
            conference_year=YEAR,
            tickets_allocated=2,
            tickets_claimed=0,
        )

    def test_issuing_against_an_allocation_counts_it(self):
        first = make_user("one@example.com")
        issue_sponsor_ticket(sponsor=self.sponsor, user=first)
        self.sponsor.refresh_from_db()
        self.assertEqual(self.sponsor.tickets_claimed, 1)
        self.assertEqual(self.sponsor.tickets_outstanding, 1)

    def test_an_exhausted_allocation_is_refused_rather_than_overspent(self):
        issue_sponsor_ticket(sponsor=self.sponsor, user=make_user("one@example.com"))
        issue_sponsor_ticket(sponsor=self.sponsor, user=make_user("two@example.com"))
        self.sponsor.refresh_from_db()
        self.assertEqual(self.sponsor.tickets_outstanding, 0)

        with self.assertRaises(IssueError) as caught:
            issue_sponsor_ticket(sponsor=self.sponsor, user=make_user("three@example.com"))
        self.assertIn("no tickets left", str(caught.exception))

    def test_the_ticket_points_back_at_the_sponsor(self):
        ticket, _ = issue_sponsor_ticket(
            sponsor=self.sponsor, user=make_user("one@example.com")
        )
        self.assertEqual(ticket.issued_for_sponsor, self.sponsor)
        self.assertEqual(ticket.complimentary_reason, REASON_SPONSOR)

    def test_issuing_twice_to_one_person_does_not_double_count(self):
        user = make_user("one@example.com")
        issue_sponsor_ticket(sponsor=self.sponsor, user=user)
        issue_sponsor_ticket(sponsor=self.sponsor, user=user)
        self.sponsor.refresh_from_db()
        self.assertEqual(self.sponsor.tickets_claimed, 1)


class InvoiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_edition()

    def setUp(self):
        invalidate()
        self.user = make_user()
        self.ticket_type = make_type()

    def test_numbers_run_in_sequence_within_an_edition(self):
        self.assertEqual(next_number(YEAR), f"PYNG-{YEAR}-0001")
        issue_invoice(make_paid_order(self.user, self.ticket_type))
        self.assertEqual(next_number(YEAR), f"PYNG-{YEAR}-0002")

    def test_the_prefix_comes_from_the_edition_settings(self):
        settings_obj = TicketSettings.for_year(YEAR)
        settings_obj.invoice_prefix = "NGPY"
        settings_obj.save()
        self.assertEqual(next_number(YEAR), f"NGPY-{YEAR}-0001")

    def test_one_invoice_per_order(self):
        ticket = make_paid_order(self.user, self.ticket_type)
        first, created_first = issue_invoice(ticket)
        second, created_second = issue_invoice(ticket)
        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Invoice.objects.count(), 1)

    def test_the_figures_are_copied_and_add_up(self):
        ticket = make_paid_order(
            self.user, self.ticket_type, quantity=2, amount=Decimal("36000"), discount=4000
        )
        invoice, _ = issue_invoice(ticket)
        self.assertEqual(invoice.total, Decimal("36000"))
        self.assertEqual(invoice.discount, Decimal("4000"))
        self.assertEqual(invoice.subtotal, Decimal("40000"))
        self.assertEqual(invoice.subtotal - invoice.discount, invoice.total)

    def test_a_company_invoice_is_addressed_to_the_company(self):
        invoice, _ = issue_invoice(
            make_paid_order(self.user, self.ticket_type),
            company_name="Acme Ltd",
            company_address="1 Broad Street, Lagos",
            buyer_tax_id="TIN-1",
        )
        self.assertEqual(invoice.addressee, "Acme Ltd")
        self.assertEqual(invoice.buyer_email, self.user.email)

    def test_an_individual_invoice_is_addressed_to_the_person(self):
        invoice, _ = issue_invoice(make_paid_order(self.user, self.ticket_type))
        self.assertEqual(invoice.addressee, invoice.buyer_name)

    def test_the_figures_survive_a_later_price_change(self):
        """An invoice is a statement about a moment, not a view over current prices."""
        ticket = make_paid_order(self.user, self.ticket_type)
        invoice, _ = issue_invoice(ticket)
        self.ticket_type.price = Decimal("99999")
        self.ticket_type.save()
        invoice.refresh_from_db()
        self.assertEqual(invoice.total, Decimal("20000"))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class RefundTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_edition()

    def setUp(self):
        invalidate()
        mail.outbox = []
        self.user = make_user()
        self.ticket_type = make_type(regular_count=5)
        self.ticket = make_paid_order(self.user, self.ticket_type)
        self.settings_obj = TicketSettings.for_year(YEAR)
        self.settings_obj.refund_deadline = timezone.now() + timedelta(days=10)
        self.settings_obj.refund_policy = "Full refund up to two weeks before."
        self.settings_obj.save()

    def test_refundable_when_paid_and_within_the_deadline(self):
        self.assertEqual(self.ticket.refund_state(), "refundable")

    def test_no_deadline_means_no_refunds_offered(self):
        self.settings_obj.refund_deadline = None
        self.settings_obj.save()
        self.assertEqual(self.ticket.refund_state(), "no_policy")

    def test_past_the_deadline_says_so(self):
        self.settings_obj.refund_deadline = timezone.now() - timedelta(days=1)
        self.settings_obj.save()
        self.assertEqual(self.ticket.refund_state(), "deadline_passed")

    def test_an_unpaid_order_has_nothing_to_refund(self):
        self.ticket.status = Ticket.ISSUED
        self.ticket.save()
        self.assertEqual(self.ticket.refund_state(), "not_paid")

    def test_a_complimentary_ticket_has_nothing_to_refund(self):
        ticket, _ = issue_complimentary_ticket(
            user=make_user("free@example.com"), year=YEAR, reason=REASON_SPEAKER
        )
        self.assertEqual(ticket.refund_state(), "complimentary")

    def test_the_suggested_amount_applies_the_retained_fee(self):
        self.settings_obj.refund_fee_percentage = 10
        self.settings_obj.save()
        self.assertEqual(self.ticket.suggested_refund(), Decimal("18000.00"))

    def test_the_full_flow_releases_the_place(self):
        before = self.ticket_type.remaining_count
        refund = request_refund(
            self.ticket, reason="Cannot travel", requested_by=self.user
        )
        self.assertEqual(refund.status, Refund.REQUESTED)
        self.assertIsNone(refund.decided_at)

        decide_refund(refund, approve=True, decided_by=self.user)
        refund.refresh_from_db()
        self.assertEqual(refund.status, Refund.APPROVED)
        self.assertIsNotNone(refund.decided_at)
        self.assertIsNone(refund.paid_at)

        mark_paid(refund, provider_reference="TRF-1", paid_by=self.user)
        refund.refresh_from_db()
        self.ticket.refresh_from_db()
        self.assertEqual(refund.status, Refund.PAID)
        self.assertIsNotNone(refund.paid_at)
        self.assertEqual(self.ticket.status, Ticket.REFUNDED)
        self.assertEqual(self.ticket_type.remaining_count, before + 1)

    def test_the_buyer_is_told_when_the_money_goes_back(self):
        refund = request_refund(self.ticket, reason="Cannot travel")
        mail.outbox = []
        mark_paid(refund, provider_reference="TRF-1")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])

    def test_a_partial_refund_can_keep_the_place(self):
        refund = request_refund(
            self.ticket, amount=Decimal("5000"), reason="Downgrade", force=True
        )
        mark_paid(refund, release_place=False)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, Ticket.PAID)
        self.assertEqual(self.ticket.refunded_amount, Decimal("5000"))
        self.assertEqual(self.ticket.refundable_amount, Decimal("15000"))

    def test_refunding_more_than_was_paid_is_refused(self):
        with self.assertRaises(RefundError):
            request_refund(self.ticket, amount=Decimal("99999"), reason="Oops")

    def test_a_request_outside_the_policy_is_refused_but_can_be_forced(self):
        self.settings_obj.refund_deadline = timezone.now() - timedelta(days=1)
        self.settings_obj.save()
        with self.assertRaises(RefundError):
            request_refund(self.ticket, reason="Late")
        refund = request_refund(self.ticket, reason="Exception agreed", force=True)
        self.assertEqual(refund.status, Refund.REQUESTED)

    def test_a_rejected_refund_cannot_be_marked_paid(self):
        refund = request_refund(self.ticket, reason="Cannot travel")
        decide_refund(refund, approve=False)
        with self.assertRaises(RefundError):
            mark_paid(refund)

    def test_a_paid_refund_cannot_be_decided_again(self):
        refund = request_refund(self.ticket, reason="Cannot travel")
        mark_paid(refund)
        with self.assertRaises(RefundError):
            decide_refund(refund, approve=False)

    def test_paystack_is_not_called_for_an_order_it_never_took(self):
        from tickets.refunds import refund_through_paystack

        refund = request_refund(self.ticket, reason="Cannot travel")
        with self.assertRaises(RefundError) as caught:
            refund_through_paystack(refund)
        self.assertIn("no Paystack reference", str(caught.exception))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class WaitlistTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_edition()

    def setUp(self):
        invalidate()
        mail.outbox = []
        self.ticket_type = make_type(regular_count=1)
        self.buyer = make_user("buyer@example.com")

    def test_joining_twice_keeps_one_place_in_the_queue(self):
        first, created_first = waitlist_service.join(
            email="wants@example.com", ticket_type=self.ticket_type, year=YEAR
        )
        second, created_second = waitlist_service.join(
            email="WANTS@example.com", ticket_type=self.ticket_type, year=YEAR
        )
        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(TicketWaitlistEntry.objects.count(), 1)

    def test_an_entry_for_any_type_is_told_about_a_specific_one(self):
        waitlist_service.join(email="any@example.com", ticket_type=None, year=YEAR)
        waiting = waitlist_service.waiting_for(self.ticket_type, YEAR)
        self.assertEqual([e.email for e in waiting], ["any@example.com"])

    def test_nobody_is_told_while_the_type_is_sold_out(self):
        make_paid_order(self.buyer, self.ticket_type)
        waitlist_service.join(email="wants@example.com", ticket_type=self.ticket_type, year=YEAR)
        self.assertEqual(self.ticket_type.remaining_count, 0)
        self.assertEqual(waitlist_service.notify_available(self.ticket_type), [])
        self.assertEqual(len(mail.outbox), 0)

    def test_only_as_many_are_told_as_there_are_places(self):
        """Telling ten people about one ticket is how a waitlist loses trust."""
        self.ticket_type.regular_count = 3
        self.ticket_type.save()
        make_paid_order(self.buyer, self.ticket_type, quantity=2)
        for index in range(5):
            waitlist_service.join(
                email=f"person{index}@example.com",
                ticket_type=self.ticket_type,
                year=YEAR,
            )
        told = waitlist_service.notify_available(self.ticket_type)
        self.assertEqual(len(told), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_the_queue_is_ordered_by_when_they_asked(self):
        for index in range(3):
            waitlist_service.join(
                email=f"person{index}@example.com",
                ticket_type=self.ticket_type,
                year=YEAR,
            )
        told = waitlist_service.notify_available(self.ticket_type, limit=1)
        self.assertEqual([e.email for e in told], ["person0@example.com"])

    def test_nobody_is_told_twice(self):
        waitlist_service.join(email="wants@example.com", ticket_type=self.ticket_type, year=YEAR)
        waitlist_service.notify_available(self.ticket_type)
        mail.outbox = []
        self.assertEqual(waitlist_service.notify_available(self.ticket_type), [])
        self.assertEqual(len(mail.outbox), 0)

    def test_a_dry_run_sends_nothing_and_marks_nothing(self):
        waitlist_service.join(email="wants@example.com", ticket_type=self.ticket_type, year=YEAR)
        told = waitlist_service.notify_available(self.ticket_type, dry_run=True)
        self.assertEqual(len(told), 1)
        self.assertEqual(len(mail.outbox), 0)
        self.assertTrue(TicketWaitlistEntry.objects.first().is_waiting)

    def test_buying_closes_the_entry(self):
        waitlist_service.join(email="buyer@example.com", ticket_type=self.ticket_type, year=YEAR)
        waitlist_service.mark_converted("buyer@example.com", YEAR)
        self.assertIsNotNone(TicketWaitlistEntry.objects.first().converted_at)
        self.assertFalse(TicketWaitlistEntry.objects.first().is_waiting)

    def test_joining_twice_for_any_type_also_keeps_one_place(self):
        """
        The case a single unique constraint misses. NULL is not equal to NULL, so
        "any ticket type" entries slipped past it and the same person could be
        emailed every time a place opened up.
        """
        first, created_first = waitlist_service.join(
            email="any@example.com", ticket_type=None, year=YEAR
        )
        second, created_second = waitlist_service.join(
            email="any@example.com", ticket_type=None, year=YEAR
        )
        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(TicketWaitlistEntry.objects.count(), 1)

    def test_the_same_person_may_wait_for_two_different_types(self):
        other = make_type("Student", price=Decimal("5000"))
        waitlist_service.join(email="a@example.com", ticket_type=self.ticket_type, year=YEAR)
        waitlist_service.join(email="a@example.com", ticket_type=other, year=YEAR)
        waitlist_service.join(email="a@example.com", ticket_type=None, year=YEAR)
        self.assertEqual(TicketWaitlistEntry.objects.count(), 3)

    def test_an_entry_needs_an_address(self):
        with self.assertRaises(ValueError):
            waitlist_service.join(email="", ticket_type=None, year=YEAR)


class CheckinTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_edition()

    def setUp(self):
        invalidate()
        self.user = make_user()
        self.ticket_type = make_type()
        self.ticket = make_paid_order(self.user, self.ticket_type)
        self.place = self.ticket.ticket_sales.first()
        self.volunteer = make_user("door@example.com")

    def test_every_place_gets_a_code(self):
        self.assertEqual(len(self.place.checkin_code), 10)
        self.assertTrue(
            all(c in "ABCDEFGHJKMNPRSTUVWXY23479" for c in self.place.checkin_code)
        )

    def test_codes_are_unique_across_many_places(self):
        codes = {self.place.checkin_code}
        for index in range(20):
            sale = TicketSale.objects.create(
                ticket=self.ticket, user=self.user, full_name=f"Person {index}"
            )
            codes.add(sale.checkin_code)
        self.assertEqual(len(codes), 21)

    def test_a_code_is_read_tolerantly(self):
        """It comes off a badge, out of a URL, and through somebody's fingers."""
        code = self.place.checkin_code
        for variant in (
            code.lower(),
            f"  {code}  ",
            f"{code[:5]}-{code[5:]}",
            f"{code[:5]} {code[5:]}",
            f"https://pycon.ng/tickets/c/{code}/",
            f"HTTPS://PYCON.NG/TICKETS/C/{code}",
        ):
            with self.subTest(variant=variant):
                self.assertEqual(checkin_service.normalise(variant), code)
                self.assertEqual(checkin_service.find(variant), self.place)

    def test_an_unknown_code_is_not_found(self):
        outcome, sale = checkin_service.inspect("ZZZZZZZZZZ")
        self.assertEqual(outcome, checkin_service.NOT_FOUND)
        self.assertIsNone(sale)

    def test_checking_in_records_who_and_when(self):
        outcome, sale = checkin_service.check_in(
            self.place.checkin_code, by=self.volunteer
        )
        self.assertEqual(outcome, checkin_service.OK)
        self.place.refresh_from_db()
        self.assertTrue(self.place.is_checked_in)
        self.assertEqual(self.place.checked_in_by, self.volunteer)

    def test_a_second_scan_is_refused_rather_than_accepted(self):
        """Usually two people trying to use one ticket, which is worth saying out loud."""
        checkin_service.check_in(self.place.checkin_code, by=self.volunteer)
        outcome, sale = checkin_service.check_in(
            self.place.checkin_code, by=self.volunteer
        )
        self.assertEqual(outcome, checkin_service.ALREADY_CHECKED_IN)
        self.assertEqual(sale.pk, self.place.pk)

    def test_an_unpaid_or_refunded_order_does_not_admit_anybody(self):
        self.ticket.status = Ticket.REFUNDED
        self.ticket.save()
        outcome, _ = checkin_service.check_in(self.place.checkin_code)
        self.assertEqual(outcome, checkin_service.NOT_PAID)
        self.place.refresh_from_db()
        self.assertFalse(self.place.is_checked_in)

    def test_check_in_can_be_held_shut_until_the_day(self):
        settings_obj = TicketSettings.for_year(YEAR)
        settings_obj.checkin_opens_at = timezone.now() + timedelta(days=1)
        settings_obj.save()
        outcome, _ = checkin_service.check_in(self.place.checkin_code)
        self.assertEqual(outcome, checkin_service.TOO_EARLY)

    def test_a_refused_scan_writes_nothing(self):
        checkin_service.check_in("NOSUCHCODE")
        self.place.refresh_from_db()
        self.assertFalse(self.place.is_checked_in)

    def test_a_check_in_can_be_undone(self):
        checkin_service.check_in(self.place.checkin_code, by=self.volunteer)
        self.place.refresh_from_db()
        checkin_service.undo(self.place, by=self.volunteer)
        self.place.refresh_from_db()
        self.assertFalse(self.place.is_checked_in)
        self.assertIsNone(self.place.checked_in_by)

    def test_the_qr_encodes_the_code(self):
        from tickets import qr
        from tickets.tests_qr import decode

        svg = checkin_service.qr_svg(self.place)
        self.assertIn("<svg", svg)
        matrix = qr.encode(checkin_service.checkin_url(self.place))
        self.assertIn(self.place.checkin_code, decode(matrix))

    def test_the_checkin_url_is_entirely_qr_alphanumeric(self):
        """
        Which is what keeps the symbol small enough to print on a badge. A lowercase
        character would force byte mode and a bigger version.
        """
        from tickets import qr

        url = checkin_service.checkin_url(self.place)
        self.assertTrue(qr.can_encode(url), url)


class GroupPurchaseTests(TestCase):
    """A buyer paying for colleagues, which used to leave them with nothing."""

    @classmethod
    def setUpTestData(cls):
        make_edition()

    def setUp(self):
        invalidate()
        self.buyer = make_user("buyer@example.com")
        self.ticket_type = make_type()

    def test_a_place_can_be_named_before_its_owner_has_an_account(self):
        ticket = make_paid_order(self.buyer, self.ticket_type, places=False)
        ticket.quantity = 1
        ticket.save()
        place = TicketSale.objects.create(
            ticket=ticket,
            user=None,
            attendee_email="colleague@example.com",
            full_name="Grace Hopper",
        )
        self.assertIsNone(place.user)
        self.assertEqual(place.owner_email, "colleague@example.com")

    def test_signing_up_claims_the_place(self):
        ticket = make_paid_order(self.buyer, self.ticket_type, places=False)
        place = TicketSale.objects.create(
            ticket=ticket,
            user=None,
            attendee_email="colleague@example.com",
            full_name="Grace Hopper",
        )
        colleague = make_user("colleague@example.com")
        place.refresh_from_db()
        self.assertEqual(place.user, colleague)

    def test_signing_up_does_not_take_a_place_somebody_already_holds(self):
        """A transfer must not be undone by somebody changing their email back."""
        ticket = make_paid_order(self.buyer, self.ticket_type)
        place = ticket.ticket_sales.first()
        place.attendee_email = "colleague@example.com"
        place.save()
        self.assertEqual(place.user, self.buyer)

        make_user("colleague@example.com")
        place.refresh_from_db()
        self.assertEqual(place.user, self.buyer)

    def test_the_assignment_form_points_the_place_at_the_named_colleague(self):
        from tickets.forms import TicketCreateForm

        colleague = make_user("colleague@example.com")
        ticket = make_paid_order(self.buyer, self.ticket_type, places=False)
        form = TicketCreateForm(
            {
                "full_name": "Grace Hopper",
                "attendee_email": "colleague@example.com",
                "diet": "Omnivorous",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save(ticket)
        place = ticket.ticket_sales.get()
        self.assertEqual(place.user, colleague)
        self.assertEqual(place.attendee_email, "colleague@example.com")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class TransferTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_edition()

    def setUp(self):
        invalidate()
        mail.outbox = []
        self.buyer = make_user("buyer@example.com")
        self.ticket_type = make_type()
        self.ticket = make_paid_order(self.buyer, self.ticket_type)
        self.place = self.ticket.ticket_sales.first()

    def test_a_transfer_records_where_the_place_came_from(self):
        form = TicketTransferForm(
            {"email": "new@example.com"}, ticket_sale=self.place
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.place.refresh_from_db()
        self.assertEqual(self.place.attendee_email, "new@example.com")
        self.assertEqual(self.place.transferred_from_email, "buyer@example.com")
        self.assertIsNotNone(self.place.transferred_at)
        self.assertEqual(len(mail.outbox), 1)

    def test_a_transfer_to_somebody_without_an_account_is_allowed(self):
        form = TicketTransferForm(
            {"email": "stranger@example.com"}, ticket_sale=self.place
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.place.refresh_from_db()
        self.assertIsNone(self.place.user)
        self.assertEqual(self.place.owner_email, "stranger@example.com")

    def test_the_deadline_closes_transfers(self):
        """Badges are printed from these names; a late change is a person turned away."""
        settings_obj = TicketSettings.for_year(YEAR)
        settings_obj.transfer_deadline = timezone.now() - timedelta(days=1)
        settings_obj.save()

        form = TicketTransferForm({"email": "new@example.com"}, ticket_sale=self.place)
        self.assertFalse(form.is_valid())
        self.assertIn("closed", str(form.errors))

    def test_transferring_to_the_current_holder_is_refused(self):
        form = TicketTransferForm({"email": "buyer@example.com"}, ticket_sale=self.place)
        self.assertFalse(form.is_valid())
        self.assertIn("already the holder", str(form.errors))


class SettingsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_edition()

    def setUp(self):
        invalidate()

    def test_settings_appear_on_demand_with_safe_defaults(self):
        """
        Every caller would otherwise need the same "if settings is None" branch, and
        the defaults are the cautious policy: transfers open, no refunds promised.
        """
        self.assertFalse(TicketSettings.objects.filter(conference_year=YEAR).exists())
        settings_obj = TicketSettings.for_year(YEAR)
        self.assertTrue(settings_obj.transfers_open)
        self.assertFalse(settings_obj.refunds_open)
        self.assertTrue(settings_obj.waitlist_enabled)
        self.assertTrue(settings_obj.checkin_open)
        # And only one row per year.
        self.assertEqual(TicketSettings.for_year(YEAR).pk, settings_obj.pk)
