"""
Tests for the frontend audit, and the audit itself run over real pages.

Two jobs. The unit tests pin each rule's behaviour so the audit stays trustworthy
-- a check that reports the wrong thing is worse than no check. The page tests
run the audit over rendered pages built from fixtures, which is the regression
guard: remove the skip link, drop the <main> landmark or break a page title again
and CI says so.

Fixtures rather than whatever is in the database on purpose. Content problems --
the development URLs somebody pasted into the navigation menu, for instance --
belong to `manage.py audit_frontend` against a real database, not to a test.
"""

from django.test import TestCase
from wagtail.models import Page, Site

from quality.audit import audit_html, collapse, errors, summarise
from quality.management.commands.rewrite_dev_urls import to_local_path

MINIMAL = """<!DOCTYPE html><html lang="en"><head><title>Page - Site</title>
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body><a class="skip-link" href="#main-content">Skip to main content</a>
<main id="main-content"><h1>Page</h1>{body}</main></body></html>"""


def rules_fired(html, severity=None):
    """The set of rule names that fired, optionally filtered by severity."""
    findings = audit_html(html)
    if severity:
        findings = [f for f in findings if f.severity == severity]
    return {f.rule for f in findings}


class AuditRuleTests(TestCase):
    def test_a_clean_page_reports_nothing(self):
        self.assertEqual(rules_fired(MINIMAL.format(body="<p>Hello.</p>")), set())

    def test_title_missing_the_page_name_is_an_error(self):
        # The exact shape base_2026.html shipped: the suffix with nothing before it.
        html = MINIMAL.format(body="").replace("<title>Page - Site</title>", "<title> - PyCon Nigeria 2026</title>")
        self.assertIn("page-title", rules_fired(html, "error"))

    def test_title_naming_the_site_alone_is_allowed(self):
        html = MINIMAL.format(body="").replace("<title>Page - Site</title>", "<title>PyCon Nigeria 2026</title>")
        self.assertNotIn("page-title", rules_fired(html))

    def test_site_name_twice_in_the_title_warns(self):
        html = MINIMAL.format(body="").replace(
            "<title>Page - Site</title>",
            "<title>Tickets - PyCon Nigeria 2026 - PyCon Nigeria 2026</title>",
        )
        self.assertIn("title-repeats-site", rules_fired(html, "warning"))

    def test_missing_lang_is_an_error(self):
        self.assertIn("html-lang", rules_fired(MINIMAL.format(body="").replace('<html lang="en">', "<html>"), "error"))

    def test_missing_main_landmark_is_an_error(self):
        html = MINIMAL.format(body="").replace("<main id=\"main-content\">", "<div id=\"main-content\">").replace("</main>", "</div>")
        self.assertIn("main-landmark", rules_fired(html, "error"))

    def test_two_main_landmarks_is_an_error(self):
        self.assertIn("main-landmark", rules_fired(MINIMAL.format(body="<main>Second</main>"), "error"))

    def test_skip_link_must_come_first(self):
        html = MINIMAL.format(body="").replace(
            '<a class="skip-link" href="#main-content">Skip to main content</a>',
            '<a href="/somewhere/">Home</a>',
        )
        self.assertIn("skip-link", rules_fired(html, "error"))

    def test_skip_link_to_a_missing_target_is_an_error(self):
        html = MINIMAL.format(body="").replace('href="#main-content"', 'href="#nowhere"')
        self.assertIn("skip-link", rules_fired(html, "error"))

    def test_image_without_alt_is_an_error(self):
        self.assertIn("img-alt", rules_fired(MINIMAL.format(body='<img src="a.png">'), "error"))

    def test_empty_alt_is_accepted_for_a_decorative_image(self):
        html = MINIMAL.format(body='<img src="a.png" alt="" width="10" height="10" loading="lazy">')
        self.assertEqual(rules_fired(html), set())

    def test_non_numeric_dimension_is_an_error(self):
        html = MINIMAL.format(body='<img src="a.png" alt="" width="320" height="auto">')
        self.assertIn("bad-dimension", rules_fired(html, "error"))

    def test_missing_dimensions_and_loading_warn(self):
        fired = rules_fired(MINIMAL.format(body='<img src="a.png" alt="">'), "warning")
        self.assertIn("img-dimensions", fired)
        self.assertIn("img-loading", fired)

    def test_icon_only_button_without_a_name_is_an_error(self):
        html = MINIMAL.format(body='<button><svg aria-hidden="true"></svg></button>')
        self.assertIn("control-name", rules_fired(html, "error"))

    def test_aria_label_names_an_icon_only_button(self):
        html = MINIMAL.format(body='<button aria-label="Open menu"><svg aria-hidden="true"></svg></button>')
        self.assertNotIn("control-name", rules_fired(html))

    def test_alpine_bound_aria_label_counts_as_a_name(self):
        # The mobile menu button sets its label from state; it is never nameless.
        html = MINIMAL.format(body="""<button :aria-label="isOpen ? 'Close' : 'Open'"><svg aria-hidden="true"></svg></button>""")
        self.assertNotIn("control-name", rules_fired(html))

    def test_unlabelled_field_is_an_error(self):
        html = MINIMAL.format(body='<form><input type="text" name="query" placeholder="Search"></form>')
        self.assertIn("field-label", rules_fired(html, "error"))

    def test_label_for_satisfies_the_field_rule(self):
        html = MINIMAL.format(
            body='<form><label for="q">Search</label><input type="text" id="q" name="q"></form>'
        )
        self.assertNotIn("field-label", rules_fired(html))

    def test_hidden_and_submit_inputs_need_no_label(self):
        html = MINIMAL.format(
            body='<form><input type="hidden" name="csrf"><input type="submit" value="Go"></form>'
        )
        self.assertNotIn("field-label", rules_fired(html))

    def test_dead_link_is_an_error(self):
        self.assertIn("dead-link", rules_fired(MINIMAL.format(body='<a href="#">Read the CoC</a>'), "error"))

    def test_duplicate_id_is_an_error(self):
        html = MINIMAL.format(body='<p id="x">a</p><p id="x">b</p>')
        self.assertIn("duplicate-id", rules_fired(html, "error"))

    def test_aria_hidden_around_something_focusable_is_an_error(self):
        html = MINIMAL.format(body='<div aria-hidden="true"><a href="/x/">Hidden link</a></div>')
        self.assertIn("aria-hidden-focusable", rules_fired(html, "error"))

    def test_blocked_zoom_is_an_error(self):
        html = MINIMAL.format(body="").replace(
            'content="width=device-width, initial-scale=1"',
            'content="width=device-width, initial-scale=1, user-scalable=no"',
        )
        self.assertIn("viewport-zoom", rules_fired(html, "error"))

    def test_blocking_script_in_head_is_an_error(self):
        html = MINIMAL.format(body="").replace("</head>", '<script src="/static/a.js"></script></head>')
        self.assertIn("blocking-script", rules_fired(html, "error"))

    def test_deferred_and_module_scripts_in_head_are_fine(self):
        html = MINIMAL.format(body="").replace(
            "</head>",
            '<script src="/static/a.js" defer></script>'
            '<script type="module" src="/static/b.js"></script></head>',
        )
        self.assertNotIn("blocking-script", rules_fired(html))

    def test_development_url_is_an_error(self):
        html = MINIMAL.format(body='<a href="http://127.0.0.1:8000/cfp/">Speaking</a>')
        self.assertIn("dev-url", rules_fired(html, "error"))

    def test_third_party_subresource_warns_but_a_link_does_not(self):
        script = MINIMAL.format(body="") .replace(
            "</body>", '<script type="module" src="https://cdn.jsdelivr.net/npm/alpinejs@3/x.js"></script></body>'
        )
        self.assertIn("third-party-asset", rules_fired(script, "warning"))
        link = MINIMAL.format(body='<a href="https://python.org/">Python</a>')
        self.assertNotIn("third-party-asset", rules_fired(link))

    def test_google_fonts_is_an_allowed_host(self):
        html = MINIMAL.format(body="").replace(
            "</head>",
            '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Syne&display=swap"></head>',
        )
        self.assertNotIn("third-party-asset", rules_fired(html))

    def test_skipped_heading_level_warns(self):
        self.assertIn("heading-order", rules_fired(MINIMAL.format(body="<h3>Deep</h3>"), "warning"))

    def test_blank_target_without_noopener_warns(self):
        html = MINIMAL.format(body='<a href="https://x.com/pyconng" target="_blank">Twitter</a>')
        self.assertIn("blank-noopener", rules_fired(html, "warning"))

    def test_malformed_markup_does_not_raise(self):
        # Templates produce unbalanced markup often enough that this must hold.
        audit_html("<html><body><div><p>unclosed<main><h1>x</h1>")

    def test_collapse_folds_repeats_and_keeps_singles(self):
        findings = audit_html(
            MINIMAL.format(body='<img src="a.png"><img src="a.png"><a href="#">x</a>')
        )
        collapsed = collapse(findings)
        self.assertLess(len(collapsed), len(findings))
        messages = [f.message for f in collapsed if f.rule == "img-alt"]
        self.assertEqual(len(messages), 1)
        self.assertIn("2 occurrences", messages[0])

    def test_summarise_counts_errors_by_rule(self):
        counts = summarise(audit_html(MINIMAL.format(body='<img src="a.png"><a href="#">x</a>')))
        self.assertEqual(counts.get("img-alt"), 1)
        self.assertEqual(counts.get("dead-link"), 1)
        self.assertNotIn("img-loading", counts)  # A warning, not an error.


