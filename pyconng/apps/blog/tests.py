"""
Tests for the blog.

The listing is the part that has to be right: it is the page a newsletter or a
post on X will point at, it is paginated, and it is filtered by tag from the query
string. Everything that reads user input from a URL gets a test for the nonsense
cases, because a 500 on the page an announcement links to is the worst time to
have one.
"""

from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase
from wagtail.models import Page, Site

from editions.current import invalidate
from editions.models import Edition
from quality.audit import audit_html, errors

from blog.models import POSTS_PER_PAGE, BlogIndexPage, BlogPostPage


def build_site():
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


def make_post(index, title, slug, day, tags=(), live=True, year=2026):
    post = BlogPostPage(
        title=title,
        slug=slug,
        summary=f"Summary of {title}.",
        published_on=date(2026, 5, day),
        conference_year=year,
        byline="The organizers",
        live=live,
    )
    index.add_child(instance=post)
    if tags:
        post.tags.add(*tags)
        post.save()
    if live:
        post.save_revision().publish()
    return post


class BlogIndexTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        home = build_site()
        cls.index = BlogIndexPage(
            title="News", slug="news", intro="<p>What we are up to.</p>"
        )
        home.add_child(instance=cls.index)
        cls.index.save_revision().publish()

        cls.older = make_post(cls.index, "Tickets are open", "tickets-open", 1, ["tickets"])
        cls.newer = make_post(cls.index, "Speakers announced", "speakers", 20, ["programme"])
        cls.draft = make_post(cls.index, "Not ready", "draft", 25, live=False)

    def setUp(self):
        invalidate()

    def test_posts_come_out_newest_first(self):
        self.assertEqual(
            [p.slug for p in self.index.published_posts()], ["speakers", "tickets-open"]
        )

    def test_a_draft_is_not_listed(self):
        self.assertNotIn("draft", [p.slug for p in self.index.published_posts()])
        response = self.client.get("/news/")
        self.assertNotContains(response, "Not ready")

    def test_the_listing_renders_live_posts(self):
        response = self.client.get("/news/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Speakers announced")
        self.assertContains(response, "Tickets are open")

    def test_only_tags_in_use_are_offered(self):
        tags = [t.slug for t in self.index.available_tags()]
        self.assertEqual(sorted(tags), ["programme", "tickets"])

    def test_filtering_by_tag_narrows_the_listing(self):
        response = self.client.get("/news/?tag=tickets")
        self.assertContains(response, "Tickets are open")
        self.assertNotContains(response, "Speakers announced")

    def test_an_unknown_tag_says_so_rather_than_erroring(self):
        response = self.client.get("/news/?tag=does-not-exist")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No posts tagged")

    def test_nonsense_page_numbers_do_not_break_the_page(self):
        for value in ("banana", "-3", "0", "99999", ""):
            with self.subTest(page=value):
                response = self.client.get(f"/news/?page={value}")
                self.assertEqual(response.status_code, 200)

    def test_pagination_appears_once_there_are_enough_posts(self):
        for number in range(POSTS_PER_PAGE + 2):
            make_post(self.index, f"Post {number}", f"post-{number}", 2)
        response = self.client.get("/news/")
        self.assertContains(response, "Older")
        self.assertEqual(len(response.context["posts"]), POSTS_PER_PAGE)

        second = self.client.get("/news/?page=2")
        self.assertContains(second, "Newer")

    def test_an_empty_blog_says_something(self):
        BlogPostPage.objects.all().delete()
        response = self.client.get("/news/")
        self.assertContains(response, self.index.empty_text)

    def test_the_listing_has_no_accessibility_errors(self):
        html = self.client.get("/news/").content.decode()
        found = errors(audit_html(html, internal_hosts=("testserver",)))
        self.assertEqual(found, [], "\n".join(str(f) for f in found))


class BlogPostTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        home = build_site()
        cls.index = BlogIndexPage(title="News", slug="news")
        home.add_child(instance=cls.index)
        cls.index.save_revision().publish()

        cls.post = make_post(cls.index, "Speakers announced", "speakers", 20, ["programme"])
        cls.post.body = [("paragraph", "<p>Here is the programme.</p>")]
        cls.post.save()
        cls.post.save_revision().publish()

        cls.same_tag = make_post(cls.index, "More speakers", "more-speakers", 21, ["programme"])
        cls.other = make_post(cls.index, "Tickets are open", "tickets-open", 1, ["tickets"])

    def setUp(self):
        invalidate()

    def test_the_post_renders_its_body_and_attribution(self):
        response = self.client.get("/news/speakers/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Here is the programme.")
        self.assertContains(response, "The organizers")
        self.assertContains(response, "20 May 2026")

    def test_related_posts_prefer_a_shared_tag(self):
        related = list(self.post.related_posts())
        self.assertIn(self.same_tag, related)
        self.assertNotIn(self.other, related)

    def test_related_posts_fall_back_to_recent_ones_with_no_tags(self):
        """A post with nothing to suggest is a dead end, so it suggests something."""
        untagged = make_post(self.index, "A note", "a-note", 15)
        related = list(untagged.related_posts())
        self.assertTrue(related)
        self.assertNotIn(untagged, related)

    def test_a_hero_image_without_alt_text_is_refused(self):
        from wagtail.images import get_image_model

        image = get_image_model()(title="Stage", file="images/stage.png", width=10, height=10)
        image.save()
        self.post.hero_image = image
        self.post.hero_image_alt = ""
        with self.assertRaises(ValidationError) as caught:
            self.post.full_clean()
        self.assertIn("hero_image_alt", caught.exception.error_dict)

    def test_the_summary_is_used_as_the_page_description(self):
        self.assertEqual(self.post.search_description_or_summary, self.post.summary)
        self.post.search_description = "A hand-written description."
        self.assertEqual(self.post.search_description_or_summary, "A hand-written description.")

    def test_the_post_has_no_accessibility_errors(self):
        html = self.client.get("/news/speakers/").content.decode()
        found = errors(audit_html(html, internal_hosts=("testserver",)))
        self.assertEqual(found, [], "\n".join(str(f) for f in found))
