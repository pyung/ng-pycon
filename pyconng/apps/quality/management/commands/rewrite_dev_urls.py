"""
Rewrite development URLs that reached published content.

    python manage.py rewrite_dev_urls              # report only
    python manage.py rewrite_dev_urls --apply      # fix and publish

The navigation menu, footer links and all six social fields on the homepage were
entered as absolute ``http://127.0.0.1:8000/...`` links on somebody's laptop.
Every one of those sends a live visitor to their own machine. Nothing in the
codebase could catch it -- the values are content -- but the frontend audit over
a rendered page does, which is how they were found.

A link to a path on this site becomes the path itself. A social profile cannot be
a path on this site, so those fields are cleared instead: the footer hides an
empty one, which is honest, where a link to the homepage labelled "Follow us on
Twitter" is not.
"""

import json
import re

from django.core.management.base import BaseCommand
from django.db import transaction

#: Hosts that only exist on a developer's machine.
DEV_HOST_RE = re.compile(
    r"^https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|testserver)(?::\d+)?",
    re.IGNORECASE,
)

#: Fields holding a link to somewhere else entirely. A dev URL here is a
#: placeholder, not a mistyped path, so it is cleared rather than rewritten.
EXTERNAL_URL_FIELDS = (
    "twitter_url",
    "facebook_url",
    "linkedin_url",
    "instagram_url",
    "youtube_url",
    "github_url",
)

#: StreamFields to walk, and the keys inside each block that hold a URL.
STREAM_URL_KEYS = ("link_url", "url")


def to_local_path(value):
    """``http://127.0.0.1:8000/cfp/`` -> ``/cfp/``. None when it is not a dev URL."""
    if not isinstance(value, str):
        return None
    match = DEV_HOST_RE.match(value.strip())
    if not match:
        return None
    path = value.strip()[match.end():]
    return path or "/"


def _rewrite_block_values(value, changes, trail=""):
    """Rewrite URL keys in a StreamField block value, in place. Returns nothing."""
    if isinstance(value, dict):
        for key, inner in list(value.items()):
            where = f"{trail}.{key}" if trail else key
            if key in STREAM_URL_KEYS:
                replacement = to_local_path(inner)
                if replacement is not None:
                    value[key] = replacement
                    changes.append((where, inner, replacement))
            else:
                _rewrite_block_values(inner, changes, where)
    elif isinstance(value, list):
        for index, inner in enumerate(value):
            _rewrite_block_values(inner, changes, f"{trail}[{index}]")


class Command(BaseCommand):
    help = "Find and rewrite development URLs in published page content."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write the changes and publish a new revision. Without this, report only.",
        )

    def handle(self, *args, **options):
        from home.models import HomePage

        apply = options["apply"]
        total = 0
        pages_changed = 0

        for page in HomePage.objects.all():
            changes = []

            for field in ("navigation_menu_items", "footer_links"):
                stream = getattr(page, field, None)
                if stream is None:
                    continue
                data = json.loads(json.dumps(list(stream.raw_data)))
                field_changes = []
                _rewrite_block_values(data, field_changes, field)
                if field_changes:
                    setattr(page, field, json.dumps(data))
                    changes.extend(field_changes)

            for field in EXTERNAL_URL_FIELDS:
                value = getattr(page, field, "")
                if to_local_path(value) is not None:
                    setattr(page, field, "")
                    changes.append((field, value, "(cleared)"))

            if not changes:
                continue

            pages_changed += 1
            total += len(changes)
            self.stdout.write(f"\n{page.pk} {page.title}  ({len(changes)} change(s))")
            for where, before, after in changes:
                self.stdout.write(f"    {where}\n        {before}  ->  {after or '(empty)'}")

            if apply:
                with transaction.atomic():
                    page.save()
                    revision = page.save_revision(log_action=True)
                    if page.live:
                        revision.publish()
                self._record(page, changes)

        self.stdout.write("")
        if not total:
            self.stdout.write(self.style.SUCCESS("No development URLs in page content."))
            return

        verb = "rewritten" if apply else "found"
        self.stdout.write(
            f"{total} development URL(s) {verb} across {pages_changed} page(s)."
        )
        if not apply:
            self.stdout.write("Re-run with --apply to write and publish them.")

    def _record(self, page, changes):
        """Leave a trail, so the edit is not an unexplained content change."""
        try:
            from audit.services import record

            record(
                actor=None,
                actor_label="manage.py rewrite_dev_urls",
                action="Rewrote development URLs in page content",
                note="; ".join(f"{where}: {before} -> {after}" for where, before, after in changes),
                target=page,
                target_label=f"{page.pk} {page.title}",
                conference_year=getattr(page, "conference_year", None),
            )
        except Exception as exc:  # noqa: BLE001
            self.stderr.write(f"    (could not write an audit entry: {exc})")