class DevUrlRewriteTests(TestCase):
    def test_a_development_url_becomes_a_path(self):
        self.assertEqual(to_local_path("http://127.0.0.1:8000/cfp/"), "/cfp/")
        self.assertEqual(to_local_path("http://localhost:8000/coc/"), "/coc/")
        self.assertEqual(to_local_path("https://0.0.0.0/x"), "/x")

    def test_a_bare_development_host_becomes_the_root(self):
        self.assertEqual(to_local_path("http://127.0.0.1:8000/"), "/")
        self.assertEqual(to_local_path("http://127.0.0.1:8000"), "/")

    def test_a_real_url_is_left_alone(self):
        self.assertIsNone(to_local_path("https://pycon.ng/cfp/"))
        self.assertIsNone(to_local_path("/cfp/"))
        self.assertIsNone(to_local_path(""))
        self.assertIsNone(to_local_path(None))


class WebfontTests(TestCase):
    def test_every_theme_asks_for_display_swap(self):
        from editions.webfonts import THEME_WEBFONTS

        for theme, url in THEME_WEBFONTS.items():
            with self.subTest(theme=theme):
                self.assertIn("display=swap", url)
                self.assertTrue(url.startswith("https://fonts.googleapis.com/"))

    def test_an_unknown_theme_falls_back_rather_than_going_bare(self):
        from editions.webfonts import THEME_WEBFONTS, fonts_href

        self.assertEqual(fonts_href("2027-does-not-exist"), THEME_WEBFONTS["2026"])
        self.assertIsNone(fonts_href(None))

    def test_no_theme_css_still_imports_fonts(self):
        """
        The regression that started this: an @import after @tailwind utilities is
        dropped by PostCSS, so the typefaces never loaded. They belong in the
        base template now, and must not creep back into the CSS.
        """
        from pathlib import Path

        from django.conf import settings

        source = Path(settings.PROJECT_DIR) / "static" / "css" / "src"
        for css in sorted(source.glob("input_*.css")):
            with self.subTest(css=css.name):
                self.assertNotIn("fonts.googleapis.com", css.read_text())


