"""
The practical pages: frequently asked questions, contact, travel and venue.

All three were missing, and their absence showed: the site footer linked to
/faqs/ and /contact/, neither of which existed, and the only answer to "where do
I stay?" was to ask on X.

The contact page is built on ``wagtail.contrib.forms``, which was already in
INSTALLED_APPS and entirely unused. That means the fields, the recipients and the
confirmation text are all editable in the admin -- adding a question to the
contact form is not a deploy.
"""

import logging

from django.db import models
from django.shortcuts import render
from modelcluster.fields import ParentalKey
from wagtail import blocks
from wagtail.admin.panels import FieldPanel, FieldRowPanel, InlinePanel, MultiFieldPanel
from wagtail.contrib.forms.models import AbstractEmailForm, AbstractFormField
from wagtail.fields import RichTextField, StreamField
from wagtail.models import Orderable, Page

from .blocks import (
    COMMON_BLOCKS,
    HotelBlock,
    QuestionBlock,
    TransportBlock,
    VenueBlock,
)

logger = logging.getLogger(__name__)


class FAQPage(Page):
    """
    Questions and answers, grouped into sections.

    Rows rather than a StreamField so each question keeps its own identity: they
    get reordered constantly in the weeks before a conference, they are linkable
    by anchor, and a question is the natural unit to search.
    """

    intro = RichTextField(
        blank=True, help_text="A sentence or two above the questions."
    )
    no_answer_heading = models.CharField(
        max_length=200,
        default="Still stuck?",
        help_text="Heading for the fallback at the foot of the page.",
    )
    no_answer_text = RichTextField(
        blank=True,
        help_text="Where to go if their question is not here. Link the contact page.",
    )

    parent_page_types = ["home.HomePage"]
    subpage_types = []

    content_panels = Page.content_panels + [
        FieldPanel("intro"),
        InlinePanel("questions", label="Question"),
        MultiFieldPanel(
            [FieldPanel("no_answer_heading"), FieldPanel("no_answer_text")],
            heading="If the answer is not here",
        ),
    ]

    class Meta:
        verbose_name = "FAQ page"

    def grouped_questions(self):
        """
        ``[(section, [question, ...])]`` in editor order.

        Sections come out in the order their first question appears, so an
        organizer controls the running order by dragging questions rather than by
        naming sections cleverly.
        """
        groups = {}
        order = []
        for item in self.questions.all():
            section = item.section.strip() or "General"
            if section not in groups:
                groups[section] = []
                order.append(section)
            groups[section].append(item)
        return [(section, groups[section]) for section in order]

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        context["question_groups"] = self.grouped_questions()
        return context


class FAQItem(Orderable):
    """One question."""

    page = ParentalKey(FAQPage, related_name="questions", on_delete=models.CASCADE)
    section = models.CharField(
        max_length=100,
        blank=True,
        help_text=(
            "Groups this question with others, e.g. 'Tickets' or 'Travel'. "
            "Leave blank for 'General'."
        ),
    )
    question = models.CharField(max_length=300)
    answer = RichTextField()
    anchor = models.SlugField(
        max_length=80,
        blank=True,
        help_text=(
            "Optional. Makes this question linkable as #your-anchor, so support "
            "replies can point straight at the answer."
        ),
    )

    panels = [
        FieldPanel("section"),
        FieldPanel("question"),
        FieldPanel("answer"),
        FieldPanel("anchor"),
    ]

    class Meta(Orderable.Meta):
        verbose_name = "question"

    def __str__(self):
        return self.question[:80]

    @property
    def dom_id(self):
        """A stable id for the answer panel, whether or not an anchor was set."""
        return self.anchor or f"faq-{self.pk}"


class ContactFormField(AbstractFormField):
    """A field on the contact form, defined in the admin."""

    page = ParentalKey(
        "info.ContactPage", related_name="form_fields", on_delete=models.CASCADE
    )


