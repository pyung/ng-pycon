"""
Composing the invitation letter itself.

The letter is generated on demand from the request record rather than stored as a
file. Two reasons: a stored PDF is a second copy of passport data to protect and
purge, and a regenerated letter can never disagree with the record it came from.

What the letter says is not cosmetic. An embassy reads it as a statement of fact by
the organisers, so two things are branched on the applicant's basis rather than
written once:

**Who pays.** A travel grant recipient's costs are partly ours and saying so helps
their application. A self-funding delegate's are not, and implying otherwise is a
misrepresentation to a foreign government made on our letterhead.

**What they are.** "An accepted speaker" is a stronger claim than "a registered
delegate", and it is only made where the CFP record supports it.

The letter also says what it is *not*, because a letter that oversells itself gets
the next one disbelieved: it is not a visa, not a guarantee of one, and not a
guarantee of anything beyond what it states.
"""

from django.utils import timezone

from . import pdf
from .models import VisaLetterRequest

#: Width of the logo on the letterhead, in points. About 45mm.
LOGO_WIDTH = 128
LOGO_MAX_HEIGHT = 64


def _date(value):
    """
    "5 October 2027". Written out rather than with strftime's %-d, which is a glibc
    extension -- this runs in a slim Debian image today and should not depend on
    that being true tomorrow.
    """
    if value is None:
        return ""
    return f"{value.day} {value.strftime('%B %Y')}"


def _logo_jpeg(settings_obj):
    """
    The letterhead logo as JPEG bytes, or None.

    Goes through a Wagtail rendition so that whatever was uploaded -- PNG, SVG-less
    raster, a 4000px original -- arrives as a JPEG the PDF can carry verbatim.
    Never raises: a letter without its logo is still a valid letter, and a broken
    image file should not stop somebody's visa application.
    """
    image = getattr(settings_obj, "logo", None)
    if image is None:
        return None
    try:
        rendition = image.get_rendition(f"width-{LOGO_WIDTH * 3}|format-jpeg")
        with rendition.file.open("rb") as handle:
            return handle.read()
    except Exception:  # noqa: BLE001 - a missing file must not block a letter
        return None


def _conference_dates(year):
    from editions.current import edition_for_year

    edition = edition_for_year(year)
    if edition is None:
        return "", ""
    return edition.dates_display or "", edition.venue or ""


def _funding_paragraph(request_obj, settings_obj):
    """
    Who is responsible for what. Stated plainly, and differently per basis.

    A consular officer is deciding whether this person can support themselves for
    the trip, so vagueness here is not politeness -- it is the part of the letter
    they are reading most closely.
    """
    organisation = settings_obj.organisation_name
    if request_obj.basis == VisaLetterRequest.BASIS_GRANTEE:
        return (
            f"{organisation} has awarded this delegate a travel grant towards the "
            "cost of attending, and will meet the portion of their travel and "
            "accommodation costs covered by that award. All remaining costs, "
            "including any not covered by the grant, remain the delegate's own "
            "responsibility. The conference registration fee has been waived."
        )
    if request_obj.basis == VisaLetterRequest.BASIS_SPEAKER:
        return (
            "This speaker's conference registration has been waived in recognition "
            f"of their contribution to the programme. {organisation} is not "
            "responsible for their travel, accommodation or living expenses, which "
            "the delegate meets themselves."
        )
    return (
        f"{organisation} is not responsible for this delegate's travel, "
        "accommodation, medical or living expenses. The delegate is attending at "
        "their own cost and has confirmed that they will meet these themselves."
    )


