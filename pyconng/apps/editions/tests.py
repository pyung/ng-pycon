"""
Tests for the edition's design hooks: the palette, and which theme renders.

Three claims are worth pinning, because all three were asserted to the organizers
and two of them were not true before this pass.

**The palette is editable without a deploy.** Three colour fields on the Edition
are emitted as CSS custom properties, so retuning them is an admin edit. It used
to be that those fields existed, were documented as the palette, and changed
almost nothing: the real colours were hard-coded in each theme's stylesheet.

**A colour field cannot inject CSS.** The values land inside a ``<style>`` block,
where Django's HTML autoescaping does not help, because CSS is not an HTML
context. Both the field validator and the render-time filter are tested, and the
render-time one matters most: it is what protects rows that predate the validator
and anything written by a data import.

**The design follows the theme, not the year.** ``Edition.theme`` already chose
the stylesheet, the fonts and the homepage body template; the base template was
keyed on the year instead, which worked only because every theme slug so far
equals its year. An edition reusing another's design is the case that broke.
"""

from django.core.exceptions import ValidationError
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase

from editions.current import invalidate
from editions.models import Edition
from editions.validators import validate_hex_color
from pyconng.context_processors import conference_context


def make_edition(year, theme, **kwargs):
    defaults = {
        "name": f"PyCon Nigeria {year}",
        "theme": theme,
        "is_published": True,
        "is_current": False,
    }
    defaults.update(kwargs)
    obj, _ = Edition.objects.update_or_create(year=year, defaults=defaults)
    invalidate()
    return obj


