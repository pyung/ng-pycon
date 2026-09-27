"""
Give every existing place a check-in code, then require one.

Three steps rather than one, because the field is unique: adding a unique column
with a single default would collide on the second row. The previous migration
added it blank; this fills it and then enforces uniqueness.
"""

from django.db import migrations, models


def fill_checkin_codes(apps, schema_editor):
    """
    Backfill a code for every place that has none.

    Generates here rather than calling tickets.models.generate_checkin_code: a
    migration must keep working when that function is changed or removed, so the
    alphabet is repeated deliberately.
    """
    import secrets

    alphabet = "ABCDEFGHJKMNPRSTUVWXY23479"
    TicketSale = apps.get_model("tickets", "TicketSale")

    taken = set(
        TicketSale.objects.exclude(checkin_code="").values_list("checkin_code", flat=True)
    )
    for sale in TicketSale.objects.filter(checkin_code="").iterator():
        while True:
            code = "".join(secrets.choice(alphabet) for _ in range(10))
            if code not in taken:
                taken.add(code)
                break
        sale.checkin_code = code
        sale.save(update_fields=["checkin_code"])


def clear_checkin_codes(apps, schema_editor):
    """Reversing drops the codes, since the column is about to allow blanks again."""
    TicketSale = apps.get_model("tickets", "TicketSale")
    TicketSale.objects.update(checkin_code="")


class Migration(migrations.Migration):
    dependencies = [
        ("tickets", "0003_invoice_refund_ticketsettings_ticketwaitlistentry_and_more"),
    ]

    operations = [
        migrations.RunPython(fill_checkin_codes, clear_checkin_codes),
        migrations.AlterField(
            model_name="ticketsale",
            name="checkin_code",
            field=models.CharField(
                db_index=True,
                editable=False,
                help_text="What the QR code carries, and what a volunteer can type instead.",
                max_length=16,
                unique=True,
            ),
        ),
    ]
