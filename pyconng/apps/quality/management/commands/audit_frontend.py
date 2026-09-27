"""
Run the accessibility and performance audit over the site and report.

    python manage.py audit_frontend              # report, exit 0
    python manage.py audit_frontend --strict     # exit 1 if any error
    python manage.py audit_frontend --warnings   # show warnings too
    python manage.py audit_frontend --url /cfp/  # one page
    python manage.py audit_frontend --palette    # theme colour contrast

Fetches each page through the Django test client rather than over the network, so
it needs no running server and no browser -- which is what makes it usable both
from a laptop and from CI.
"""

from django.core.management.base import BaseCommand
from django.test import Client
from django.test.utils import setup_test_environment, teardown_test_environment

from quality.audit import audit_html, collapse, errors
from quality.urls_to_audit import all_urls


class Command(BaseCommand):
    help = "Audit rendered pages for accessibility and performance problems."

    def add_arguments(self, parser):
        parser.add_argument(
            "--url",
            action="append",
            dest="urls",
            metavar="PATH",
            help="Audit this path instead of the whole site. Repeatable.",
        )
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Exit non-zero when any error is found. Use this in CI.",
        )
        parser.add_argument(
            "--warnings",
            action="store_true",
            help="Report warnings as well as errors.",
        )
        parser.add_argument(
            "--palette",
            action="store_true",
            help="Also report colour contrast across the theme palettes.",
        )
        parser.add_argument(
            "--rule",
            action="append",
            dest="rules",
            metavar="NAME",
            help="Only report this rule. Repeatable.",
        )

    def handle(self, *args, **options):
        urls = options["urls"] or all_urls()
        if not urls:
            self.stdout.write(self.style.WARNING("No URLs to audit."))
            return

        # setup_test_environment allows the test client's Host header. Torn down
        # again so a management command does not leave the process in test mode.
        started_test_env = False
        try:
            setup_test_environment()
            started_test_env = True
        except RuntimeError:
            pass  # Already inside a test run.

        try:
            self._audit(urls, options)
            if options["palette"]:
                self._palette()
        finally:
            if started_test_env:
                teardown_test_environment()

    def _palette(self):
        """
        Contrast over the declared theme colours, and tokens declared twice.

        Separate from the page audit because contrast cannot be read off markup:
        this checks the palette a theme declares, against the combinations its
        components are written to produce. See quality/contrast.py.
        """
        from pathlib import Path

        from django.conf import settings

        from quality.contrast import audit_palette, conflicting_tokens

        source = Path(settings.PROJECT_DIR) / "static" / "css" / "src"
        self.stdout.write("\nTheme palettes")
        for css in sorted(source.glob("input_*.css")):
            theme = css.stem.replace("input_", "")
            text = css.read_text()
            self.stdout.write(f"\n  {theme}")
            results = audit_palette(text, theme)
            if not results:
                self.stdout.write("    no checkable colour pairs declared")
            for pair, ratio, passes in results:
                mark = self.style.SUCCESS("pass") if passes else self.style.ERROR("FAIL")
                self.stdout.write(
                    f"    {mark}  {ratio:5.2f}:1 (needs {pair.minimum})  {pair.component}"
                )
            conflicts = conflicting_tokens(text, theme)
            for token, values in sorted(conflicts.items()):
                pairs = ", ".join(f"{scope} {value}" for scope, value in values.items())
                self.stdout.write(
                    self.style.WARNING(
                        f"    declared twice: {token} -- {pairs}"
                        " (the class wins inside <body>)"
                    )
                )

    def _audit(self, urls, options):
        client = Client()
        wanted = set(options["rules"] or ())
        show_warnings = options["warnings"]

        total_errors = 0
        total_warnings = 0
        audited = 0
        skipped = []

        for url in urls:
            response = client.get(url, follow=True)
            if response.status_code != 200:
                skipped.append((url, response.status_code))
                continue
            if "text/html" not in response.get("Content-Type", ""):
                continue

            audited += 1
            findings = audit_html(
                response.content.decode(response.charset or "utf-8", "replace"),
                internal_hosts=("testserver",),
            )
            if wanted:
                findings = [f for f in findings if f.rule in wanted]

            page_errors = errors(findings)
            page_warnings = [f for f in findings if not f.is_error]
            total_errors += len(page_errors)
            total_warnings += len(page_warnings)

            shown = collapse(page_errors + (page_warnings if show_warnings else []))
            if not shown:
                self.stdout.write(f"{self.style.SUCCESS('  ok'):>4} {url}")
                continue

            label = f"{len(page_errors)} error" + ("s" if len(page_errors) != 1 else "")
            if show_warnings:
                label += f", {len(page_warnings)} warning" + (
                    "s" if len(page_warnings) != 1 else ""
                )
            self.stdout.write(f"\n{url}  ({label})")
            for finding in shown:
                style = self.style.ERROR if finding.is_error else self.style.WARNING
                self.stdout.write(f"    {style(str(finding))}")

        self.stdout.write("")
        self.stdout.write(
            f"{audited} page(s) audited: {total_errors} error(s), {total_warnings} warning(s)."
        )
        if not show_warnings and total_warnings:
            self.stdout.write("Run with --warnings to see the warnings.")
        for url, status in skipped:
            self.stdout.write(self.style.NOTICE(f"skipped {url} (HTTP {status})"))

        if options["strict"] and total_errors:
            raise SystemExit(1)
