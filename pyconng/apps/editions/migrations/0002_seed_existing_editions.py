"""
Seed the editions that were hard-coded in pyconng/context_processors.py.

Carries across exactly what the CONFERENCE_YEARS dict held, so the site looks
and behaves identically the moment the constant is removed. 2026 is marked
current because that was the value of CURRENT_YEAR.
"""

from django.db import migrations

SEED = [
    {
        "year": 2024,
        "name": "Tech Innovation",
        "description": "Clean, geometric, tech-focused design",
        "theme": "2024",
        "primary_color": "#2563eb",
        "secondary_color": "#059669",
        "accent_color": "#f59e0b",
        "is_current": False,
    },
    {
        "year": 2025,
        "name": "Creative Community",
        "description": "Organic, playful, community-focused design",
        "theme": "2025",
        "primary_color": "#7c3aed",
        "secondary_color": "#ec4899",
        "accent_color": "#ea580c",
        "is_current": False,
    },
    {
        "year": 2026,
        "name": "Future Forward",
        "description": "Clean, modern, professional, forward-looking design",
        "theme": "2026",
        "primary_color": "#14b8a6",
        "secondary_color": "#3b82f6",
        "accent_color": "#f97316",
        "is_current": True,
    },
]


def seed(apps, schema_editor):
    Edition = apps.get_model("editions", "Edition")
    for row in SEED:
        Edition.objects.update_or_create(
            year=row["year"],
            defaults={**row, "is_published": True},
        )


def unseed(apps, schema_editor):
    Edition = apps.get_model("editions", "Edition")
    Edition.objects.filter(year__in=[r["year"] for r in SEED]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("editions", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
