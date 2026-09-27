"""
Content blocks for the practical-information pages.

Structured rather than free rich text, because the questions these pages answer
are specific: how far is the hotel, what does it cost, which airport, who do I
ask. A hotel with a name, a distance and a price band can be rendered as a
comparable list and read on a phone; the same information in a paragraph cannot.
"""

from wagtail import blocks
from wagtail.images.blocks import ImageChooserBlock


class ImageWithAltBlock(blocks.StructBlock):
    """
    An image and its alt text, together.

    Paired on purpose: Wagtail will happily render a bare ImageChooserBlock with
    the image's title as alt text, which is a filename more often than a
    description. Asking for the alt text beside the image is the only reliable
    way to get it.
    """

    image = ImageChooserBlock()
    alt = blocks.CharBlock(
        max_length=200,
        help_text=(
            "What the image shows, for someone who cannot see it. Leave blank only "
            "if it is purely decorative."
        ),
        required=False,
    )
    caption = blocks.CharBlock(max_length=250, required=False)

    class Meta:
        icon = "image"
        label = "Image"


class VenueBlock(blocks.StructBlock):
    """Where the conference is, and how to get inside it."""

    name = blocks.CharBlock(max_length=200)
    address = blocks.TextBlock(
        help_text="As you would write it on an envelope, or read it to a driver."
    )
    map_url = blocks.URLBlock(
        required=False,
        help_text=(
            "A link to the venue on a map. A link rather than an embedded map: an "
            "embed is a third-party script on every page load, and it is the one "
            "thing that will not work on a slow connection."
        ),
    )
    directions = blocks.RichTextBlock(
        required=False, help_text="Landmarks, entrances, which gate to use."
    )
    accessibility = blocks.RichTextBlock(
        required=False,
        help_text=(
            "Step-free access, lifts, accessible toilets, quiet spaces. Say what is "
            "there and what is not -- a gap somebody discovers on the day is worse "
            "than a gap they could plan around."
        ),
    )
    image = ImageWithAltBlock(required=False)

    class Meta:
        icon = "home"
        label = "Venue"


class HotelBlock(blocks.StructBlock):
    """One place to stay."""

    name = blocks.CharBlock(max_length=200)
    distance = blocks.CharBlock(
        max_length=100,
        required=False,
        help_text="From the venue, as people think of it: '10 minutes on foot'.",
    )
    price_band = blocks.CharBlock(
        max_length=100,
        required=False,
        help_text="A range in naira per night. A band, not a quote -- it will change.",
    )
    url = blocks.URLBlock(required=False)
    notes = blocks.CharBlock(max_length=300, required=False)

    class Meta:
        icon = "home"
        label = "Place to stay"


class TransportBlock(blocks.StructBlock):
    """One way of getting to the venue."""

    mode = blocks.CharBlock(
        max_length=100, help_text="By air, by road, by ride-hailing, on foot."
    )
    detail = blocks.RichTextBlock()

    class Meta:
        icon = "redirect"
        label = "Getting here"


class QuestionBlock(blocks.StructBlock):
    """One question and its answer, for a page that is mostly prose."""

    question = blocks.CharBlock(max_length=300)
    answer = blocks.RichTextBlock()

    class Meta:
        icon = "help"
        label = "Question"


class CalloutBlock(blocks.StructBlock):
    title = blocks.CharBlock(max_length=200, required=False)
    text = blocks.RichTextBlock()

    class Meta:
        icon = "warning"
        label = "Callout"


#: The block set shared by the informational pages, so they read alike.
COMMON_BLOCKS = [
    ("heading", blocks.CharBlock(max_length=200, form_classname="title")),
    ("paragraph", blocks.RichTextBlock()),
    ("image", ImageWithAltBlock()),
    ("callout", CalloutBlock()),
]