class ContactPage(AbstractEmailForm):
    """
    A contact form, plus the ways to reach the team that are not a form.

    Both halves matter. A form is the right default -- it goes to a shared
    address rather than one person's inbox, and it does not put a mailto: on a
    public page for scrapers. But somebody at the venue with a dying phone
    battery needs a number, so the other routes are listed as well.
    """

    intro = RichTextField(blank=True)
    response_time = models.CharField(
        max_length=200,
        blank=True,
        help_text=(
            "What to expect, e.g. 'We usually reply within two working days.' "
            "Saying nothing is better than promising something you will not meet."
        ),
    )
    contact_methods = StreamField(
        [
            (
                "method",
                blocks.StructBlock(
                    [
                        ("label", blocks.CharBlock(max_length=120)),
                        (
                            "detail",
                            blocks.CharBlock(
                                max_length=200,
                                help_text="The address, number or handle itself.",
                            ),
                        ),
                        (
                            "url",
                            blocks.CharBlock(
                                max_length=300,
                                required=False,
                                help_text=(
                                    "Optional link. Use mailto: or tel: to make it "
                                    "one tap on a phone."
                                ),
                            ),
                        ),
                        (
                            "note",
                            blocks.CharBlock(
                                max_length=200,
                                required=False,
                                help_text="What this route is for, so people pick the right one.",
                            ),
                        ),
                    ],
                    label="Contact method",
                    icon="mail",
                ),
            )
        ],
        blank=True,
        use_json_field=True,
    )
    thank_you_text = RichTextField(
        blank=True, help_text="Shown after the form is sent."
    )

    parent_page_types = ["home.HomePage"]
    subpage_types = []

    content_panels = AbstractEmailForm.content_panels + [
        FieldPanel("intro"),
        FieldPanel("response_time"),
        FieldPanel("contact_methods"),
        InlinePanel("form_fields", label="Form field"),
        FieldPanel("thank_you_text"),
        MultiFieldPanel(
            [
                FieldRowPanel([FieldPanel("from_address"), FieldPanel("to_address")]),
                FieldPanel("subject"),
            ],
            heading="Where submissions go",
        ),
    ]

    class Meta:
        verbose_name = "Contact page"

    #: Bots fill in every field they find. A real visitor never sees this one, so
    #: anything in it means the submission is not a person. Checked in serve()
    #: rather than added to the form class, because the form class is assembled
    #: from admin-defined fields and should stay that way.
    HONEYPOT_FIELD = "website"

    def serve(self, request, *args, **kwargs):
        if request.method == "POST" and request.POST.get(self.HONEYPOT_FIELD, "").strip():
            # Say nothing useful. Telling a bot which check it failed helps it, and
            # a human will never see this branch.
            logger.info("Discarded a contact submission that filled the honeypot.")
            return render(
                request,
                self.get_landing_page_template(request),
                self.get_context(request),
            )
        return super().serve(request, *args, **kwargs)


class TravelPage(Page):
    """
    Getting here, staying here, and getting inside the building.

    Structured blocks rather than one rich-text field: hotels and transport
    options are lists people compare, and a visa question is one somebody needs to
    find in a hurry.
    """

    intro = RichTextField(blank=True)
    body = StreamField(
        COMMON_BLOCKS
        + [
            ("venue", VenueBlock()),
            (
                "places_to_stay",
                blocks.ListBlock(
                    HotelBlock(),
                    label="Places to stay",
                    help_text="Rendered as a comparable list. Order them as you would recommend them.",
                ),
            ),
            (
                "getting_here",
                blocks.ListBlock(TransportBlock(), label="Getting here"),
            ),
            (
                "questions",
                blocks.ListBlock(
                    QuestionBlock(),
                    label="Travel questions",
                    help_text="Visas, currency, insurance -- whatever gets asked most.",
                ),
            ),
        ],
        blank=True,
        use_json_field=True,
    )

    parent_page_types = ["home.HomePage"]
    subpage_types = []

    content_panels = Page.content_panels + [
        FieldPanel("intro"),
        FieldPanel("body"),
    ]

    class Meta:
        verbose_name = "Travel and venue page"
