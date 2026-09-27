"""
Invoices.

A company cannot claim an expense without a document carrying its own name, an
address and a number, and corporate buyers place the largest orders. So this is not
a nicety: without it the biggest orders either do not happen or happen by email.

An invoice records the figures as they were at issue rather than joining to the
order's current ones. An invoice is a statement about a moment; if a price changes
or a refund lands, the invoice must still say what it said.
"""

import logging
from decimal import Decimal

from django.db import IntegrityError, transaction

from audit.services import record

from .models import Invoice, Ticket, TicketSettings

logger = logging.getLogger(__name__)


def next_number(year, settings_obj=None):
    """
    The next invoice number for an edition, as ``PREFIX-YEAR-0001``.

    Derived from the highest number already issued rather than from a count, so
    deleting a row cannot cause a number to be reused. Collisions are still
    possible under concurrency and are handled by retrying in ``issue_invoice``;
    the unique constraint on the column is what actually guarantees uniqueness.
    """
    settings_obj = settings_obj or TicketSettings.for_year(year)
    prefix = (settings_obj.invoice_prefix or "PYNG").strip().upper()
    stem = f"{prefix}-{year}-"

    highest = 0
    for number in Invoice.objects.filter(
        conference_year=year, number__startswith=stem
    ).values_list("number", flat=True):
        tail = number[len(stem) :]
        if tail.isdigit():
            highest = max(highest, int(tail))
    return f"{stem}{highest + 1:04d}"


def invoice_figures(ticket):
    """
    ``(subtotal, discount, total)`` for an order, from what was recorded on it.

    The total is what was actually charged. The subtotal is reconstructed as total
    plus discount, so the three always agree on the page even where an older order
    predates the discount field.
    """
    total = Decimal(ticket.total_amount or ticket.amount or 0)
    discount = Decimal(ticket.discount_amount or 0)
    return total + discount, discount, total


@transaction.atomic
def issue_invoice(
    ticket,
    *,
    company_name="",
    company_address="",
    buyer_tax_id="",
    purchase_order_reference="",
    buyer_name="",
    notes="",
    issued_by=None,
):
    """
    Create the invoice for ``ticket``, or return the one it already has.

    One invoice per order, enforced by the one-to-one field: re-issuing would give
    a buyer two numbered documents for one payment, which is exactly the thing
    their finance team will query.
    """
    existing = getattr(ticket, "invoice", None)
    if existing is not None:
        return existing, False

    settings_obj = TicketSettings.for_year(ticket.conference_year)
    subtotal, discount, total = invoice_figures(ticket)

    for attempt in range(5):
        try:
            with transaction.atomic():
                invoice = Invoice.objects.create(
                    ticket=ticket,
                    number=next_number(ticket.conference_year, settings_obj),
                    conference_year=ticket.conference_year,
                    buyer_name=buyer_name or ticket.user.get_full_name() or ticket.user.email,
                    buyer_email=ticket.user.email or "",
                    company_name=company_name,
                    company_address=company_address,
                    buyer_tax_id=buyer_tax_id,
                    purchase_order_reference=purchase_order_reference,
                    subtotal=subtotal,
                    discount=discount,
                    total=total,
                    notes=notes or settings_obj.invoice_notes,
                )
            break
        except IntegrityError:
            # Two invoices issued in the same instant. The number is unique in the
            # database, so losing the race means picking the next one and retrying.
            if attempt == 4:
                logger.error(
                    "Could not allocate an invoice number for %s after 5 tries.",
                    ticket.order,
                )
                raise
            continue

    record(
        target=ticket,
        action="Invoice issued",
        actor=issued_by,
        new_value=invoice.number,
        note=f"{invoice.addressee}, {invoice.total}",
        conference_year=ticket.conference_year,
    )
    return invoice, True


def invoice_for(ticket, **kwargs):
    """The order's invoice, issuing one if it has none. Paid orders only."""
    if ticket.status != Ticket.PAID:
        return None
    invoice, _ = issue_invoice(ticket, **kwargs)
    return invoice
