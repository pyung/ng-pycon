import logging

from django.core.exceptions import ValidationError
from django.db import models
from django.template import TemplateDoesNotExist
from django.template.loader import get_template
from django.urls import reverse

from editions.validators import validate_edition_year
from wagtail.models import Page
from wagtail.fields import RichTextField, StreamField
from wagtail.admin.panels import FieldPanel, MultiFieldPanel
from wagtail import blocks
from wagtail.images.blocks import ImageChooserBlock
from wagtail.images import get_image_model_string
from wagtail.blocks import PageChooserBlock


logger = logging.getLogger(__name__)


class FeatureBlock(blocks.StructBlock):
    """A feature card block with icon, title, and description."""
    icon_svg = blocks.TextBlock(
        required=False,
        help_text="SVG code for the icon (optional)"
    )
    title = blocks.CharBlock(max_length=100)
    description = blocks.TextBlock()
    link_text = blocks.CharBlock(max_length=50, required=False)
    link_url = blocks.URLBlock(required=False)
    
    class Meta:
        icon = "doc-full"
        label = "Feature Card"


class NavigationSubItemBlock(blocks.StructBlock):
    """A sub-item within a navigation dropdown (e.g. 'Proposal Guidelines' under 'Speaking')."""
    label = blocks.CharBlock(max_length=80, required=True)
    page = PageChooserBlock(
        target_model=Page,
        required=False,
        help_text="Pick an internal page, or leave blank and set URL below.",
    )
    link_url = blocks.CharBlock(
        max_length=200,
        required=False,
        blank=True,
        help_text="External URL, /path, or #anchor (used when no page is selected).",
    )

    def clean(self, value):
        value = super().clean(value)
        page = value.get("page")
        link = (value.get("link_url") or "").strip()
        if not page and not link:
            raise ValidationError(
                "Choose a page, or provide a URL / path for this sub-menu item."
            )
        return value

    class Meta:
        icon = "arrows-up-down"
        label = "Sub-menu Item"


class NavigationMenuItemBlock(blocks.StructBlock):
    """A navigation menu item - can be a simple link or a dropdown with sub-items."""
    label = blocks.CharBlock(max_length=50, required=True)
    page = PageChooserBlock(
        target_model=Page,
        required=False,
        help_text="For a direct link: pick an internal page, or leave blank and set URL below.",
    )
    link_url = blocks.CharBlock(
        max_length=200,
        required=False,
        blank=True,
        help_text="URL or path when the item is a direct link (not required if this item only opens a dropdown).",
    )
    sub_items = blocks.ListBlock(
        NavigationSubItemBlock(),
        required=False,
        blank=True,
        help_text="Add sub-menu items to show a dropdown. If empty, this will be a direct link."
    )

    def clean(self, value):
        value = super().clean(value)
        subs = value.get("sub_items")
        has_subs = bool(subs and len(subs) > 0)
        if not has_subs:
            page = value.get("page")
            link = (value.get("link_url") or "").strip()
            if not page and not link:
                raise ValidationError(
                    "For a top-level link (no sub-menu), choose a page or provide a URL / path."
                )
        return value

    class Meta:
        icon = "link"
        label = "Menu Item"


class FooterLinkBlock(blocks.StructBlock):
    """A footer link."""
    label = blocks.CharBlock(max_length=100, required=True)
    url = blocks.CharBlock(max_length=200, required=True)
    
    class Meta:
        icon = "link"
        label = "Footer Link"


