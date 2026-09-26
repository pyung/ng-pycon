from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings, RequestFactory


def _make_user(email="ada@example.com"):
    """A speaker profile needs an owning account; its email is the account's."""
    return get_user_model().objects.create_user(
        username="ada", email=email, password="x",
    )


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class CfpSubmissionConfirmationEmailTests(TestCase):
    def setUp(self):
        mail.outbox = []
        from cfp.models import Speaker, Proposal, Track
        self.user = _make_user()
        self.speaker = Speaker.objects.create(
            user=self.user, full_name="Ada Lovelace",
            bio="Bio", country="NG", conference_year=2026,
        )
        self.track = Track.objects.create(name="Web", conference_year=2026)
        self.proposal = Proposal.objects.create(
            speaker=self.speaker,
            title="My Talk",
            abstract="abstract", description="desc",
            track=self.track,
            format="talk",
            duration=30,
            audience_level="beginner",
            conference_year=2026,
        )

    def test_send_submission_confirmation_uses_helper(self):
        from cfp.services import CFPService
        request = RequestFactory().get("/cfp/")
        CFPService.send_submission_confirmation(self.proposal, request=request)

        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ["ada@example.com"])
        self.assertIn("Proposal Received", msg.subject)
        self.assertIn("My Talk", msg.subject)
        self.assertEqual(len(msg.alternatives), 1)
        html, _ = msg.alternatives[0]
        self.assertIn("My Talk", html)
        self.assertIn("Web", html)
        self.assertEqual(msg.tags, ["cfp", "submission"])


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class CfpDecisionEmailTests(TestCase):
    def setUp(self):
        mail.outbox = []
        from cfp.models import Speaker, Proposal, Track
        from emails.models import EmailTemplate
        self.user = _make_user()
        self.speaker = Speaker.objects.create(
            user=self.user, full_name="Ada Lovelace",
            bio="Bio", country="NG", conference_year=2026,
        )
        self.track = Track.objects.create(name="Web", conference_year=2026)
        self.proposal = Proposal.objects.create(
            speaker=self.speaker, title="My Talk",
            abstract="a", description="d",
            track=self.track, format="talk",
            duration=30, audience_level="beginner",
            conference_year=2026,
        )
        EmailTemplate.objects.create(
            key="cfp/accepted",
            conference_year=2026,
            subject="Accepted: {proposal_title}",
            body="Hi {speaker_name}, your proposal {proposal_title} was accepted.",
        )

    def test_send_decision_emails_uses_helper_and_preserves_template_render(self):
        from cfp.services import CFPService
        sent = CFPService.send_decision_emails(
            [self.proposal.id], template_type="acceptance", conference_year=2026,
        )
        self.assertEqual(sent, 1)
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.subject, "Accepted: My Talk")
        self.assertIn("Ada Lovelace", msg.body)
        self.assertIn("accepted", msg.body.lower())
        self.assertEqual(len(msg.alternatives), 1)
        html, _ = msg.alternatives[0]
        self.assertIn("Ada Lovelace", html)
        self.assertEqual(msg.tags, ["cfp", "decision", "acceptance"])