def build(request_obj, settings_obj=None, base_url=None):
    """
    The invitation letter for ``request_obj`` as PDF bytes.

    Raises ``ValueError`` if the request has not been approved: there is no such
    thing as a letter for an undecided request, and generating one "just to see"
    is how an unapproved letter escapes.
    """
    from emails.services import site_url

    from .models import VisaSettings

    if not request_obj.has_letter:
        raise ValueError(
            "A letter exists only for an approved request with a reference."
        )

    settings_obj = settings_obj or VisaSettings.for_year(request_obj.conference_year)
    year = request_obj.conference_year
    dates, venue = _conference_dates(year)
    base = (base_url or site_url() or "").rstrip("/")

    doc = pdf.Document(
        title=f"Invitation letter {request_obj.reference}",
        author=settings_obj.organisation_name,
    )

    # -- letterhead -------------------------------------------------------
    logo = _logo_jpeg(settings_obj)
    if logo:
        try:
            width, height = pdf._jpeg_size(logo)
            scale = min(LOGO_WIDTH / width, LOGO_MAX_HEIGHT / height)
            doc.image(logo, width * scale, height * scale)
        except ValueError:
            pass

    doc.text_line(settings_obj.organisation_name, font=pdf.HELVETICA_BOLD, size=15)
    for line in (settings_obj.organisation_address or "").splitlines():
        if line.strip():
            doc.text_line(line.strip(), size=9, leading=11)
    contact_bits = [
        bit for bit in (settings_obj.contact_email, settings_obj.contact_phone) if bit
    ]
    if contact_bits:
        doc.text_line(" · ".join(contact_bits), size=9, leading=11)
    if settings_obj.organisation_registration:
        doc.text_line(
            f"Registration: {settings_obj.organisation_registration}", size=9, leading=11
        )
    doc.rule(gap_above=8, gap_below=14)

    # -- date, reference, addressee ---------------------------------------
    issued = timezone.localtime(request_obj.letter_issued_at or timezone.now())
    doc.text_line(_date(issued), size=10)
    doc.text_line(f"Our reference: {request_obj.reference}", font=pdf.HELVETICA_BOLD, size=10)
    doc.spacer(10)

    embassy = request_obj.embassy
    doc.text_line("The Visa Officer", size=10.5)
    doc.text_line(
        f"Nigerian Embassy / High Commission, {embassy}" if embassy
        else "Nigerian Embassy / High Commission",
        size=10.5,
    )
    doc.spacer(12)

    doc.text_line("Dear Sir or Madam,", size=10.5)
    doc.spacer(6)
    doc.heading(
        f"Letter of invitation: {settings_obj.organisation_name} {year}",
        size=11.5, space_before=0, space_after=8,
    )

    # -- the substance ----------------------------------------------------
    basis_phrase = VisaLetterRequest.BASIS_DESCRIPTIONS.get(
        request_obj.basis, "a registered delegate"
    )
    where = f" at {venue}" if venue else ""
    when = f", {dates}" if dates else ""
    doc.paragraph(
        f"I write to confirm that the person named below has been registered as "
        f"{basis_phrase} at {settings_obj.organisation_name} {year}, a technical "
        f"conference for the Python programming language community, to be held"
        f"{where}{when}. We invite them to attend and would be grateful for any "
        f"assistance you are able to give with their entry visa."
    )

    rows = [
        ("Name (as in passport)", request_obj.passport_name_formal),
        ("Nationality", request_obj.nationality),
    ]
    if request_obj.details_retained:
        rows.append(("Passport number", request_obj.passport_number))
        if request_obj.passport_expiry:
            rows.append(("Passport expiry", _date(request_obj.passport_expiry)))
        if request_obj.date_of_birth:
            rows.append(("Date of birth", _date(request_obj.date_of_birth)))
    rows.append(("Attending as", request_obj.get_basis_display() or "Delegate"))
    if request_obj.arrival_on:
        rows.append(("Expected arrival", _date(request_obj.arrival_on)))
    if request_obj.departure_on:
        rows.append(("Expected departure", _date(request_obj.departure_on)))
    if request_obj.accommodation:
        rows.append(("Accommodation", request_obj.accommodation))
    doc.fields(rows, space_after=12)

    doc.paragraph(_funding_paragraph(request_obj, settings_obj))
    doc.paragraph(
        "This letter is issued solely in support of a visa application for the "
        "purpose stated above. It is not a visa, it does not guarantee that one "
        "will be granted, and it makes no undertaking beyond what is written here. "
        "The delegate has undertaken to leave Nigeria on or before the expiry of "
        "any visa issued to them."
    )

    verify = f"{base}/visa/verify/" if base else "the conference website"
    if settings_obj.verification_enabled:
        doc.paragraph(
            f"The authenticity of this letter can be confirmed at {verify} using "
            f"reference {request_obj.reference}"
            + (
                f", or by writing to {settings_obj.contact_email}."
                if settings_obj.contact_email
                else "."
            )
        )
    elif settings_obj.contact_email:
        doc.paragraph(
            f"To confirm this letter, please write to {settings_obj.contact_email} "
            f"quoting reference {request_obj.reference}."
        )

    # -- signature --------------------------------------------------------
    doc.spacer(24)
    doc.paragraph("Yours faithfully,", space_after=0)
    doc.spacer(34)
    doc.rule(width=0.5, gap_above=0, gap_below=4, indent=0)
    if settings_obj.signatory_name:
        doc.text_line(settings_obj.signatory_name, font=pdf.HELVETICA_BOLD, size=10.5)
    if settings_obj.signatory_title:
        doc.text_line(settings_obj.signatory_title, size=10)
    doc.text_line(f"{settings_obj.organisation_name} {year}", size=10)

    return doc.render()


def filename(request_obj):
    """A filename an applicant can send to an embassy without renaming it."""
    surname = (request_obj.passport_surname or "letter").strip().replace(" ", "-")
    return f"pyconng-{request_obj.conference_year}-invitation-{surname}-{request_obj.reference}.pdf"
