"""Travel grant transactional emails.

Wording lives in ``emails/grants/*.{html,txt}`` and can be overridden per edition
in the admin without a deploy. Links use the configured site URL rather than a
literal domain.
"""
from __future__ import annotations

import logging

from emails.services import send_email, site_url

logger = logging.getLogger(__name__)


def _applicant_context(application, **extra):
    user = application.user
    return {
        "applicant_name": user.get_full_name() or user.email,
        "conference_year": application.conference_year,
        "dashboard_url": f"{site_url()}/grants/my-application/",
        **extra,
    }


def send_grant_submission_confirmation(application) -> None:
    user = application.user
    if not user or not user.email:
        return
    send_email(
        template="grants/submission_confirmation",
        to=[user.email],
        subject="PyCon Nigeria Travel Grant — Application Received",
        context=_applicant_context(application),
        tags=["grants", "submission"],
        conference_year=application.conference_year,
        fail_silently=True,
    )


#: Application status -> (template key, subject, decision tag).
DECISION_EMAILS = {
    "approved": (
        "grants/approved",
        "PyCon Nigeria Travel Grant — Approved, please confirm",
        "approved",
    ),
    "not_selected": ("grants/rejected", "PyCon Nigeria Travel Grant — Decision", "rejected"),
    "waitlisted": (
        "grants/waitlisted",
        "PyCon Nigeria Travel Grant — Waitlisted",
        "waitlisted",
    ),
}


def send_grant_decision(application) -> None:
    """
    Email the decision for this application.

    Waitlisted applicants used to be told nothing at all -- the old version
    returned early for any status other than approved or rejected, so someone
    could sit on a waitlist without ever hearing it.
    """
    user = application.user
    if not user or not user.email:
        return

    chosen = DECISION_EMAILS.get(application.status)
    if chosen is None:
        logger.debug(
            "No decision email defined for grant status %r", application.status,
        )
        return
    template, subject, tag = chosen

    extra = {}
    if application.status == "approved":
        # The approval email is now an offer, so it has to carry the deadline. An
        # offer without one reads as a settled award, and the recipient does nothing.
        extra.update(
            approved_amount=application.approved_amount,
            deadline=application.acceptance_deadline,
            days_left=application.days_left_to_accept,
            decision_note=application.decision_note,
        )

    send_email(
        template=template,
        to=[user.email],
        subject=subject,
        context=_applicant_context(application, **extra),
        tags=["grants", "decision", tag],
        conference_year=application.conference_year,
        fail_silently=True,
    )


def send_grant_payment_sent(application, payment) -> None:
    """Tell a recipient their grant has actually been paid."""
    user = application.user
    if not user or not user.email:
        return
    send_email(
        template="grants/payment_sent",
        to=[user.email],
        subject="PyCon Nigeria Travel Grant — Payment Sent",
        context=_applicant_context(
            application,
            amount_paid=payment.amount_paid,
            reference=payment.reference,
        ),
        tags=["grants", "payment"],
        conference_year=application.conference_year,
        fail_silently=True,
    )


def send_offer_reminder(application) -> None:
    """
    Nudge somebody whose offer is about to expire.

    Sent by ``manage.py remind_grant_offers``. Worth sending because the usual reason
    an offer lapses is not indifference but an email read on a phone and forgotten.
    """
    user = application.user
    if not user or not user.email:
        return
    send_email(
        template="grants/offer_reminder",
        to=[user.email],
        subject="PyCon Nigeria Travel Grant — Your offer expires soon",
        context=_applicant_context(
            application,
            amount=application.approved_amount,
            deadline=application.acceptance_deadline,
            days_left=application.days_left_to_accept,
        ),
        tags=["grants", "offer_reminder"],
        conference_year=application.conference_year,
        fail_silently=True,
    )