class PhaseCTABlock(blocks.StructBlock):
    """Override the homepage's primary button for one lifecycle phase."""

    phase = blocks.ChoiceBlock(
        choices=[],  # filled in __init__ so the phase list stays in one place
        help_text="Which phase this wording applies to.",
    )
    label = blocks.CharBlock(max_length=80)
    url = blocks.CharBlock(
        max_length=500,
        help_text="Path or full URL. Relative paths like /tickets/ are fine.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from editions.phases import PHASE_CHOICES

        self.child_blocks["phase"].field.choices = PHASE_CHOICES

    class Meta:
        icon = "link"
        label = "Phase call to action"


class SponsorBenefitBlock(blocks.StructBlock):
    """A single 'why sponsor' benefit (title + body)."""
    title = blocks.CharBlock(max_length=200)
    body = blocks.RichTextBlock()

    class Meta:
        icon = "tick"
        label = "Benefit"


class SponsorPackageBlock(blocks.StructBlock):
    """One sponsorship tier (e.g. Platinum, Gold)."""
    tier_name = blocks.CharBlock(max_length=200)
    price_label = blocks.CharBlock(max_length=120)
    best_for = blocks.CharBlock(
        max_length=300,
        required=False,
        help_text="Short line, e.g. 'Best for: talent acquisition'",
    )
    benefits = blocks.RichTextBlock()
    slots_note = blocks.CharBlock(
        max_length=200,
        required=False,
        help_text="e.g. 'Limited slots: 2'",
    )
    featured = blocks.BooleanBlock(
        required=False,
        default=False,
        help_text="Visually emphasize this tier on the public page",
    )

    class Meta:
        icon = "placeholder"
        label = "Sponsorship package"


class HomePage(Page):
    """
    Home page for PyCon Nigeria conference.
    Can be used for both current year (at root /) and archived years (at /2024/, /2025/, etc.)
    Each instance can have its own theme/design.
    """
    
    # Conference Year & Theme
    conference_year = models.IntegerField(
        null=True,
        blank=True,
        validators=[validate_edition_year],
        help_text="The conference year this page represents (e.g., 2024, 2025). Leave blank for current year."
    )
    
    # Theme/Design selection.
    # Deliberately free text rather than choices: adding a theme for a new
    # edition should not need a migration. Leave blank to inherit the theme from
    # this page's Edition, which is the usual case.
    theme = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text=(
            "Template theme override, e.g. theme_2026. Leave blank to use the "
            "theme set on this page's Edition."
        ),
    )
    
    # Hero Section
    hero_title = models.CharField(
        max_length=200, 
        default="PyCon Nigeria",
        help_text="Main hero title"
    )
    hero_subtitle = models.CharField(
        max_length=200,
        blank=True,
        help_text="Subtitle text (optional)"
    )
    hero_description = RichTextField(
        blank=True,
        help_text="Hero section description"
    )
    hero_primary_button_text = models.CharField(
        max_length=50,
        blank=True,
        default="Register Now",
        help_text=(
            "Overrides the automatic button. Clear this (and the URL) to let the "
            "button follow the conference phase instead."
        ),
    )
    hero_primary_button_url = models.URLField(blank=True)
    hero_secondary_button_text = models.CharField(
        max_length=50,
        blank=True,
        default="Call for Papers",
        help_text="Secondary button text. Leave blank to hide the button.",
    )
    hero_secondary_button_url = models.URLField(blank=True)
    
    # Conference Location & Dates
    conference_location = models.CharField(
        max_length=200,
        blank=True,
        help_text="Conference location (e.g., Lagos, Nigeria)"
    )
    show_countdown = models.BooleanField(
        default=True,
        help_text="Show a countdown to the first day. Needs a start date on the Edition.",
    )
    countdown_label = models.CharField(
        max_length=120,
        blank=True,
        default="until the conference",
        help_text="Text under the countdown.",
    )
    phase_ctas = StreamField(
        [("cta", PhaseCTABlock())],
        blank=True,
        use_json_field=True,
        help_text=(
            "Optional: reword the primary button for particular phases. Without "
            "an entry, sensible defaults are used. The hero button fields above "
            "override this entirely if you fill them in."
        ),
    )
    conference_dates = models.CharField(
        max_length=200,
        blank=True,
        help_text="Conference dates (e.g., May 15-17, 2025)"
    )
    
    # Conference Info Section
    conference_section_title = models.CharField(
        max_length=200,
        default="Conference Highlights",
        help_text="Title for the main conference section"
    )
    conference_description = RichTextField(
        blank=True,
        help_text="Description of the conference"
    )
    
    # Features
    features = StreamField([
        ('feature', FeatureBlock()),
    ], blank=True, help_text="Add feature cards to showcase conference highlights")
    
    # Navigation Menu
    navigation_menu_items = StreamField([
        ('menu_item', NavigationMenuItemBlock()),
    ], blank=True, help_text="Navigation menu items")
    login_button_text = models.CharField(
        max_length=50,
        default="Login",
        blank=True,
        help_text="Sign-in control label (default: Sign In if left blank).",
    )
    login_link_page = models.ForeignKey(
        "wagtailcore.Page",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Optional. Internal page for sign-in; used before Login button URL.",
    )
    login_button_url = models.URLField(
        blank=True,
        help_text="Optional URL if no page is selected (e.g. external or SSO).",
    )
    
    # Sponsor Section
    sponsor_section_title = models.CharField(
        max_length=200,
        default="Sponsors",
        blank=True,
        help_text="Sponsor section title"
    )
    
    # Community Voting Section
    voting_section_title = models.CharField(
        max_length=200,
        default="Community Voting",
        blank=True,
        help_text="Voting section title"
    )
    voting_deadline = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Voting deadline (for countdown timer)"
    )
    voting_description = RichTextField(
        blank=True,
        help_text="Voting section description"
    )
    voting_button_text = models.CharField(
        max_length=50,
        default="Vote Now",
        blank=True,
        help_text="Voting button text"
    )
    voting_button_url = models.URLField(
        blank=True,
        help_text="Voting button URL"
    )
    
    # Year Navigation Section
    year_navigation_title = models.CharField(
        max_length=200,
        default="Browse Conference Years",
        help_text="Title for the year navigation section"
    )
    year_navigation_description = RichTextField(
        blank=True,
        help_text="Description for the year navigation section"
    )
    
    # Footer
    footer_copyright = models.CharField(
        max_length=200,
        default="© PyCon Nigeria. All rights reserved.",
        blank=True,
        help_text="Footer copyright text"
    )
    footer_links = StreamField([
        ('link', FooterLinkBlock()),
    ], blank=True, help_text="Footer links (Privacy Policy, FAQs, etc.)")
    
    # Social Media
    twitter_url = models.URLField(blank=True, help_text="Twitter/X URL")
    facebook_url = models.URLField(blank=True, help_text="Facebook URL")
    linkedin_url = models.URLField(blank=True, help_text="LinkedIn URL")
    instagram_url = models.URLField(blank=True, help_text="Instagram URL")
    youtube_url = models.URLField(blank=True, help_text="YouTube URL")
    github_url = models.URLField(blank=True, help_text="GitHub URL")
    
    # Newsletter
    newsletter_title = models.CharField(
        max_length=200,
        default="Stay tuned!",
        blank=True,
        help_text="Newsletter section title"
    )
    newsletter_description = models.CharField(
        max_length=500,
        blank=True,
        help_text="Newsletter description"
    )

    # Allow HomePage to be nested (for archived years) and have standard pages as children
    parent_page_types = ["wagtailcore.Page", "home.HomePage"]  # Can be root or child of another HomePage
    subpage_types = [
        "home.StandardPage",
        "home.SponsorPage",
        "home.HomePage",
        "meetups.MeetupIndexPage",
        "program.YearArchivePage",
        "program.SchedulePage",
        "program.SpeakersPage",
    ]  # Standard pages, sponsor page, meetups index, year archives, nested year homepages

    def get_default_child_class(self):
        from .models import StandardPage
        return StandardPage
    
    DEFAULT_TEMPLATE = "home/home_page.html"

    @property
    def resolved_theme(self):
        """
        The theme this page renders with: its own override, else the theme set on
        its Edition. Empty when neither is set, meaning the default design.
        """
        if self.theme:
            return self.theme
        from editions.current import current_year, edition_for_year

        edition = edition_for_year(self.conference_year or current_year())
        if edition and edition.theme:
            # Editions store a bare slug ("2026"); templates are "theme_2026".
            return f"theme_{edition.theme}"
        return ""

    def get_template(self, request, *args, **kwargs):
        """
        Pick the template from the resolved theme, so a new edition's design is
        a matter of adding a template plus an Edition row -- no code change to
        map it, and no migration to allow the value.
        """
        theme = self.resolved_theme
        if not theme or theme == "default":
            return self.DEFAULT_TEMPLATE

        candidate = f"home/themes/{theme}.html"
        try:
            get_template(candidate)
        except TemplateDoesNotExist:
            logger.warning(
                "HomePage %s wants theme %r but %s is missing; using the default design.",
                self.pk, theme, candidate,
            )
            return self.DEFAULT_TEMPLATE
        return candidate
    
    def save(self, *args, **kwargs):
        """Auto-set slug and title based on conference year if specified."""
        if self.conference_year:
            # For archived years, set slug to the year
            if not self.slug or self.slug == 'home':
                self.slug = str(self.conference_year)
            # Auto-set title if not customized
            if not self.title or self.title == "Home":
                self.title = f"PyCon Nigeria {self.conference_year}"
        return super().save(*args, **kwargs)
    
    @property
    def edition(self):
        """This page's Edition, falling back to the current one."""
        from editions.current import current_year, edition_for_year

        return edition_for_year(self.conference_year or current_year())

    @property
    def display_dates(self):
        """
        The dates to show. The page's own text wins, so an organizer can write
        something the date fields cannot express, otherwise the Edition's dates.
        """
        if self.conference_dates:
            return self.conference_dates
        edition = self.edition
        return edition.dates_display if edition else ""

    @property
    def display_venue(self):
        """The venue to show: this page's text, else the Edition's."""
        if self.conference_location:
            return self.conference_location
        edition = self.edition
        return edition.venue if edition else ""

    @property
    def countdown_target(self):
        """
        The datetime the countdown runs to, or None when there is nothing to
        count down to — no start date, or the conference has already begun.
        """
        from django.utils import timezone

        if not self.show_countdown:
            return None
        edition = self.edition
        if not edition or not edition.starts_on:
            return None
        if edition.starts_on <= timezone.localdate():
            return None
        return edition.starts_on

    @property
    def countdown_days(self):
        """
        Whole days until the first day, so the figure is correct before any
        JavaScript runs and for anyone who has it turned off.
        """
        from django.utils import timezone

        target = self.countdown_target
        if not target:
            return None
        return max((target - timezone.localdate()).days, 0)

    @property
    def conference_phase(self):
        from editions.current import current_year
        from editions.phases import phase_for

        return phase_for(self.conference_year or current_year())

    def get_primary_cta(self):
        """
        The hero's primary button as ``{"label", "url", "phase"}``.

        Resolution order, most explicit first: the manual hero fields, then a
        per-phase override, then the computed default for the phase. That way
        nothing changes for a page that already had its buttons filled in.
        """
        from editions.current import current_year
        from editions.phases import default_cta

        year = self.conference_year or current_year()
        phase, label, url = default_cta(year)

        if self.hero_primary_button_text and self.hero_primary_button_url:
            return {
                "label": self.hero_primary_button_text,
                "url": self.hero_primary_button_url,
                "phase": phase,
                "source": "manual",
            }

        for block in self.phase_ctas:
            value = block.value
            if value.get("phase") == phase:
                return {
                    "label": value.get("label") or label,
                    "url": value.get("url") or url,
                    "phase": phase,
                    "source": "override",
                }

        return {"label": label, "url": url, "phase": phase, "source": "default"}

    def get_sponsors_by_tier(self):
        """
        This edition's published sponsors, grouped by tier in configured order.

        Reads Sponsor records rather than page blocks, so the homepage and the
        sponsorship page show the same list and there is one place to update it.
        Returns ``[(tier, [sponsor, ...]), ...]``.
        """
        from editions.current import current_year
        from sponsors.models import sponsors_by_tier

        return sponsors_by_tier(self.conference_year or current_year())

    def get_ticket_types(self):
        """Fetch ticket types from the tickets app for this page's conference year."""
        from editions.current import current_year
        from tickets.models import TicketType

        year = self.conference_year or current_year()
        ticket_types = (
            TicketType.objects.active()
            .for_year(year)
            .order_by("display_order", "price")
        )
        return [
            {
                "name": tt.name,
                "description": tt.description,
                "current_price": tt.current_price,
                "price": tt.price,
                "early_bird_price": tt.early_bird_price,
                "is_early_bird": tt.early_bird_remaining,
                "remaining": tt.remaining_count,
                "is_sold_out": tt.is_sold_out,
            }
            for tt in ticket_types
        ]

    def get_login_href(self, request=None):
        """Page URL first, then custom URL, then default Login URL."""
        if self.login_link_page_id and self.login_link_page:
            return self.login_link_page.get_url(request=request) if request is not None else self.login_link_page.get_url()
        if self.login_button_url:
            return self.login_button_url
        return reverse("login")

    def get_login_label(self):
        t = (self.login_button_text or "").strip()
        return t if t else "Sign In"

    def get_context(self, request, *args, **kwargs):
        """
        Add theme information, sponsors_by_tier, and ticket_types to context for this HomePage.
        """
        context = super().get_context(request, *args, **kwargs)
        context['page_theme'] = self.theme
        context['page_conference_year'] = self.conference_year
        context['sponsors_by_tier'] = self.get_sponsors_by_tier()
        context['ticket_types'] = self.get_ticket_types()
        context['display_dates'] = self.display_dates
        context['display_venue'] = self.display_venue
        context['countdown_target'] = self.countdown_target
        context['countdown_days'] = self.countdown_days
        context['conference_phase'] = self.conference_phase
        context['primary_cta'] = self.get_primary_cta()
        return context

    content_panels = Page.content_panels + [
        MultiFieldPanel([
            FieldPanel('conference_year'),
            FieldPanel('theme'),
        ], heading="Conference Year & Theme"),
        
        MultiFieldPanel([
            FieldPanel('hero_title'),
            FieldPanel('hero_subtitle'),
            FieldPanel('hero_description'),
            FieldPanel('conference_location'),
            FieldPanel('conference_dates'),
            FieldPanel('hero_primary_button_text'),
            FieldPanel('hero_primary_button_url'),
            FieldPanel('hero_secondary_button_text'),
            FieldPanel('hero_secondary_button_url'),
            FieldPanel('show_countdown'),
            FieldPanel('countdown_label'),
            FieldPanel('phase_ctas'),
        ], heading="Hero Section"),
        
        MultiFieldPanel([
            FieldPanel('conference_section_title'),
            FieldPanel('conference_description'),
        ], heading="Conference Info"),
        
        FieldPanel('features'),
        
        MultiFieldPanel([
            FieldPanel('sponsor_section_title'),
        ], heading="Sponsors"),
        
        MultiFieldPanel([
            FieldPanel('voting_section_title'),
            FieldPanel('voting_deadline'),
            FieldPanel('voting_description'),
            FieldPanel('voting_button_text'),
            FieldPanel('voting_button_url'),
        ], heading="Community Voting"),
        
        MultiFieldPanel([
            FieldPanel("navigation_menu_items"),
            FieldPanel("login_link_page"),
            FieldPanel("login_button_url"),
            FieldPanel("login_button_text"),
        ], heading="Navigation"),
        
        MultiFieldPanel([
            FieldPanel('year_navigation_title'),
            FieldPanel('year_navigation_description'),
        ], heading="Year Navigation"),
        
        MultiFieldPanel([
            FieldPanel('footer_copyright'),
            FieldPanel('footer_links'),
            FieldPanel('newsletter_title'),
            FieldPanel('newsletter_description'),
        ], heading="Footer"),
        
        MultiFieldPanel([
            FieldPanel('twitter_url'),
            FieldPanel('facebook_url'),
            FieldPanel('linkedin_url'),
            FieldPanel('instagram_url'),
            FieldPanel('youtube_url'),
            FieldPanel('github_url'),
        ], heading="Social Media"),
    ]

    class Meta:
        verbose_name = "Home Page"


