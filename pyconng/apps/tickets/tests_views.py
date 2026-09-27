"""
View-level tests for ticketing.

Covers the things a page can get wrong that a service cannot: who is allowed to see
an order, what the purchase page offers when nothing is on sale, and whether the
door screen admits somebody as a side effect of loading a URL. The new pages are
also run through the module 1 accessibility audit, since a ticket page read on a
phone at a venue is exactly where that matters.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from wagtail.models import Page, Site

from accounts.models import RoleAssignment
from accounts.roles import Role
from editions.current import invalidate
from editions.models import Edition
from quality.audit import audit_html, errors
from tickets import checkin as checkin_service
from tickets.models import (
    Coupon,
    Refund,
    Ticket,
    TicketSale,
    TicketSettings,
    TicketType,
    TicketWaitlistEntry,
)

User = get_user_model()
YEAR = 2026


def build_site():
    from home.models import HomePage

    Edition.objects.update_or_create(
        year=YEAR,
        defaults={
            "name": f"PyCon Nigeria {YEAR}",
            "theme": "2026",
            "is_current": True,
            "is_published": True,
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


def make_type(name="Personal", **kwargs):
    defaults = {
        "conference_year": YEAR,
        "price": Decimal("20000"),
        "regular_count": 20,
        "is_active": True,
    }
    defaults.update(kwargs)
    return TicketType.objects.create(name=name, **defaults)


def make_paid_order(user, ticket_type, quantity=1):
    from tickets.utils import generate_order_code

    amount = Decimal(ticket_type.price) * quantity
    ticket = Ticket.objects.create(
        order=generate_order_code(),
        user=user,
        ticket_type=ticket_type,
        quantity=quantity,
        amount=amount,
        total_amount=amount,
        status=Ticket.PAID,
        date_paid=timezone.now(),
        conference_year=YEAR,
        created_tickets=True,
    )
    for index in range(quantity):
        TicketSale.objects.create(
            ticket=ticket,
            user=user,
            attendee_email=user.email,
            full_name=f"Attendee {index + 1}",
        )
    return ticket


def assert_accessible(test, html):
    found = errors(audit_html(html, internal_hosts=("testserver",)))
    test.assertEqual(found, [], "\n".join(str(f) for f in found))


class TicketListingViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        build_site()

    def setUp(self):
        invalidate()

    def test_a_type_not_yet_on_sale_is_shown_with_its_date(self):
        """
        It used to be omitted entirely, which reads as "this ticket does not exist"
        rather than "it goes on sale on Friday".
        """
        make_type("Later", sales_start_at=timezone.now() + timedelta(days=3))
        response = self.client.get("/tickets/")
        self.assertContains(response, "Later")
        self.assertContains(response, "Not on sale yet")
        self.assertContains(response, "On sale from")

    def test_a_closed_type_says_when_it_closed(self):
        make_type("Over", sales_end_at=timezone.now() - timedelta(days=1))
        response = self.client.get("/tickets/")
        self.assertContains(response, "Sales closed")

    def test_an_invite_only_type_is_not_listed_at_all(self):
        make_type("Comp", availability=TicketType.INVITE)
        response = self.client.get("/tickets/")
        self.assertNotContains(response, "Comp")

    def test_the_waitlist_appears_once_something_sells_out(self):
        ticket_type = make_type("Personal", regular_count=1)
        buyer = User.objects.create_user(username="b", email="b@example.com", password="x")
        make_paid_order(buyer, ticket_type)

        response = self.client.get("/tickets/")
        self.assertContains(response, "Join the waiting list")
        self.assertContains(response, "not a reservation")

    def test_no_waitlist_offered_while_tickets_remain(self):
        make_type("Personal")
        response = self.client.get("/tickets/")
        self.assertNotContains(response, "Join the waiting list")

    def test_the_waitlist_can_be_switched_off(self):
        ticket_type = make_type("Personal", regular_count=1)
        buyer = User.objects.create_user(username="b", email="b@example.com", password="x")
        make_paid_order(buyer, ticket_type)
        settings_obj = TicketSettings.for_year(YEAR)
        settings_obj.waitlist_enabled = False
        settings_obj.save()

        response = self.client.get("/tickets/")
        self.assertNotContains(response, "Join the waiting list")

    def test_the_refund_policy_is_shown_where_people_buy(self):
        make_type("Personal")
        settings_obj = TicketSettings.for_year(YEAR)
        settings_obj.refund_policy = "Full refund up to two weeks before."
        settings_obj.save()
        self.assertContains(self.client.get("/tickets/"), "Full refund up to two weeks")

    def test_the_page_has_no_accessibility_errors(self):
        make_type("Personal", regular_count=1)
        buyer = User.objects.create_user(username="b", email="b@example.com", password="x")
        make_paid_order(buyer, make_type("Student", price=Decimal("5000"), regular_count=1))
        assert_accessible(self, self.client.get("/tickets/").content.decode())


class PurchaseViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        build_site()
        cls.user = User.objects.create_user(
            username="ada", email="ada@example.com", password="secret"
        )

    def setUp(self):
        invalidate()
        self.client.force_login(self.user)

    def test_only_types_on_sale_can_be_selected(self):
        make_type("Personal")
        make_type("Later", sales_start_at=timezone.now() + timedelta(days=1))
        response = self.client.get("/tickets/purchase/")
        names = [row["short_name"] for row in response.context["tickets"]]
        self.assertEqual(names, ["Personal"])

    def test_the_quantity_options_stop_at_the_cap_and_the_stock(self):
        make_type("Personal", max_per_order=3, regular_count=2)
        response = self.client.get("/tickets/purchase/")
        self.assertEqual(response.context["tickets"][0]["quantity_options"], [0, 1, 2])

    def test_nothing_on_sale_says_so_rather_than_rendering_empty(self):
        response = self.client.get("/tickets/purchase/")
        self.assertContains(response, "No tickets are on sale right now")

    def test_a_concession_prompt_is_shown(self):
        make_type(
            "Student",
            price=Decimal("5000"),
            requires_verification=True,
            verification_prompt="Your institution and student ID",
        )
        response = self.client.get("/tickets/purchase/")
        self.assertContains(response, "Confirm your eligibility")
        self.assertContains(response, "Your institution and student ID")

    def test_posting_an_order_records_the_billing_details_on_the_session(self):
        make_type("Personal")
        response = self.client.post(
            "/tickets/purchase/",
            data={
                "tickets": {"Personal": 1},
                "company_name": "Acme Ltd",
                "company_address": "1 Broad Street",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("order", response.json())
        self.assertEqual(
            self.client.session["tickets_billing_details"]["company_name"], "Acme Ltd"
        )

    def test_an_over_cap_order_comes_back_with_a_readable_error(self):
        make_type("Personal", max_per_order=2)
        response = self.client.post(
            "/tickets/purchase/",
            data={"tickets": {"Personal": 5}},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("At most 2", str(response.json()["error"]))

    def test_the_page_has_no_accessibility_errors(self):
        make_type(
            "Student",
            price=Decimal("5000"),
            requires_verification=True,
            verification_prompt="Your institution and student ID",
        )
        assert_accessible(self, self.client.get("/tickets/purchase/").content.decode())


class CouponEndpointTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        build_site()

    def setUp(self):
        invalidate()

    def test_a_percentage_code(self):
        Coupon.objects.create(code="TEN", percentage=10, conference_year=YEAR, max_usage=5)
        data = self.client.get("/tickets/coupons/?value=ten").json()
        self.assertEqual(data["status"], 10)
        self.assertEqual(data["kind"], "percentage")

    def test_a_fixed_code_is_reported_as_an_amount(self):
        """The old endpoint could only express a percentage, so fixed codes were silent."""
        Coupon.objects.create(
            code="FIVEK",
            discount_type=Coupon.FIXED,
            amount=Decimal("5000"),
            conference_year=YEAR,
            max_usage=5,
        )
        data = self.client.get("/tickets/coupons/?value=FIVEK").json()
        self.assertEqual(data["kind"], "fixed")
        self.assertEqual(data["amount"], 5000.0)
        self.assertEqual(data["label"], "5,000 off")

    def test_an_expired_code_is_refused_with_a_message(self):
        Coupon.objects.create(
            code="GONE",
            percentage=10,
            conference_year=YEAR,
            valid_until=timezone.now() - timedelta(days=1),
        )
        data = self.client.get("/tickets/coupons/?value=GONE").json()
        self.assertEqual(data["status"], 0)
        self.assertIn("not valid", data["message"])

    def test_an_unknown_code_is_refused(self):
        self.assertEqual(self.client.get("/tickets/coupons/?value=NOPE").json()["status"], 0)


class InvoiceViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        build_site()
        cls.buyer = User.objects.create_user(
            username="ada", email="ada@example.com", password="secret",
            first_name="Ada", last_name="Lovelace",
        )
        cls.other = User.objects.create_user(
            username="eve", email="eve@example.com", password="secret"
        )

    def setUp(self):
        invalidate()
        self.ticket_type = make_type()
        self.ticket = make_paid_order(self.buyer, self.ticket_type)

    def test_the_buyer_sees_a_numbered_invoice(self):
        self.client.force_login(self.buyer)
        response = self.client.get(f"/tickets/invoice/{self.ticket.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"PYNG-{YEAR}-0001")
        self.assertContains(response, "Ada Lovelace")

    def test_somebody_else_cannot(self):
        self.client.force_login(self.other)
        self.assertEqual(
            self.client.get(f"/tickets/invoice/{self.ticket.pk}/").status_code, 404
        )

    def test_an_organizer_can(self):
        RoleAssignment.objects.create(
            user=self.other, role=Role.ORGANIZER.value, conference_year=YEAR
        )
        self.client.force_login(self.other)
        self.assertEqual(
            self.client.get(f"/tickets/invoice/{self.ticket.pk}/").status_code, 200
        )

    def test_an_unpaid_order_has_no_invoice(self):
        self.ticket.status = Ticket.ISSUED
        self.ticket.save()
        self.client.force_login(self.buyer)
        response = self.client.get(f"/tickets/invoice/{self.ticket.pk}/", follow=True)
        self.assertContains(response, "only issued once an order is paid")

    def test_company_details_can_be_added_afterwards(self):
        """Buyers routinely discover a fortnight later that finance needs a company name."""
        self.client.force_login(self.buyer)
        response = self.client.post(
            f"/tickets/invoice/{self.ticket.pk}/",
            {
                "company_name": "Acme Ltd",
                "company_address": "1 Broad Street, Lagos",
                "buyer_tax_id": "TIN-1",
                "purchase_order_reference": "PO-9",
            },
            follow=True,
        )
        self.assertContains(response, "Acme Ltd")
        self.assertContains(response, "PO-9")
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.invoice.company_name, "Acme Ltd")
        # The number does not change: it is the same document.
        self.assertEqual(self.ticket.invoice.number, f"PYNG-{YEAR}-0001")

    def test_the_page_has_no_accessibility_errors(self):
        self.client.force_login(self.buyer)
        assert_accessible(
            self, self.client.get(f"/tickets/invoice/{self.ticket.pk}/").content.decode()
        )


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class RefundViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        build_site()
        cls.buyer = User.objects.create_user(
            username="ada", email="ada@example.com", password="secret"
        )

    def setUp(self):
        invalidate()
        self.ticket = make_paid_order(self.buyer, make_type())
        settings_obj = TicketSettings.for_year(YEAR)
        settings_obj.refund_deadline = timezone.now() + timedelta(days=5)
        settings_obj.refund_policy = "Full refund up to two weeks before."
        settings_obj.refund_fee_percentage = 10
        settings_obj.save()
        self.client.force_login(self.buyer)

    def test_the_form_shows_the_policy_and_the_likely_figure(self):
        response = self.client.get(f"/tickets/refund/{self.ticket.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Full refund up to two weeks")
        self.assertContains(response, "18,000")

    def test_a_request_is_recorded_but_pays_nothing(self):
        response = self.client.post(
            f"/tickets/refund/{self.ticket.pk}/",
            {"reason": "I can no longer travel."},
            follow=True,
        )
        self.assertContains(response, "Nothing has been paid back yet")
        refund = Refund.objects.get()
        self.assertEqual(refund.status, Refund.REQUESTED)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, Ticket.PAID)

    def test_past_the_deadline_the_form_is_closed_with_the_reason(self):
        settings_obj = TicketSettings.for_year(YEAR)
        settings_obj.refund_deadline = timezone.now() - timedelta(days=1)
        settings_obj.save()
        response = self.client.get(f"/tickets/refund/{self.ticket.pk}/", follow=True)
        self.assertContains(response, "refund deadline for this edition has passed")
        self.assertEqual(Refund.objects.count(), 0)

    def test_somebody_elses_order_is_not_refundable_by_you(self):
        other = User.objects.create_user(
            username="eve", email="eve@example.com", password="secret"
        )
        self.client.force_login(other)
        self.assertEqual(
            self.client.get(f"/tickets/refund/{self.ticket.pk}/").status_code, 404
        )

    def test_the_page_has_no_accessibility_errors(self):
        assert_accessible(
            self, self.client.get(f"/tickets/refund/{self.ticket.pk}/").content.decode()
        )


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class WaitlistViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        build_site()

    def setUp(self):
        invalidate()
        self.ticket_type = make_type("Personal", regular_count=1)

    def test_joining_needs_no_account(self):
        response = self.client.post(
            "/tickets/waitlist/", {"email": "wants@example.com"}, follow=True
        )
        self.assertContains(response, "on the waiting list")
        self.assertEqual(TicketWaitlistEntry.objects.count(), 1)

    def test_joining_twice_says_so_without_a_second_entry(self):
        self.client.post("/tickets/waitlist/", {"email": "wants@example.com"})
        response = self.client.post(
            "/tickets/waitlist/", {"email": "wants@example.com"}, follow=True
        )
        self.assertContains(response, "already on that waiting list")
        self.assertEqual(TicketWaitlistEntry.objects.count(), 1)

    def test_a_bad_address_is_refused(self):
        response = self.client.post("/tickets/waitlist/", {"email": "nope"}, follow=True)
        self.assertContains(response, "check the email address")
        self.assertEqual(TicketWaitlistEntry.objects.count(), 0)

    def test_joining_is_refused_when_the_waitlist_is_off(self):
        settings_obj = TicketSettings.for_year(YEAR)
        settings_obj.waitlist_enabled = False
        settings_obj.save()
        response = self.client.post(
            "/tickets/waitlist/", {"email": "wants@example.com"}, follow=True
        )
        self.assertContains(response, "no waiting list")
        self.assertEqual(TicketWaitlistEntry.objects.count(), 0)


class CheckinViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        build_site()
        cls.buyer = User.objects.create_user(
            username="ada", email="ada@example.com", password="secret"
        )
        cls.volunteer = User.objects.create_user(
            username="door", email="door@example.com", password="secret"
        )
        RoleAssignment.objects.create(
            user=cls.volunteer, role=Role.VOLUNTEER.value, conference_year=YEAR
        )

    def setUp(self):
        invalidate()
        self.ticket = make_paid_order(self.buyer, make_type())
        self.place = self.ticket.ticket_sales.first()

    def _url(self, code=None):
        return f"/tickets/c/{code or self.place.checkin_code}/"

    def test_an_attendee_cannot_open_the_door_screen(self):
        self.client.force_login(self.buyer)
        self.assertEqual(self.client.get(self._url()).status_code, 404)

    def test_a_volunteer_sees_the_name_without_admitting_anybody(self):
        """
        Opening a URL must never admit somebody as a side effect: a scanner app,
        a preview, a back button and a link check all do GET requests.
        """
        self.client.force_login(self.volunteer)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Attendee 1")
        self.assertContains(response, "Admit")
        self.place.refresh_from_db()
        self.assertFalse(self.place.is_checked_in)

    def test_confirming_admits_them(self):
        self.client.force_login(self.volunteer)
        response = self.client.post(self._url())
        self.assertContains(response, "Admitted")
        self.place.refresh_from_db()
        self.assertTrue(self.place.is_checked_in)
        self.assertEqual(self.place.checked_in_by, self.volunteer)

    def test_a_second_attempt_is_refused(self):
        self.client.force_login(self.volunteer)
        self.client.post(self._url())
        response = self.client.post(self._url())
        self.assertContains(response, "already been used")

    def test_an_unknown_code_says_so_and_explains_the_alphabet(self):
        self.client.force_login(self.volunteer)
        response = self.client.get(self._url("ZZZZZZZZZZ"))
        self.assertContains(response, "No ticket matches that code")
        self.assertContains(response, "letter O, I, L or Z")

    def test_a_refunded_order_does_not_admit_anybody(self):
        self.ticket.status = Ticket.REFUNDED
        self.ticket.save()
        self.client.force_login(self.volunteer)
        response = self.client.post(self._url())
        self.assertContains(response, "not paid, or has been refunded")
        self.place.refresh_from_db()
        self.assertFalse(self.place.is_checked_in)

    def test_the_page_has_no_accessibility_errors(self):
        self.client.force_login(self.volunteer)
        assert_accessible(self, self.client.get(self._url()).content.decode())


class TicketDetailViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        build_site()
        cls.buyer = User.objects.create_user(
            username="ada", email="ada@example.com", password="secret"
        )

    def setUp(self):
        invalidate()
        self.ticket = make_paid_order(self.buyer, make_type(), quantity=2)
        self.client.force_login(self.buyer)

    def test_every_place_shows_a_qr_and_its_code(self):
        response = self.client.get(f"/tickets/detail/{self.ticket.pk}/")
        self.assertEqual(response.status_code, 200)
        for place in self.ticket.ticket_sales.all():
            self.assertContains(response, place.checkin_code)
        self.assertContains(response, "<svg", count=None)
        self.assertEqual(response.content.decode().count("shape-rendering"), 2)

    def test_the_invoice_is_linked_for_the_buyer(self):
        response = self.client.get(f"/tickets/detail/{self.ticket.pk}/")
        self.assertContains(response, f"/tickets/invoice/{self.ticket.pk}/")

    def test_transfers_are_closed_with_a_date_once_the_deadline_passes(self):
        settings_obj = TicketSettings.for_year(YEAR)
        settings_obj.transfer_deadline = timezone.now() - timedelta(days=1)
        settings_obj.save()
        response = self.client.get(f"/tickets/detail/{self.ticket.pk}/")
        self.assertContains(response, "Transfers closed")
        self.assertNotContains(response, "Transfer Ticket")

    def test_the_refund_route_appears_only_within_the_policy(self):
        response = self.client.get(f"/tickets/detail/{self.ticket.pk}/")
        self.assertContains(response, "Refunds are not offered for this edition")

        settings_obj = TicketSettings.for_year(YEAR)
        settings_obj.refund_deadline = timezone.now() + timedelta(days=5)
        settings_obj.save()
        response = self.client.get(f"/tickets/detail/{self.ticket.pk}/")
        self.assertContains(response, f"/tickets/refund/{self.ticket.pk}/")

    def test_an_attendee_sees_only_their_own_place(self):
        colleague = User.objects.create_user(
            username="grace", email="grace@example.com", password="secret"
        )
        place = self.ticket.ticket_sales.first()
        place.user = colleague
        place.attendee_email = colleague.email
        place.save()

        self.client.force_login(colleague)
        response = self.client.get(f"/tickets/detail/{self.ticket.pk}/")
        self.assertEqual(len(response.context["tickets"]), 1)
        self.assertFalse(response.context["is_buyer"])
        # And no invoice: it is not their order.
        self.assertNotContains(response, f"/tickets/invoice/{self.ticket.pk}/")

    def test_the_page_has_no_accessibility_errors(self):
        assert_accessible(
            self, self.client.get(f"/tickets/detail/{self.ticket.pk}/").content.decode()
        )
