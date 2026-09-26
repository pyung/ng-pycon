"""Transactional email helper.

Renders an HTML + text pair and sends via the configured EMAIL_BACKEND (Resend
in production, console in dev).

Every email has a version-controlled default at ``emails/<template>.{html,txt}``.
An :class:`~emails.models.EmailTemplate` row for the same key overrides the
wording, so organizers reword messages in the admin without a deploy. The file
is the fallback, never the other way round -- a missing override must never mean
nothing is sent.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def site_url():
    """
    Absolute base URL for links in email, without a trailing slash.

    Prefers an explicit setting, then the Wagtail site's own root URL, so links
    follow the deployment instead of a literal baked into the sending code.
    """
    explicit = getattr(settings, "PYCON_SITE_URL", "")
    if explicit:
        return explicit.rstrip("/")

    try:
        from wagtail.models import Site

        site = Site.objects.filter(is_default_site=True).first()
        if site:
            return site.root_url.rstrip("/")
    except Exception:  # noqa: BLE001 - database not ready, or no site configured
        pass

    return getattr(settings, "WAGTAILADMIN_BASE_URL", "").rstrip("/")


def send_email(
    *,
    template: str,
    to: list[str],
    subject: str,
    context: dict | None = None,
    tags: list[str] | None = None,
    reply_to: list[str] | None = None,
    fail_silently: bool = True,
    from_email: str | None = None,
    conference_year: int | None = None,
) -> bool:
    """Render and send one email.

    ``template`` is a key such as ``"grants/waitlisted"``. An active
    EmailTemplate row for that key wins over the files on disk; pass
    ``conference_year`` to prefer an override written for that edition.

    Returns True if a message was handed to the backend, False otherwise.
    Raises only when fail_silently is False.
    """
    ctx = {
        "site_name": "PyCon Nigeria",
        "support_email": "hello@pynigeria.com",
        "site_url": site_url(),
    }
    if context:
        ctx.update(context)

    override = _lookup_override(template, conference_year)

    try:
        if override is not None:
            subject, body = override.render(ctx)
            ctx = {**ctx, "rendered_subject": subject, "rendered_body": body}
            text_body = render_to_string("emails/_override.txt", ctx)
            html_body = render_to_string("emails/_override.html", ctx)
        else:
            text_body = render_to_string(f"emails/{template}.txt", ctx)
            html_body = render_to_string(f"emails/{template}.html", ctx)
    except Exception:
        logger.exception("Failed to render email template %r to %r", template, to)
        if not fail_silently:
            raise
        return False

    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=from_email or settings.DEFAULT_FROM_EMAIL,
        to=to,
        reply_to=reply_to,
    )
    msg.attach_alternative(html_body, "text/html")
    if tags:
        msg.tags = list(tags)

    try:
        msg.send(fail_silently=False)
        return True
    except Exception:
        logger.exception("Failed to send email template=%r to=%r", template, to)
        if not fail_silently:
            raise
        return False


def _lookup_override(template, conference_year):
    """An admin override for this template, or None. Never raises."""
    try:
        from .models import EmailTemplate

        return EmailTemplate.lookup(template, conference_year)
    except Exception:  # noqa: BLE001 - table missing during migrate, etc.
        logger.debug("Email template override lookup skipped for %r", template, exc_info=True)
        return None
