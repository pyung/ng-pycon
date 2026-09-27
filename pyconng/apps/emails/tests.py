from django.conf import settings
from django.core import mail
from django.test import TestCase, override_settings


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SendEmailHelperTests(TestCase):
    def setUp(self):
        mail.outbox = []

    def test_sends_html_and_text_with_subject_and_recipients(self):
        from emails.services import send_email

        send_email(
            template="test_basic",
            to=["a@example.com"],
            subject="Hello",
            context={"name": "Ada"},
        )

        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.subject, "Hello")
        self.assertEqual(msg.to, ["a@example.com"])
        self.assertIn("Ada", msg.body)
        self.assertEqual(len(msg.alternatives), 1)
        html_content, mimetype = msg.alternatives[0]
        self.assertEqual(mimetype, "text/html")
        self.assertIn("Ada", html_content)

    def test_uses_default_from_email_when_not_provided(self):
        from emails.services import send_email

        send_email(
            template="test_basic",
            to=["a@example.com"],
            subject="Hi",
            context={"name": "Ada"},
        )
        msg = mail.outbox[0]
        # Asserts the contract -- the helper falls back to DEFAULT_FROM_EMAIL --
        # rather than a literal address. It used to assert "hello@pynigeria.com"
        # and failed, because the setting reads hello@hello.pynigeria.com. Which
        # of the two is the typo is still an open question: a doubled label looks
        # like a slip, but a sending subdomain is also how Resend is often set up.
        # Pinning the literal here only ever produced a permanently red test.
        self.assertEqual(msg.from_email, settings.DEFAULT_FROM_EMAIL)

    def test_sets_tags_on_message(self):
        from emails.services import send_email

        send_email(
            template="test_basic",
            to=["a@example.com"],
            subject="Hi",
            context={"name": "Ada"},
            tags=["cfp", "submission"],
        )
        msg = mail.outbox[0]
        self.assertEqual(msg.tags, ["cfp", "submission"])

    def test_fail_silently_swallows_template_errors(self):
        from emails.services import send_email

        send_email(
            template="does_not_exist",
            to=["a@example.com"],
            subject="Hi",
            context={},
            fail_silently=True,
        )
        self.assertEqual(len(mail.outbox), 0)

    def test_raises_when_fail_silently_false(self):
        from django.template import TemplateDoesNotExist
        from emails.services import send_email

        with self.assertRaises(TemplateDoesNotExist):
            send_email(
                template="does_not_exist",
                to=["a@example.com"],
                subject="Hi",
                context={},
                fail_silently=False,
            )


from django.contrib.auth import get_user_model
from django.urls import reverse


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class PasswordResetEmailTests(TestCase):
    def setUp(self):
        mail.outbox = []
        User = get_user_model()
        self.user = User.objects.create_user(
            username="ada", email="ada@example.com", password="secret123"
        )

    def test_password_reset_sends_email(self):
        response = self.client.post(
            reverse("password_reset"), {"email": "ada@example.com"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertIn("PyCon Nigeria", msg.subject)
        self.assertIn("reset", msg.subject.lower())
        self.assertEqual(msg.to, ["ada@example.com"])
        self.assertIn("password", msg.body.lower())
        self.assertEqual(len(msg.alternatives), 1)
        html, mimetype = msg.alternatives[0]
        self.assertEqual(mimetype, "text/html")
        self.assertIn("Choose a new password", html)