class HexColorValidatorTests(TestCase):
    def test_six_and_three_digit_hex_are_accepted(self):
        for value in ("#0E6B3D", "#3ddc84", "#fff", "#ABC"):
            with self.subTest(value=value):
                validate_hex_color(value)

    def test_a_colour_name_is_refused(self):
        """
        Valid CSS, but not a value this can safely interpolate -- and allowing
        names is the thin end of allowing anything.
        """
        with self.assertRaises(ValidationError):
            validate_hex_color("red")

    def test_a_short_or_long_hex_is_refused(self):
        for value in ("#0E6B3", "#0E6B3DD", "0E6B3D", "#"):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    validate_hex_color(value)

    def test_a_value_that_would_escape_the_rule_is_refused(self):
        """The reason this validator exists."""
        for value in (
            "teal;}body{display:none}",
            "#fff</style><script>alert(1)</script>",
            "#fff;background:url(http://example.com/x)",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    validate_hex_color(value)

    def test_empty_is_refused(self):
        with self.assertRaises(ValidationError):
            validate_hex_color("")


class CssVariableTests(TestCase):
    def setUp(self):
        self.edition = make_edition(2026, "2026")

    def test_all_three_colours_become_properties(self):
        self.edition.primary_color = "#0E6B3D"
        self.edition.secondary_color = "#3DDC84"
        self.edition.accent_color = "#8A4E06"
        self.assertEqual(
            self.edition.css_variables(),
            [
                ("--theme-primary", "#0E6B3D"),
                ("--theme-secondary", "#3DDC84"),
                ("--theme-accent", "#8A4E06"),
            ],
        )

    def test_an_unsafe_value_is_dropped_rather_than_emitted(self):
        """
        The field validator catches this on entry. This is the second line: a row
        written before the validator, or by an import that bypassed it, must not
        reach the stylesheet.
        """
        self.edition.primary_color = "teal;}body{display:none}"
        properties = dict(self.edition.css_variables())
        self.assertNotIn("--theme-primary", properties)
        # And the others still come through, so one bad field is not fatal.
        self.assertIn("--theme-secondary", properties)

    def test_whitespace_is_tolerated(self):
        self.edition.primary_color = "  #0E6B3D  "
        self.assertIn(("--theme-primary", "#0E6B3D"), self.edition.css_variables())

    def test_an_edition_with_no_colours_emits_nothing(self):
        self.edition.primary_color = ""
        self.edition.secondary_color = ""
        self.edition.accent_color = ""
        self.assertEqual(self.edition.css_variables(), [])


class PaletteRenderingTests(TestCase):
    """What actually reaches the page, which is the deploy-free claim."""

    def _render(self, request_path="/"):
        request = RequestFactory().get(request_path)
        return render_to_string("base.html", conference_context(request))

    def setUp(self):
        self.edition = make_edition(
            2026, "2026", is_current=True,
            primary_color="#0E6B3D", secondary_color="#3DDC84", accent_color="#8A4E06",
        )

    def test_the_palette_reaches_the_rendered_page(self):
        html = self._render()
        self.assertIn("--theme-primary: #0E6B3D;", html)
        self.assertIn("--theme-secondary: #3DDC84;", html)
        self.assertIn("--theme-accent: #8A4E06;", html)

    def test_changing_a_colour_changes_the_page_with_no_code_change(self):
        """The whole point: a palette edit is an admin edit."""
        self.assertIn("--theme-primary: #0E6B3D;", self._render())
        self.edition.primary_color = "#B91C1C"
        self.edition.save()
        invalidate()
        html = self._render()
        self.assertIn("--theme-primary: #B91C1C;", html)
        self.assertNotIn("#0E6B3D", html)

    def test_the_palette_comes_after_the_stylesheet_so_it_wins(self):
        """
        An override declared before the stylesheet it overrides is not an
        override. Source order decides this one, so the order is the test.
        """
        html = self._render()
        self.assertLess(
            html.index("css/2026.css"),
            html.index("--theme-primary"),
        )

    def test_an_unsafe_stored_colour_does_not_reach_the_page(self):
        Edition.objects.filter(pk=self.edition.pk).update(
            primary_color="#fff</style><script>alert(1)</script>"
        )
        invalidate()
        html = self._render()
        self.assertNotIn("<script>alert(1)", html)
        self.assertNotIn("--theme-primary:", html)
        # The page still renders, and the stylesheet's own default stands.
        self.assertIn("css/2026.css", html)

    def test_no_style_block_at_all_when_nothing_is_usable(self):
        """
        Asserted on the emitted *declarations*, not on the token names: the nav
        references var(--theme-primary) for its focus ring, so the names appear
        on the page whether or not this block does.
        """
        Edition.objects.filter(pk=self.edition.pk).update(
            primary_color="x", secondary_color="y", accent_color="z"
        )
        invalidate()
        html = self._render()
        for prop in ("--theme-primary:", "--theme-secondary:", "--theme-accent:"):
            self.assertNotIn(prop, html)


class BaseTemplateKeyingTests(TestCase):
    """Which chrome renders, and the case the old keying got wrong."""

    def _base_template(self, path):
        return conference_context(RequestFactory().get(path))["base_template"]

    def setUp(self):
        make_edition(2026, "2026", is_current=True)
        make_edition(2025, "2025")

    def test_each_edition_gets_its_own_base_template(self):
        self.assertEqual(self._base_template("/"), "base_2026.html")
        self.assertEqual(self._base_template("/2025/"), "base_2025.html")

    def test_an_edition_reusing_another_design_gets_that_design(self):
        """
        The bug. Keyed on the year, 2025 would have loaded the 2026 stylesheet
        and fonts under base_2025.html's chrome -- a page half in each design.
        """
        make_edition(2025, "2026")
        self.assertEqual(self._base_template("/2025/"), "base_2026.html")

    def test_an_unknown_theme_falls_back_rather_than_erroring(self):
        """A new edition must be able to open before its template exists."""
        make_edition(2025, "brand-new-look")
        self.assertEqual(self._base_template("/2025/"), "base.html")

    def test_the_stylesheet_and_the_chrome_agree(self):
        make_edition(2025, "2026")
        context = conference_context(RequestFactory().get("/2025/"))
        self.assertEqual(context["conference_theme"], "2026")
        self.assertEqual(context["base_template"], "base_2026.html")