class NewsletterSubscriber(models.Model):
    """Newsletter subscriber model."""
    email = models.EmailField(unique=True)
    subscribed_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        verbose_name = "Newsletter Subscriber"
        verbose_name_plural = "Newsletter Subscribers"
        ordering = ['-subscribed_at']
    
    def __str__(self):
        return self.email


class StandardPage(Page):
    """A generic, reusable content page for most site pages."""
    intro = RichTextField(blank=True)
    body = StreamField([
        ("heading", blocks.CharBlock(form_classname="full title")),
        ("paragraph", blocks.RichTextBlock()),
        ("image", ImageChooserBlock()),
    ], blank=True, use_json_field=True)

    parent_page_types = ["home.HomePage", "home.StandardPage"]
    subpage_types = ["home.StandardPage"]

    content_panels = Page.content_panels + [
        FieldPanel("intro"),
        FieldPanel("body"),
    ]
    
    def get_parent_homepage(self):
        """
        Traverse up the page tree to find the parent HomePage.
        This allows child pages to inherit theme from their year's homepage.
        """
        parent = self.get_parent()
        
        while parent:
            if isinstance(parent.specific, HomePage):
                return parent.specific
            parent = parent.get_parent()
        
        return None
    
    def get_context(self, request, *args, **kwargs):
        """
        Add parent HomePage theme information to context.
        This allows child pages to use the same theme as their parent year.
        """
        context = super().get_context(request, *args, **kwargs)
        
        # Get parent HomePage to inherit theme
        parent_homepage = self.get_parent_homepage()
        
        if parent_homepage:
            context['parent_homepage'] = parent_homepage
            context['page_theme'] = parent_homepage.theme
            context['page_conference_year'] = parent_homepage.conference_year
        else:
            context['page_theme'] = 'default'
            context['page_conference_year'] = None
        
        return context

    class Meta:
        verbose_name = "Standard Page"


