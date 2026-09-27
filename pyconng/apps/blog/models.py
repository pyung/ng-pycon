"""
The blog: announcements that belong to the conference rather than to a timeline.

Every announcement so far has gone out on LinkedIn and X and nowhere else, which
means the record of how an edition was run lives on two platforms the community
does not own, cannot search properly, and cannot read without an account. A post
here is a page with a permanent URL that a newsletter or a tweet can point at.

Posts are scoped to an edition like everything else, so 2027's announcements do
not silently appear in 2026's archive.
"""

from django.db import models
from django.utils import timezone
from modelcluster.contrib.taggit import ClusterTaggableManager
from modelcluster.fields import ParentalKey
from taggit.models import TaggedItemBase
from wagtail.admin.panels import FieldPanel, MultiFieldPanel
from wagtail.fields import RichTextField, StreamField
from wagtail.images import get_image_model_string
from wagtail.models import Page
from wagtail.search import index

from editions.current import current_year
from editions.validators import validate_edition_year
from info.blocks import COMMON_BLOCKS

#: How many posts a listing page shows before paginating. Small on purpose: the
#: listing is read on a phone on a slow connection more often than not.
POSTS_PER_PAGE = 10


class BlogPostTag(TaggedItemBase):
    content_object = ParentalKey(
        "blog.BlogPostPage", related_name="tagged_items", on_delete=models.CASCADE
    )


class BlogIndexPage(Page):
    """The listing. Filterable by tag, paginated, newest first."""

    intro = RichTextField(blank=True)
    empty_text = models.CharField(
        max_length=300,
        default="Nothing here yet. Announcements will appear on this page.",
        help_text="Shown when there are no posts, so the page is never blank.",
    )

    parent_page_types = ["home.HomePage"]
    subpage_types = ["blog.BlogPostPage"]

    content_panels = Page.content_panels + [
        FieldPanel("intro"),
        FieldPanel("empty_text"),
    ]

    class Meta:
        verbose_name = "Blog index page"

    def published_posts(self):
        """Live posts under this index, newest first."""
        return (
            BlogPostPage.objects.child_of(self)
            .live()
            .public()
            .order_by("-published_on", "-pk")
        )

    def get_context(self, request, *args, **kwargs):
        from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator

        context = super().get_context(request, *args, **kwargs)
        posts = self.published_posts()

        tag = (request.GET.get("tag") or "").strip()
        if tag:
            posts = posts.filter(tags__slug=tag).distinct()

        paginator = Paginator(posts, POSTS_PER_PAGE)
        try:
            page_number = paginator.page(request.GET.get("page"))
        except PageNotAnInteger:
            page_number = paginator.page(1)
        except EmptyPage:
            # A link to a page that no longer exists shows the last one rather
            # than a 404: posts move between pages as new ones are published.
            page_number = paginator.page(paginator.num_pages)

        context["posts"] = page_number
        context["paginator"] = paginator
        context["selected_tag"] = tag
        context["tags"] = self.available_tags()
        return context

    def available_tags(self):
        """Tags actually used by live posts, alphabetically."""
        from taggit.models import Tag

        return (
            Tag.objects.filter(
                blog_blogposttag_items__content_object__in=self.published_posts()
            )
            .distinct()
            .order_by("name")
        )


class BlogPostPage(Page):
    """One post."""

    published_on = models.DateField(
        default=timezone.localdate,
        help_text=(
            "The date shown to readers. Separate from Wagtail's own publish time so "
            "a post written in advance carries the date it is about."
        ),
    )
    conference_year = models.IntegerField(
        validators=[validate_edition_year],
        default=current_year,
        help_text="Which edition this post belongs to.",
    )
    byline = models.CharField(
        max_length=200,
        blank=True,
        help_text=(
            "Who wrote it, as you want it to read. Free text rather than an account, "
            "because posts are often written by a team or by somebody without one."
        ),
    )
    summary = models.CharField(
        max_length=300,
        help_text=(
            "One or two sentences. Used in the listing, in search results and as the "
            "page description, so write it for someone who has not opened the post."
        ),
    )
    hero_image = models.ForeignKey(
        get_image_model_string(),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    hero_image_alt = models.CharField(
        max_length=200,
        blank=True,
        help_text="What the image shows. Required if an image is set.",
    )
    body = StreamField(COMMON_BLOCKS, blank=True, use_json_field=True)
    tags = ClusterTaggableManager(through=BlogPostTag, blank=True)

    parent_page_types = ["blog.BlogIndexPage"]
    subpage_types = []

    search_fields = Page.search_fields + [
        index.SearchField("summary"),
        index.SearchField("body"),
        index.SearchField("byline"),
    ]

    content_panels = Page.content_panels + [
        MultiFieldPanel(
            [
                FieldPanel("published_on"),
                FieldPanel("conference_year"),
                FieldPanel("byline"),
            ],
            heading="Attribution",
        ),
        FieldPanel("summary"),
        MultiFieldPanel(
            [FieldPanel("hero_image"), FieldPanel("hero_image_alt")],
            heading="Hero image",
        ),
        FieldPanel("body"),
        FieldPanel("tags"),
    ]

    class Meta:
        verbose_name = "Blog post"
        ordering = ["-published_on"]

    def clean(self):
        """An image without alt text is a hole in the page for a screen reader."""
        from django.core.exceptions import ValidationError

        super().clean()
        if self.hero_image and not (self.hero_image_alt or "").strip():
            raise ValidationError(
                {
                    "hero_image_alt": (
                        "Describe the image for someone who cannot see it. If it is "
                        "purely decorative, leave the image out instead."
                    )
                }
            )

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        context["index_page"] = self.get_parent().specific
        context["related_posts"] = self.related_posts()
        return context

    def related_posts(self, limit=3):
        """
        Other posts sharing a tag, newest first; recent posts if it has no tags.

        Falls back rather than showing nothing: a post with no siblings to suggest
        is a dead end, and the point of the page is to lead somewhere.
        """
        siblings = (
            BlogPostPage.objects.live()
            .public()
            .sibling_of(self)
            .exclude(pk=self.pk)
            .order_by("-published_on", "-pk")
        )
        tag_ids = list(self.tags.values_list("id", flat=True))
        if tag_ids:
            tagged = siblings.filter(tags__id__in=tag_ids).distinct()
            if tagged.exists():
                return tagged[:limit]
        return siblings[:limit]

    @property
    def search_description_or_summary(self):
        return self.search_description or self.summary
