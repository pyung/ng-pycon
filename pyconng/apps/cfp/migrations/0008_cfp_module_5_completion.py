"""
Module 5 completion: co-speakers, speaker photo and onboarding, notes to
reviewers, a four-dimension review rubric, anonymised review and a confirmation
deadline.

Written by hand because ``makemigrations`` needs a one-off default for the new
non-null rubric columns. The review table is empty, but the defaults are declared
with ``preserve_default=False`` so the SQL is valid either way rather than
relying on that being true.
"""

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("wagtailimages", "0001_initial"),
        ("cfp", "0007_delete_emailtemplate"),
    ]

    operations = [
        # --- CFPSettings: anonymised review + confirmation deadline ---
        migrations.AddField(
            model_name="cfpsettings",
            name="anonymise_review",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "Hide speaker names, organisations, countries and bios from "
                    "reviewers, so proposals are judged on their content. Chairs "
                    "always see identities."
                ),
            ),
        ),
        migrations.AddField(
            model_name="cfpsettings",
            name="confirmation_deadline",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text=(
                    "Accepted speakers must confirm by this date. Run "
                    "'manage.py expire_unconfirmed_talks' afterwards to release the slots."
                ),
            ),
        ),

        # --- Speaker: photo + onboarding ---
        migrations.AddField(
            model_name="speaker",
            name="photo",
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+", to="wagtailimages.image",
                help_text="Headshot for the programme and the speakers page.",
            ),
        ),
        migrations.AddField(
            model_name="speaker",
            name="photo_alt",
            field=models.CharField(blank=True, max_length=200,
                help_text="Describe the photo for screen readers. Defaults to the speaker's name."),
        ),
        migrations.AddField(
            model_name="speaker",
            name="tshirt_size",
            field=models.CharField(blank=True, max_length=8, choices=[
                ("xs", "XS"), ("s", "S"), ("m", "M"), ("l", "L"),
                ("xl", "XL"), ("xxl", "2XL"), ("xxxl", "3XL"),
            ]),
        ),
        migrations.AddField(
            model_name="speaker",
            name="dietary_requirements",
            field=models.CharField(blank=True, max_length=255,
                help_text="Anything the caterers need to know."),
        ),
        migrations.AddField(
            model_name="speaker",
            name="travel_support_needed",
            field=models.BooleanField(default=False,
                help_text="Whether they need help getting to the conference."),
        ),
        migrations.AddField(
            model_name="speaker",
            name="accessibility_needs",
            field=models.TextField(blank=True,
                help_text="Anything we should arrange so they can present comfortably."),
        ),
        migrations.AddField(
            model_name="speaker",
            name="onboarding_completed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),

        # --- Proposal: reviewer notes, confirmation, lapsed status ---
        migrations.AddField(
            model_name="proposal",
            name="notes_to_reviewers",
            field=models.TextField(blank=True, help_text=(
                "Anything reviewers should know that does not belong in the public "
                "abstract. Never shown publicly."
            )),
        ),
        migrations.AddField(
            model_name="proposal",
            name="confirmed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="proposal",
            name="status",
            field=models.CharField(default="draft", max_length=20, choices=[
                ("draft", "Draft"), ("submitted", "Submitted"),
                ("under_review", "Under Review"), ("accepted", "Accepted"),
                ("rejected", "Rejected"), ("waitlisted", "Waitlisted"),
                ("withdrawn", "Withdrawn"), ("confirmed", "Confirmed"),
                ("lapsed", "Lapsed — not confirmed in time"),
            ]),
        ),

        # --- Review: one score becomes four dimensions ---
        migrations.RemoveField(model_name="review", name="score"),
        migrations.AddField(
            model_name="review",
            name="relevance",
            field=models.IntegerField(
                default=3,
                help_text="How much this audience wants this talk (1-5).",
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(5),
                ],
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="review",
            name="clarity",
            field=models.IntegerField(
                default=3,
                help_text="How clearly the proposal is written and scoped (1-5).",
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(5),
                ],
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="review",
            name="depth",
            field=models.IntegerField(
                default=3,
                help_text="Substance: is there something real here (1-5).",
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(5),
                ],
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="review",
            name="speaker_readiness",
            field=models.IntegerField(
                default=3,
                help_text="Confidence they can deliver it well (1-5).",
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(5),
                ],
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="review",
            name="weighted_score",
            field=models.DecimalField(
                decimal_places=2, default=0, max_digits=3,
                help_text="Auto-calculated mean of the four dimensions.",
            ),
        ),

        # --- Co-speakers ---
        migrations.CreateModel(
            name="ProposalCoSpeaker",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("email", models.EmailField(max_length=254,
                    help_text="The address the invitation went to.")),
                ("display_name", models.CharField(max_length=200,
                    help_text="How the name should read in the programme before they sign up.")),
                ("invited_at", models.DateTimeField(auto_now_add=True)),
                ("linked_at", models.DateTimeField(blank=True, null=True,
                    help_text="When the invitation found an account.")),
                ("proposal", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="co_speakers", to="cfp.proposal")),
                ("user", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="cfp_co_speaker_invites",
                    to=settings.AUTH_USER_MODEL,
                    help_text="Linked automatically once an account exists for this address.")),
            ],
            options={
                "verbose_name": "Co-speaker",
                "verbose_name_plural": "Co-speakers",
                "ordering": ["invited_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="proposalcospeaker",
            constraint=models.UniqueConstraint(
                fields=("proposal", "email"), name="unique_co_speaker_per_proposal"),
        ),
        migrations.AddIndex(
            model_name="proposalcospeaker",
            index=models.Index(fields=["email"], name="cfp_proposa_email_f99684_idx"),
        ),
    ]
