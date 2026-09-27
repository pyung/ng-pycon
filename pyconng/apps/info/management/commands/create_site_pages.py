"""
Create the module 2 pages under the current homepage.

    python manage.py create_site_pages            # create as drafts
    python manage.py create_site_pages --publish  # create and publish

Five page types -- Code of Conduct, FAQ, contact, travel and blog -- each needing
a parent, a slug and in the contact form's case a set of fields. Doing that by
hand in the admin five times, for each new edition, is exactly the sort of chore
that gets half done.

Drafts by default, and deliberately so. The skeleton content here is structure,
not copy: a Code of Conduct is a commitment the organizers make in their own
words, and this command must never be the reason placeholder text goes live.
Idempotent -- an existing page of the same type is left exactly as it is.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from blog.models import BlogIndexPage
from conduct.models import CodeOfConductPage
from editions.current import current_year
from info.models import ContactFormField, ContactPage, FAQItem, FAQPage, TravelPage


def _home_page():
    """The homepage of the current edition, which every one of these hangs from."""
    from django.db.models import Q
    from home.models import HomePage

    year = current_year()
    return (
        HomePage.objects.live()
        .filter(Q(conference_year__isnull=True) | Q(conference_year=year))
        .first()
        or HomePage.objects.live().first()
    )


#: Starter questions. Structure and section names, with answers that say plainly
#: that they need writing -- an answer nobody has written must not read as one
#: somebody has.
FAQ_SKELETON = [
    ("Tickets", "How much is a ticket?", "To be written."),
    ("Tickets", "Can I get a refund?", "To be written."),
    ("Tickets", "Do you offer student or group rates?", "To be written."),
    ("Travel", "Where should I stay?", "To be written. Link the travel page here."),
    ("Travel", "Do I need a visa?", "To be written."),
    ("Speaking", "When does the call for proposals close?", "To be written."),
    ("At the event", "Is the venue accessible?", "To be written."),
    ("At the event", "Will talks be recorded?", "To be written."),
]

#: Fields on the contact form. Kept minimal: every extra field is a reason not to
#: get in touch, and the topic dropdown is what actually saves the team time.
CONTACT_FIELDS = [
    ("Your name", "singleline", True, "", ""),
    ("Your email", "email", True, "So we can reply.", ""),
    (
        "What is this about?",
        "dropdown",
        True,
        "",
        "Tickets\nSpeaking\nSponsorship\nTravel grants\nAccessibility\nSomething else",
    ),
    ("Message", "multiline", True, "", ""),
]


class Command(BaseCommand):
    help = "Create the Code of Conduct, FAQ, contact, travel and blog pages."

    def add_arguments(self, parser):
        parser.add_argument(
            "--publish",
            action="store_true",
            help="Publish the pages. Without this they are created as drafts.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        home = _home_page()
        if home is None:
            raise CommandError(
                "No live HomePage to attach these to. Create one first."
            )

        publish = options["publish"]
        created = []
        skipped = []

        for build in (
            self._code_of_conduct,
            self._faq,
            self._contact,
            self._travel,
            self._blog,
        ):
            page, was_created = build(home, publish)
            (created if was_created else skipped).append(page)

        for page in created:
            state = "published" if publish else "draft"
            self.stdout.write(
                self.style.SUCCESS(f"  created ({state})  {page.url_path}  {page.title}")
            )
        for page in skipped:
            self.stdout.write(f"  exists            {page.url_path}  {page.title}")

        self.stdout.write("")
        self.stdout.write(f"{len(created)} created, {len(skipped)} already there.")
        if created and not publish:
            self.stdout.write(
                "Created as drafts. Write the copy in the admin, then publish. "
                "Re-run with --publish to publish new pages immediately."
            )
        if created:
            self.stdout.write(
                "Add them to the footer links and the navigation menu on the "
                "homepage, so people can find them."
            )

    def _add(self, home, page, publish):
        """Attach a page and either publish it or leave it as a draft."""
        page.live = publish
        home.add_child(instance=page)
        revision = page.save_revision()
        if publish:
            revision.publish()
        return page

    def _existing(self, model, home):
        return model.objects.descendant_of(home, inclusive=False).first()

    def _code_of_conduct(self, home, publish):
        existing = self._existing(CodeOfConductPage, home)
        if existing:
            return existing, False
        page = CodeOfConductPage(
            title="Code of Conduct",
            slug="code-of-conduct",
            intro=(
                "<p>PyCon Nigeria is for everyone. This page says what we expect of "
                "each other, and what to do when someone falls short of it. Replace "
                "this paragraph with the organizers' own words.</p>"
            ),
            report_heading="Reporting a breach",
            report_intro=(
                "<p>Tell us and we will act. Only the Code of Conduct team can read "
                "a report, and you do not have to give your name.</p>"
            ),
            response_promise="",
            body=[
                ("heading", "What we expect"),
                (
                    "expectations",
                    [
                        "Be kind and assume good faith.",
                        "Respect that people have differences of opinion.",
                        "Take responsibility for your mistakes.",
                    ],
                ),
                ("heading", "What is not acceptable"),
                (
                    "unacceptable",
                    [
                        "Harassment of any kind, in person or online.",
                        "Unwelcome comments about a person's identity or appearance.",
                        "Deliberate intimidation, stalking or following.",
                    ],
                ),
                ("heading", "What happens when someone reports"),
                (
                    "paragraph",
                    "<p>To be written: who reads a report, how quickly, and what "
                    "the team can do about it.</p>",
                ),
            ],
        )
        return self._add(home, page, publish), True

    def _faq(self, home, publish):
        existing = self._existing(FAQPage, home)
        if existing:
            return existing, False
        page = FAQPage(
            title="FAQs",
            slug="faqs",
            intro="<p>The questions we are asked most.</p>",
            no_answer_text=(
                "<p>If your question is not here, get in touch and we will answer "
                "it &mdash; and add it to this page.</p>"
            ),
        )
        self._add(home, page, publish)
        for order, (section, question, answer) in enumerate(FAQ_SKELETON):
            FAQItem.objects.create(
                page=page,
                sort_order=order,
                section=section,
                question=question,
                answer=f"<p>{answer}</p>",
            )
        return page, True

    def _contact(self, home, publish):
        existing = self._existing(ContactPage, home)
        if existing:
            return existing, False
        page = ContactPage(
            title="Contact",
            slug="contact",
            intro="<p>How to reach the PyCon Nigeria team.</p>",
            response_time="",
            thank_you_text=(
                "<p>Your message has reached us. We will be in touch.</p>"
            ),
            # A shared address on both sides, so a reply is not stuck in one
            # person's inbox and a bounce goes somewhere a human looks.
            from_address="hello@pynigeria.com",
            to_address="hello@pynigeria.com",
            subject="Website contact form",
        )
        self._add(home, page, publish)
        for order, (label, field_type, required, help_text, choices) in enumerate(
            CONTACT_FIELDS
        ):
            ContactFormField.objects.create(
                page=page,
                sort_order=order,
                label=label,
                field_type=field_type,
                required=required,
                help_text=help_text,
                choices=choices,
            )
        return page, True

    def _travel(self, home, publish):
        existing = self._existing(TravelPage, home)
        if existing:
            return existing, False
        page = TravelPage(
            title="Travel and venue",
            slug="travel",
            intro="<p>Getting to PyCon Nigeria, and what to expect when you arrive.</p>",
            body=[
                (
                    "venue",
                    {
                        "name": "Venue name",
                        "address": "To be written.",
                        "map_url": "",
                        "directions": "<p>To be written: gates, entrances, landmarks.</p>",
                        "accessibility": (
                            "<p>To be written: step-free access, lifts, accessible "
                            "toilets, quiet spaces. Say what is not there as well as "
                            "what is.</p>"
                        ),
                    },
                ),
                ("heading", "Getting here"),
                (
                    "getting_here",
                    [
                        {"mode": "By air", "detail": "<p>To be written.</p>"},
                        {"mode": "By road", "detail": "<p>To be written.</p>"},
                    ],
                ),
            ],
        )
        return self._add(home, page, publish), True

    def _blog(self, home, publish):
        existing = self._existing(BlogIndexPage, home)
        if existing:
            return existing, False
        page = BlogIndexPage(
            title="News",
            slug="news",
            intro="<p>Announcements, and what we are working on.</p>",
        )
        return self._add(home, page, publish), True