class SponsorPage(Page):
    """
    Sponsorship landing page (CMS-driven). Child of HomePage only.
    Inherits visual theme from the parent HomePage.
    """

    HERO_OVERLAY_CHOICES = [
        ("dark", "Dark overlay (recommended for photos)"),
        ("light", "Light overlay"),
        ("none", "No overlay"),
    ]

    # Hero
    hero_headline = models.CharField(max_length=200)
    hero_subheadline = models.CharField(max_length=300, blank=True)
    hero_intro = RichTextField(blank=True)
    hero_background = models.ForeignKey(
        get_image_model_string(),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Optional full-width hero background image",
    )
    hero_background_alt = models.CharField(
        max_length=255,
        blank=True,
        help_text="Alt text for the hero image (accessibility)",
    )
    hero_overlay_style = models.CharField(
        max_length=20,
        choices=HERO_OVERLAY_CHOICES,
        default="dark",
    )
    hero_event_summary = RichTextField(
        blank=True,
        help_text="Dates, location, format, attendee scale (event overview)",
    )

    # Why sponsor
    why_section_title = models.CharField(
        max_length=200,
        default="Why sponsor PyCon Nigeria?",
    )
    why_intro = RichTextField(blank=True)
    why_benefits = StreamField(
        [("benefit", SponsorBenefitBlock())],
        blank=True,
        use_json_field=True,
        help_text="Key reasons to sponsor (e.g. talent, hiring, brand)",
    )

    # Audience
    audience_section_title = models.CharField(
        max_length=200,
        default="Audience profile",
    )
    audience_intro = RichTextField(blank=True)
    audience_points = StreamField(
        [("point", blocks.CharBlock(max_length=400))],
        blank=True,
        use_json_field=True,
        help_text="Bullet-style audience segments",
    )

    # Packages
    packages_section_title = models.CharField(
        max_length=200,
        default="Sponsorship packages",
    )
    packages_intro = RichTextField(blank=True)
    packages = StreamField(
        [("package", SponsorPackageBlock())],
        blank=True,
        use_json_field=True,
    )
    packages_supplement = RichTextField(
        blank=True,
        help_text="Add-ons, community impact, custom options, etc.",
    )

    # CTA & trust
    cta_section_title = models.CharField(
        max_length=200,
        default="Partner with us",
    )
    cta_body = RichTextField(blank=True)
    cta_button_label = models.CharField(max_length=80, blank=True)
    cta_button_url = models.CharField(
        max_length=500,
        blank=True,
        help_text="Primary button URL or path (mailto: allowed)",
    )
    cta_secondary_label = models.CharField(max_length=80, blank=True)
    cta_secondary_url = models.CharField(
        max_length=500,
        blank=True,
        help_text="Secondary link, e.g. mailto:hello@example.org",
    )
    cta_urgency_line = models.CharField(
        max_length=300,
        blank=True,
        help_text="Short urgency line, e.g. limited slots / early commitment",
    )
    cta_trust_note = RichTextField(
        blank=True,
        help_text="Organizer credibility, nonprofit / community messaging",
    )

    # Prospectus
    prospectus = models.ForeignKey(
        "wagtaildocs.Document",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text=(
            "The sponsorship prospectus, usually a PDF. Upload it under Documents "
            "and choose it here; replacing the file needs no deploy."
        ),
    )
    prospectus_label = models.CharField(
        max_length=80,
        blank=True,
        default="Download the prospectus",
        help_text="Text on the download button.",
    )
    prospectus_note = models.CharField(
        max_length=200,
        blank=True,
        help_text='Optional line under the button, e.g. "PDF, 2.4 MB".',
    )

    # Current sponsors
    show_current_sponsors = models.BooleanField(
        default=True,
        help_text="Show this edition's published sponsors, grouped by tier.",
    )
    current_sponsors_title = models.CharField(
        max_length=200,
        default="Our sponsors",
        help_text="Heading above the sponsor logos.",
    )
    current_sponsors_intro = RichTextField(blank=True)

    parent_page_types = ["home.HomePage"]
    subpage_types = []

    content_panels = Page.content_panels + [
        MultiFieldPanel(
            [
                FieldPanel("hero_headline"),
                FieldPanel("hero_subheadline"),
                FieldPanel("hero_intro"),
                FieldPanel("hero_background"),
                FieldPanel("hero_background_alt"),
                FieldPanel("hero_overlay_style"),
                FieldPanel("hero_event_summary"),
            ],
            heading="Hero",
        ),
        MultiFieldPanel(
            [
                FieldPanel("why_section_title"),
                FieldPanel("why_intro"),
                FieldPanel("why_benefits"),
            ],
            heading="Why sponsor",
        ),
        MultiFieldPanel(
            [
                FieldPanel("audience_section_title"),
                FieldPanel("audience_intro"),
                FieldPanel("audience_points"),
            ],
            heading="Audience",
        ),
        MultiFieldPanel(
            [
                FieldPanel("packages_section_title"),
                FieldPanel("packages_intro"),
                FieldPanel("packages"),
                FieldPanel("packages_supplement"),
            ],
            heading="Packages",
        ),
        MultiFieldPanel(
            [
                FieldPanel("cta_section_title"),
                FieldPanel("cta_body"),
                FieldPanel("cta_button_label"),
                FieldPanel("cta_button_url"),
                FieldPanel("cta_secondary_label"),
                FieldPanel("cta_secondary_url"),
                FieldPanel("cta_urgency_line"),
                FieldPanel("cta_trust_note"),
            ],
            heading="Call to action & trust",
        ),
        MultiFieldPanel(
            [
                FieldPanel("prospectus"),
                FieldPanel("prospectus_label"),
                FieldPanel("prospectus_note"),
            ],
            heading="Prospectus",
        ),
        MultiFieldPanel(
            [
                FieldPanel("show_current_sponsors"),
                FieldPanel("current_sponsors_title"),
                FieldPanel("current_sponsors_intro"),
            ],
            heading="Current sponsors",
        ),
    ]

    def get_parent_homepage(self):
        parent = self.get_parent()
        while parent:
            if isinstance(parent.specific, HomePage):
                return parent.specific
            parent = parent.get_parent()
        return None

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        parent_homepage = self.get_parent_homepage()
        if parent_homepage:
            context["parent_homepage"] = parent_homepage
            context["page_theme"] = parent_homepage.theme
            context["page_conference_year"] = parent_homepage.conference_year
        else:
            context["page_theme"] = "default"
            context["page_conference_year"] = None

        from editions.current import current_year
        from sponsors.models import sponsors_by_tier

        # This page's edition, falling back to the current one.
        year = (parent_homepage.conference_year if parent_homepage else None) or current_year()
        context["sponsor_year"] = year
        context["sponsors_by_tier"] = (
            sponsors_by_tier(year) if self.show_current_sponsors else []
        )
        return context

    class Meta:
        verbose_name = "Sponsor Page"