class TemplateSourceTests(TestCase):
    """
    Checks over the templates themselves, for things a rendered page cannot show.

    The audit only sees pages it can reach anonymously. A dead link on the ticket
    purchase page, behind a login and a sale window, is exactly the kind of thing
    that stayed broken for a year -- so this looks at the source instead.
    """

    def _templates(self):
        from pathlib import Path

        from django.conf import settings

        root = Path(settings.BASE_DIR)
        for path in root.rglob("*.html"):
            text = str(path)
            if any(part in text for part in ("node_modules", "/venv/", "/static/", "/.git/")):
                continue
            yield path

    def test_no_template_ships_a_link_to_nowhere(self):
        """
        href="#" announces as a link, takes focus and does nothing. In this
        codebase every one of them has been a to-do somebody forgot.
        """
        offenders = []
        for path in self._templates():
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if 'href="#"' in line and "@click" not in line and "x-on:click" not in line:
                    offenders.append(f"{path}:{number}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_no_template_ships_a_development_url(self):
        offenders = []
        for path in self._templates():
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if "127.0.0.1" in line or "localhost:" in line:
                    offenders.append(f"{path}:{number}  {line.strip()[:80]}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_every_base_template_offers_the_skip_link_and_main_landmark(self):
        """
        base_2024, base_2025 and base_2026 are thin children of base.html. If one
        of them stops extending it, the accessibility work silently comes undone
        for that year -- and there may be no page of that year to audit.
        """
        from django.template.loader import get_template

        from editions.webfonts import fonts_href

        for name in ("base.html", "base_2024.html", "base_2025.html", "base_2026.html"):
            with self.subTest(template=name):
                html = get_template(name).render(
                    {
                        "site_name": "PyCon Nigeria",
                        "conference_theme": "2026",
                        # Supplied by the conference context processor in a real
                        # request; passed here so the font link is exercised too.
                        "fonts_href": fonts_href("2026"),
                    }
                )
                self.assertIn('<main id="main-content">', html)
                self.assertIn('href="#main-content"', html)
                self.assertIn("fonts.googleapis.com/css2", html)
                self.assertIn("js/vendor/alpine.esm.js", html)


class ContrastTests(TestCase):
    """
    WCAG 1.4.3 over the declared palettes.

    This is the check that stops a new edition shipping an unreadable button:
    change --theme-primary to something too light and the test says so, with the
    ratio, before anyone has to squint at a screen to find out.
    """

    def _themes(self):
        from pathlib import Path

        from django.conf import settings

        source = Path(settings.PROJECT_DIR) / "static" / "css" / "src"
        for css in sorted(source.glob("input_*.css")):
            yield css.stem.replace("input_", ""), css.read_text()

    def test_known_ratios(self):
        from quality.contrast import contrast_ratio

        self.assertAlmostEqual(contrast_ratio("#000000", "#ffffff"), 21.0, places=2)
        self.assertAlmostEqual(contrast_ratio("#ffffff", "#ffffff"), 1.0, places=2)
        # Both orders must give the same ratio.
        self.assertAlmostEqual(
            contrast_ratio("#0f766e", "#ffffff"), contrast_ratio("#ffffff", "#0f766e")
        )

    def test_short_hex_and_named_colours_resolve(self):
        from quality.contrast import contrast_ratio, resolve

        self.assertAlmostEqual(contrast_ratio("#fff", "#000"), 21.0, places=2)
        self.assertEqual(resolve("white", {}), "#ffffff")
        self.assertEqual(resolve("var(--x)", {"--x": "#123456"}), "#123456")
        self.assertEqual(resolve("var(--missing, #abcdef)", {}), "#abcdef")
        self.assertIsNone(resolve("var(--missing)", {}))

    def test_the_class_scope_wins_over_the_attribute_scope(self):
        """
        data-theme sits on <html> and theme-<year> on <body>, so where both
        declare a token the class is the nearer ancestor for all content. Reading
        it the other way round reports contrast for a colour nobody sees.
        """
        from quality.contrast import parse_palette

        css = """
        :root { --theme-primary: #0ea5e9; }
        .theme-2026 { --theme-primary: #0F766E; }
        [data-theme="2026"] { --theme-primary: #14b8a6; }
        """
        self.assertEqual(parse_palette(css, "2026")["--theme-primary"], "#0F766E")

    def test_every_declared_pair_meets_aa(self):
        from quality.contrast import audit_palette

        failures = []
        for theme, css in self._themes():
            for pair, ratio, passes in audit_palette(css, theme):
                if not passes:
                    failures.append(
                        f"{theme}: {pair.component} is {ratio}:1, needs {pair.minimum}"
                    )
        self.assertEqual(failures, [], "\n".join(failures))

    def test_at_least_one_pair_is_checkable_per_theme(self):
        """Guards the parser: a palette silently failing to parse would pass above."""
        from quality.contrast import audit_palette

        for theme, css in self._themes():
            with self.subTest(theme=theme):
                self.assertTrue(audit_palette(css, theme))


class RenderedPageAuditTests(TestCase):
    """The audit over real rendered pages, on fixture content."""

    @classmethod
    def setUpTestData(cls):
        from editions.current import invalidate
        from editions.models import Edition
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
        # Wagtail's initial migration leaves a welcome page at the site root; a
        # HomePage has to replace it for these templates to be exercised.
        home = HomePage(
            title="PyCon Nigeria 2026",
            slug="home-2026",
            hero_title="PyCon Nigeria 2026",
            hero_subtitle="The premier Python conference in Nigeria.",
        )
        root.add_child(instance=home)
        home.save_revision().publish()

        site = Site.objects.get(is_default_site=True)
        site.root_page = home
        site.save()
        cls.home = home

    def setUp(self):
        from editions.current import invalidate

        invalidate()

    def _audit(self, url):
        response = self.client.get(url, follow=True)
        self.assertEqual(response.status_code, 200, url)
        return audit_html(response.content.decode(), internal_hosts=("testserver",))

    def test_the_homepage_has_no_accessibility_errors(self):
        found = errors(self._audit("/"))
        self.assertEqual(found, [], "\n".join(str(f) for f in found))

    def test_app_landing_pages_have_no_accessibility_errors(self):
        for url in ("/cfp/", "/tickets/", "/grants/", "/search/",
                    "/accounts/login/", "/accounts/signup/"):
            with self.subTest(url=url):
                found = errors(self._audit(url))
                self.assertEqual(found, [], "\n".join(str(f) for f in found))

    def test_every_page_offers_a_skip_link_to_its_main_landmark(self):
        for url in ("/", "/cfp/", "/accounts/login/"):
            with self.subTest(url=url):
                html = self.client.get(url, follow=True).content.decode()
                self.assertIn('href="#main-content"', html)
                self.assertIn('<main id="main-content">', html)

    def test_the_page_title_names_the_page(self):
        html = self.client.get("/cfp/", follow=True).content.decode()
        self.assertIn("<title>", html)
        title = html.split("<title>")[1].split("</title>")[0]
        self.assertIn("Call for Proposals", title)
        self.assertEqual(title.count("PyCon Nigeria"), 1)

    def test_the_theme_fonts_are_linked_from_the_page(self):
        html = self.client.get("/", follow=True).content.decode()
        self.assertIn("fonts.googleapis.com/css2", html)
        self.assertIn("Syne", html)

    def test_alpine_is_served_from_this_site(self):
        html = self.client.get("/", follow=True).content.decode()
        self.assertIn("js/vendor/alpine.esm.js", html)
        self.assertNotIn("cdn.jsdelivr.net", html)

    def test_an_archived_year_has_no_accessibility_errors(self):
        """
        /2024/ renders through base_2024.html, which is now a thin child of
        base.html. This is the test that would notice if that inheritance broke
        and the archive lost its skip link or its main landmark.
        """
        from editions.current import invalidate
        from editions.models import Edition
        from program.models import YearArchivePage

        Edition.objects.update_or_create(
            year=2024,
            defaults={
                "name": "PyCon Nigeria 2024",
                "theme": "2024",
                "is_current": False,
                "is_published": True,
            },
        )
        invalidate()

        archive = YearArchivePage(
            title="PyCon Nigeria 2024",
            slug="2024",
            conference_year=2024,
            intro="<p>A look back at 2024.</p>",
        )
        self.home.add_child(instance=archive)
        archive.save_revision().publish()

        response = self.client.get("/2024/", follow=True)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('<main id="main-content">', html)
        self.assertIn('href="#main-content"', html)
        # The 2024 theme's own typefaces, not the house style's.
        self.assertIn("Space+Grotesk", html)
        found = errors(audit_html(html, internal_hosts=("testserver",)))
        self.assertEqual(found, [], "\n".join(str(f) for f in found))

    def test_the_footer_reads_its_values_from_the_homepage(self):
        self.home.newsletter_title = "Stay tuned"
        self.home.twitter_url = "https://x.com/pyconng"
        self.home.save()
        self.home.save_revision().publish()

        html = self.client.get("/cfp/", follow=True).content.decode()
        # /cfp/ is not a Wagtail page, which is exactly where the bare names in
        # the footer template used to resolve to nothing.
        self.assertIn("Stay tuned", html)
        self.assertIn("https://x.com/pyconng", html)
