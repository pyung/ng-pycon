"""
Emails for visa letter requests.

Four messages, and the approval one carries the letter as an attachment rather than
only a link. An applicant is often reading this on a phone in a queue at an embassy,
and "sign in to the conference website to download a PDF" is the wrong instruction
at that moment.
"""

import logging

from emails.services import send_email, site_url

from .models import VisaLetterRequest

logger = logging.getLogger(__name__)

SUBJECTS = {
    "received": "We have your visa letter request — PyCon Nigeria {year}",
    "approved": "Your PyCon Nigeria {year} invitation letter is ready",
    "rejected": "About your PyCon Nigeria {year} visa letter request",
}


def _context(request_obj):
    base = site_url()
    return {
        "applicant_name": (
            request_obj.passport_given_names.strip()
            or request_obj.user.get_full_name()
            or request_obj.user.email
        ),
        "conference_year": request_obj.conference_year,
        "reference": request_obj.reference or "",
        "basis": request_obj.get_basis_display(),
        "decision_note": request_obj.decision_note,
        "embassy": request_obj.embassy,
        "request_url": f"{base}/visa/my-request/" if base else "",
        "verify_url": f"{base}/visa/verify/" if base else "",
    }


def send_visa_email(request_obj, template):
    """
    Send one of the visa emails. Returns True if it was handed to the backend.

    The approved message attaches the letter. If generating it fails the email
    still goes out with the link, because "your letter is ready" arriving late is
    worse than it arriving without the attachment.
    """
    if template not in SUBJECTS:
        raise ValueError(f"Unknown visa email template: {template}")

    context = _context(request_obj)
    attachments = None
    if template == "approved":
        try:
            from . import letter

            attachments = [(
                letter.filename(request_obj),
                letter.build(request_obj),
                "application/pdf",
            )]
        except Exception:  # noqa: BLE001 - the notification matters more
            logger.exception(
                "Could not attach the invitation letter for request %s", request_obj.pk
            )

    return _send(
        template=f"visas/{template}",
        to=[request_obj.user.email],
        subject=SUBJECTS[template].format(year=request_obj.conference_year),
        context=context,
        year=request_obj.conference_year,
        attachments=attachments,
    )


def _send(*, template, to, subject, context, year, attachments=None):
    """
    Hand off to the shared sender.

    Attachments go through ``emails.services.send_email`` rather than a second
    message-building path here, so the admin's template overrides and both the text
    and HTML bodies keep working the same way for this email as for every other.
    """
    return send_email(
        template=template,
        to=to,
        subject=subject,
        context=context,
        conference_year=year,
        tags=["visa"],
        attachments=attachments,
    )
