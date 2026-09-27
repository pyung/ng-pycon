"""
Tests for the practical pages: FAQ, contact and travel.

The contact form gets most of the attention. It is the one page here that accepts
input from anybody on the internet, so what matters is that a real message reaches
a real inbox, that a bot's does not, and that every field a person has to fill in
has a label a screen reader can read.
"""

from django.core import mail
from django.test import TestCase, override_settings
from wagtail.models import Page, Site

from editions.current import invalidate
from editions.models import Edition
from quality.audit import audit_html, errors

from info.models import ContactFormField, ContactPage, FAQItem, FAQPage, TravelPage


def build_site():
    """A 2026 edition and a homepage at the site root, for pages to hang from."""
    from home.models import HomePage

    Edition.objects.update_or_create(
        year=2026,
        defaults={
            "name": "PyCon Nigeria 2026",
            "theme": "2026",
            "is_current": True,
            "is_published": True,
        },
    )
    invalidate()

    root = Page.objects.get(depth=1)
    home = HomePage(title="PyCon Nigeria 2026", slug="home-2026")
    root.add_child(instance=home)
    home.save_revision().publish()

    site = Site.objects.get(is_default_site=True)
    site.root_page = home
    site.save()
    return home


class FAQPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        home = build_site()
        cls.faq = FAQPage(title="FAQs", slug="faqs", intro="<p>Common questions.</p>")
        home.add_child(instance=cls.faq)
        cls.faq.save_revision().publish()

        # Deliberately interleaved, to prove sections come out in the order their
        # first question appears rather than alphabetically or by insertion.
        for order, (section, question) in enumerate(
            [
                ("Tickets", "How much is a ticket?"),
                ("Travel", "Where should I stay?"),
                ("Tickets", "Can I get a refund?"),
                ("", "Is there a dress code?"),
            ]
        ):
            FAQItem.objects.create(
                page=cls.faq,
                sort_order=order,
                section=section,
                question=question,
                answer=f"<p>Answer to {question}</p>",
            )

    def setUp(self):
        invalidate()

    def test_sections_follow_the_order_their_first_question_appears(self):
        groups = self.faq.grouped_questions()
        self.assertEqual([section for section, _ in groups], ["Tickets", "Travel", "General"])
        self.assertEqual(len(groups[0][1]), 2)

    def test_a_blank_section_becomes_general(self):
        sections = dict(self.faq.grouped_questions())
        self.assertIn("General", sections)
        self.assertEqual(sections["General"][0].question, "Is there a dress code?")

    def test_a_question_is_linkable_with_or_without_an_anchor(self):
        item = self.faq.questions.first()
        self.assertEqual(item.dom_id, f"faq-{item.pk}")
        item.anchor = "ticket-price"
        item.save()
        self.assertEqual(item.dom_id, "ticket-price")

    def test_the_page_renders_every_question(self):
        response = self.client.get("/faqs/")
        self.assertEqual(response.status_code, 200)
        for item in self.faq.questions.all():
            self.assertContains(response, item.question)

    def test_the_page_has_no_accessibility_errors(self):
        html = self.client.get("/faqs/").content.decode()
        found = errors(audit_html(html, internal_hosts=("testserver",)))
        self.assertEqual(found, [], "\n".join(str(f) for f in found))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ContactPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        home = build_site()
        cls.contact = ContactPage(
            title="Contact",
            slug="contact",
            intro="<p>Get in touch.</p>",
            response_time="We usually reply within two working days.",
            thank_you_text="<p>We have your message.</p>",
            from_address="hello@pynigeria.com",
            to_address="team@pynigeria.com",
            subject="Website contact form",
        )
        home.add_child(instance=cls.contact)
        cls.contact.save_revision().publish()

        ContactFormField.objects.create(
            page=cls.contact, sort_order=0, label="Your name",
            field_type="singleline", required=True,
        )
        ContactFormField.objects.create(
            page=cls.contact, sort_order=1, label="Your email",
            field_type="email", required=True,
        )
        ContactFormField.objects.create(
            page=cls.contact, sort_order=2, label="Message",
            field_type="multiline", required=True,
        )

    def setUp(self):
        invalidate()
        mail.outbox = []

    def _payload(self, **overrides):
        # Wagtail derives the field name from the label, with underscores.
        data = {
            "your_name": "Ada Lovelace",
            "your_email": "ada@example.com",
            "message": "When do tickets go on sale?",
        }
        data.update(overrides)
        return data

    def test_the_form_renders_with_a_label_for_every_field(self):
        response = self.client.get("/contact/")
        self.assertEqual(response.status_code, 200)
        for label in ("Your name", "Your email", "Message"):
            self.assertContains(response, label)

    def test_a_message_is_stored_and_emailed(self):
        response = self.client.post("/contact/", self._payload())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "We have your message.")

        submissions = self.contact.get_submission_class().objects.filter(page=self.contact)
        self.assertEqual(submissions.count(), 1)
        self.assertIn("Ada Lovelace", str(submissions.first().form_data))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["team@pynigeria.com"])

    def test_a_filled_honeypot_is_discarded_silently(self):
        """
        The bot gets the same page a person gets, and nothing is stored or sent.
        Telling it which check it failed would only help it.
        """
        response = self.client.post(
            "/contact/", self._payload(website="http://spam.example")
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.contact.get_submission_class().objects.filter(page=self.contact).count(), 0
        )
        self.assertEqual(len(mail.outbox), 0)

    def test_a_missing_required_field_comes_back_with_the_error(self):
        response = self.client.post("/contact/", self._payload(message=""))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.contact.get_submission_class().objects.filter(page=self.contact).count(), 0
        )
        self.assertContains(response, "This field is required")

    def test_the_page_has_no_accessibility_errors(self):
        html = self.client.get("/contact/").content.decode()
        found = errors(audit_html(html, internal_hosts=("testserver",)))
        self.assertEqual(found, [], "\n".join(str(f) for f in found))

    def test_the_landing_page_has_no_accessibility_errors(self):
        html = self.client.post("/contact/", self._payload()).content.decode()
        found = errors(audit_html(html, internal_hosts=("testserver",)))
        self.assertEqual(found, [], "\n".join(str(f) for f in found))


class TravelPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        home = build_site()
        cls.travel = TravelPage(
            title="Travel and venue",
            slug="travel",
            intro="<p>Getting to PyCon Nigeria.</p>",
            body=[
                (
                    "venue",
                    {
                        "name": "Landmark Centre",
                        "address": "Water Corporation Road, Victoria Island, Lagos",
                        "map_url": "https://maps.example/landmark",
                        "directions": "<p>Use the Oniru gate.</p>",
                        "accessibility": "<p>Step-free from the car park.</p>",
                    },
                ),
                (
                    "places_to_stay",
                    [
                        {
                            "name": "Nearby Hotel",
                            "distance": "10 minutes on foot",
                            "price_band": "45,000 - 70,000 per night",
                            "url": "https://hotel.example",
                            "notes": "Ask for the conference rate.",
                        }
                    ],
                ),
                (
                    "getting_here",
                    [{"mode": "By air", "detail": "<p>Fly into Lagos (LOS).</p>"}],
                ),
                (
                    "questions",
                    [
                        {
                            "question": "Do I need a visa?",
                            "answer": "<p>Most visitors do. Write to us early.</p>",
                        }
                    ],
                ),
            ],
        )
        home.add_child(instance=cls.travel)
        cls.travel.save_revision().publish()

    def setUp(self):
        invalidate()

    def test_every_block_renders(self):
        response = self.client.get("/travel/")
        self.assertEqual(response.status_code, 200)
        for expected in (
            "Landmark Centre",
            "Water Corporation Road",
            "Use the Oniru gate.",
            "Step-free from the car park.",
            "Nearby Hotel",
            "10 minutes on foot",
            "By air",
            "Do I need a visa?",
        ):
            self.assertContains(response, expected)

    def test_an_external_link_opens_safely_and_says_so(self):
        response = self.client.get("/travel/")
        self.assertContains(response, 'rel="noopener noreferrer"')
        self.assertContains(response, "opens in a new tab")

    def test_hotels_carry_a_caveat(self):
        """A list of hotels on a conference site reads as an endorsement otherwise."""
        self.assertContains(self.client.get("/travel/"), "no arrangement with these")

    def test_the_page_has_no_accessibility_errors(self):
        html = self.client.get("/travel/").content.decode()
        found = errors(audit_html(html, internal_hosts=("testserver",)))
        self.assertEqual(found, [], "\n".join(str(f) for f in found))
