"""
Link Speaker to a Django account.

Drops the parallel identity the CFP had grown: the denormalised ``email`` column
and the UUID ``access_token`` that let someone manage proposals without ever
holding an account. Both are replaced by a required foreign key to the user.

Safe to add a NOT NULL column without a default here because the speaker table
is empty. It would not be safe once proposals exist -- a later run of this shape
needs a data migration to match speakers to accounts by email first.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("cfp", "0003_alter_reviewerassignment_reviewer_and_more"),
    ]

    operations = [
        # Release the old (email, conference_year) constraint before the column goes.
        migrations.AlterUniqueTogether(
            name="speaker",
            unique_together=set(),
        ),
        migrations.RemoveField(
            model_name="speaker",
            name="email",
        ),
        migrations.RemoveField(
            model_name="speaker",
            name="access_token",
        ),
        migrations.AddField(
            model_name="speaker",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="speaker_profiles",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="speaker",
            name="full_name",
            field=models.CharField(
                help_text="Name as it should appear in the programme",
                max_length=200,
            ),
        ),
        migrations.AlterUniqueTogether(
            name="speaker",
            unique_together={("user", "conference_year")},
        ),
    ]
