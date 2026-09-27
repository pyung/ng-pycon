"""
Tests for the Code of Conduct reporting path.

Weighted towards what must *not* happen. A reporting form that works is table
stakes; a reporting form that quietly keeps a name it promised not to keep, or
puts an incident description into an inbox, or shows reports to the organizer the
report is about, is worse than no form at all. Most of what follows checks those.
"""

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from wagtail.models import Page, Site

from accounts.models import RoleAssignment
from accounts.roles import Role, roles_for
from audit.models import AuditEntry
from editions.current import invalidate
from editions.models import Edition

from conduct.forms import IncidentReportForm
from conduct.models import IncidentReport, REFERENCE_ALPHABET, make_reference
from conduct.services import set_status, submit_report

User = get_user_model()

GOOD_DESCRIPTION = (
    "During the afternoon break a speaker made repeated comments about another "
    "attendee's appearance after being asked to stop."
)


def form_payload(**overrides):
    data = {
        "reporter_name": "Ada Lovelace",
        "reporter_email": "ada@example.com",
        "incident_when": "Saturday afternoon",
        "incident_where": "Hall B",
        "people_involved": "A speaker and an attendee",
        "description": GOOD_DESCRIPTION,
        "witnesses": "",
        "desired_outcome": "A word with the speaker.",
        "website": "",
    }
    data.update(overrides)
    return data


class ReferenceTests(TestCase):
    def test_shape(self):
        reference = make_reference(2026)
        self.assertTrue(reference.startswith("CoC-2026-"))
        self.assertEqual(len(reference), len("CoC-2026-") + 5)

    def test_alphabet_avoids_characters_people_confuse(self):
        """
        A reference is read aloud and typed from a note, so only one character of
        each confusable pair is in the alphabet.
        """
        for character in "0O1IL5Z6Q8":
            self.assertNotIn(character, REFERENCE_ALPHABET)
        # And the survivor of each pair is still available.
        for character in "SB2G9":
            self.assertIn(character, REFERENCE_ALPHABET)

    def test_references_are_unique_across_many_reports(self):
        Edition.objects.update_or_create(
            year=2026, defaults={"name": "2026", "theme": "2026", "is_current": True, "is_published": True}
        )
        invalidate()
        references = set()
        for _ in range(25):
            report = IncidentReport.objects.create(conference_year=2026, description=GOOD_DESCRIPTION)
            references.add(report.reference)
        self.assertEqual(len(references), 25)


class FormTests(TestCase):
    def test_a_named_report_needs_an_email(self):
        form = IncidentReportForm(data=form_payload(reporter_email=""))
        self.assertFalse(form.is_valid())
        self.assertIn("reporter_email", form.errors)

    def test_an_anonymous_report_needs_no_email(self):
        form = IncidentReportForm(data=form_payload(is_anonymous="on", reporter_email=""))
        self.assertTrue(form.is_valid(), form.errors)

    def test_anonymous_discards_a_name_typed_before_the_box_was_ticked(self):
        form = IncidentReportForm(data=form_payload(is_anonymous="on"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["reporter_name"], "")
        self.assertEqual(form.cleaned_data["reporter_email"], "")

    def test_a_filled_honeypot_is_rejected(self):
        form = IncidentReportForm(data=form_payload(website="http://spam.example"))
        self.assertFalse(form.is_valid())

    def test_a_one_word_description_is_rejected(self):
        form = IncidentReportForm(data=form_payload(description="bad"))
        self.assertFalse(form.is_valid())
        self.assertIn("description", form.errors)

    def test_a_signed_in_reporter_gets_their_details_prefilled(self):
        user = User.objects.create_user(
            username="grace", email="grace@example.com", password="x", first_name="Grace"
        )
        form = IncidentReportForm(user=user)
        self.assertEqual(form.fields["reporter_email"].initial, "grace@example.com")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SubmissionTests(TestCase):
    def setUp(self):
        Edition.objects.update_or_create(
            year=2026,
            defaults={"name": "PyCon Nigeria 2026", "theme": "2026", "is_current": True, "is_published": True},
        )
        invalidate()
        self.responder = User.objects.create_user(
            username="coc", email="coc@example.com", password="x"
        )
        RoleAssignment.objects.create(
            user=self.responder, role=Role.COC_TEAM.value, conference_year=2026
        )
        mail.outbox = []

    def _submit(self, **overrides):
        form = IncidentReportForm(data=form_payload(**overrides))
        self.assertTrue(form.is_valid(), form.errors)
        return submit_report(data=form.report_data(), year=2026)

    def test_the_team_is_notified_and_the_reporter_acknowledged(self):
        report = self._submit()
        recipients = [set(m.to) for m in mail.outbox]
        self.assertIn({"coc@example.com"}, recipients)
        self.assertIn({"ada@example.com"}, recipients)
        self.assertIn(report.reference, mail.outbox[0].subject + mail.outbox[0].body)

    def test_the_notification_does_not_carry_the_incident_description(self):
        """
        The rule the whole design turns on. An email is forwarded, backed up and
        read in public; the description stays behind a login.
        """
        self._submit()
        for message in mail.outbox:
            haystack = message.subject + message.body + "".join(
                body for body, _ in getattr(message, "alternatives", [])
            )
            self.assertNotIn(GOOD_DESCRIPTION, haystack)
            self.assertNotIn("appearance", haystack)

    def test_an_anonymous_report_keeps_nothing_identifying(self):
        report = self._submit(is_anonymous="on")
        self.assertTrue(report.is_anonymous)
        self.assertEqual(report.reporter_name, "")
        self.assertEqual(report.reporter_email, "")
        self.assertIsNone(report.reporter_user)
        self.assertEqual(report.reporter_label, "Anonymous")
        self.assertFalse(report.can_reply)

    def test_an_anonymous_report_is_not_linked_to_the_signed_in_account(self):
        user = User.objects.create_user(username="ada", email="ada@example.com", password="x")
        form = IncidentReportForm(data=form_payload(is_anonymous="on"))
        self.assertTrue(form.is_valid(), form.errors)
        report = submit_report(data=form.report_data(), user=user, year=2026)
        self.assertIsNone(report.reporter_user)
        self.assertEqual(report.reporter_email, "")

    def test_an_anonymous_report_sends_nothing_to_a_reporter(self):
        self._submit(is_anonymous="on")
        self.assertEqual([m.to for m in mail.outbox], [["coc@example.com"]])

    def test_the_audit_trail_records_the_report_without_its_contents(self):
        report = self._submit()
        entry = AuditEntry.objects.filter(target_id=str(report.pk)).first()
        self.assertIsNotNone(entry)
        self.assertIn(report.reference, entry.note)
        self.assertNotIn(GOOD_DESCRIPTION, entry.note)

    def test_a_report_is_saved_even_with_nobody_to_send_it_to(self):
        RoleAssignment.objects.all().delete()
        with self.assertLogs("conduct.services", level="ERROR") as logs:
            report = self._submit()
        self.assertTrue(IncidentReport.objects.filter(pk=report.pk).exists())
        self.assertIn(report.reference, "\n".join(logs.output))

    def test_a_standing_team_assignment_covers_an_unstaffed_year(self):
        RoleAssignment.objects.all().delete()
        RoleAssignment.objects.create(
            user=self.responder, role=Role.COC_TEAM.value, conference_year=None
        )
        self._submit()
        self.assertIn(["coc@example.com"], [m.to for m in mail.outbox])


class StatusTests(TestCase):
    def setUp(self):
        Edition.objects.update_or_create(
            year=2026, defaults={"name": "2026", "theme": "2026", "is_current": True, "is_published": True}
        )
        invalidate()
        self.report = IncidentReport.objects.create(
            conference_year=2026, description=GOOD_DESCRIPTION
        )
        self.responder = User.objects.create_user(
            username="coc", email="coc@example.com", password="x"
        )

    def test_moving_off_new_stamps_acknowledgement_and_records_the_move(self):
        self.assertIsNone(self.report.acknowledged_at)
        set_status(self.report, IncidentReport.STATUS_INVESTIGATING, actor=self.responder)
        self.report.refresh_from_db()
        self.assertIsNotNone(self.report.acknowledged_at)
        self.assertEqual(self.report.handled_by, self.responder)
        self.assertTrue(self.report.is_open)
        entry = AuditEntry.objects.filter(action__icontains="status").first()
        self.assertEqual(entry.old_value, "New")
        self.assertEqual(entry.new_value, "Investigating")

    def test_closing_stamps_closed_at_and_reopening_clears_it(self):
        set_status(self.report, IncidentReport.STATUS_RESOLVED, actor=self.responder)
        self.report.refresh_from_db()
        self.assertIsNotNone(self.report.closed_at)
        self.assertFalse(self.report.is_open)

        set_status(self.report, IncidentReport.STATUS_INVESTIGATING, actor=self.responder)
        self.report.refresh_from_db()
        self.assertIsNone(self.report.closed_at)

    def test_a_note_keeps_its_author_as_written(self):
        set_status(
            self.report,
            IncidentReport.STATUS_ACKNOWLEDGED,
            actor=self.responder,
            note="Spoke to both people separately.",
        )
        note = self.report.notes.first()
        self.assertEqual(note.author, self.responder)
        self.assertEqual(note.author_label, "coc@example.com")


class PermissionTests(TestCase):
    """Who may read a report. The answer is: the team, and only by name."""

    def setUp(self):
        from conduct.wagtail_hooks import CoCTeamOnly, is_coc_team

        self.helper_class = CoCTeamOnly
        self.is_coc_team = is_coc_team
        Edition.objects.update_or_create(
            year=2026, defaults={"name": "2026", "theme": "2026", "is_current": True, "is_published": True}
        )
        invalidate()

    def _helper(self):
        return self.helper_class(model=IncidentReport)

    def test_the_team_may_read(self):
        user = User.objects.create_user(username="coc", email="coc@example.com", password="x")
        RoleAssignment.objects.create(user=user, role=Role.COC_TEAM.value, conference_year=2026)
        self.assertTrue(self.is_coc_team(user))
        self.assertTrue(self._helper().user_can_list(user))

    def test_an_organizer_may_not(self):
        user = User.objects.create_user(username="org", email="org@example.com", password="x")
        RoleAssignment.objects.create(user=user, role=Role.ORGANIZER.value, conference_year=2026)
        self.assertFalse(self.is_coc_team(user))
        self.assertFalse(self._helper().user_can_list(user))

    def test_a_superuser_may_not_either(self):
        """
        Deliberate. A report may be about an organizer, and a superuser is usually
        one. Access is granted to named people so the community can be told
        truthfully who reads these.
        """
        user = User.objects.create_superuser(
            username="root", email="root@example.com", password="x"
        )
        self.assertIn(Role.SUPER_ADMIN, roles_for(user))
        self.assertFalse(self.is_coc_team(user))
        self.assertFalse(self._helper().user_can_list(user))

    def test_nobody_may_create_or_delete(self):
        user = User.objects.create_user(username="coc", email="coc@example.com", password="x")
        RoleAssignment.objects.create(user=user, role=Role.COC_TEAM.value, conference_year=2026)
        helper = self._helper()
        report = IncidentReport.objects.create(conference_year=2026, description=GOOD_DESCRIPTION)
        self.assertFalse(helper.user_can_create(user))
        self.assertFalse(helper.user_can_delete_obj(user, report))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ViewTests(TestCase):
    """The public path, including that the new pages pass the frontend audit."""

    @classmethod
    def setUpTestData(cls):
        from conduct.models import CodeOfConductPage
        from home.models import HomePage

        Edition.objects.update_or_create(
            year=2026,
            defaults={"name": "PyCon Nigeria 2026", "theme": "2026", "is_current": True, "is_published": True},
        )
        invalidate()

        root = Page.objects.get(depth=1)
        home = HomePage(title="PyCon Nigeria 2026", slug="home-2026")
        root.add_child(instance=home)
        home.save_revision().publish()

        site = Site.objects.get(is_default_site=True)
        site.root_page = home
        site.save()

        coc = CodeOfConductPage(
            title="Code of Conduct",
            slug="code-of-conduct",
            intro="<p>Everyone is welcome here.</p>",
            report_intro="<p>Only the Code of Conduct team will read it.</p>",
            response_promise="We aim to respond within 24 hours.",
        )
        home.add_child(instance=coc)
        coc.save_revision().publish()
        cls.coc = coc

    def setUp(self):
        invalidate()
        mail.outbox = []

    def test_the_form_is_reachable_without_signing_in(self):
        response = self.client.get("/conduct/report/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Report a Code of Conduct breach")

    def test_a_valid_report_redirects_and_shows_the_reference_once(self):
        response = self.client.post("/conduct/report/", form_payload(), follow=True)
        self.assertEqual(response.status_code, 200)
        report = IncidentReport.objects.get()
        self.assertContains(response, report.reference)

        # Shown once: popped from the session, not carried in the URL.
        again = self.client.get("/conduct/report/submitted/")
        self.assertNotContains(again, report.reference)
        self.assertContains(again, "Nothing to show here")

    def test_the_reference_is_never_put_in_the_url(self):
        response = self.client.post("/conduct/report/", form_payload())
        self.assertEqual(response.status_code, 302)
        report = IncidentReport.objects.get()
        self.assertNotIn(report.reference, response["Location"])

    def test_an_invalid_report_comes_back_with_the_error_and_saves_nothing(self):
        response = self.client.post("/conduct/report/", form_payload(description="no"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(IncidentReport.objects.exists())
        self.assertContains(response, "Please say a little more")

    def test_the_code_of_conduct_page_offers_the_form(self):
        response = self.client.get("/code-of-conduct/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/conduct/report/")
        self.assertContains(response, "We aim to respond within 24 hours.")

    def test_the_new_pages_have_no_accessibility_errors(self):
        from quality.audit import audit_html, errors

        for url in ("/code-of-conduct/", "/conduct/report/"):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                found = errors(
                    audit_html(response.content.decode(), internal_hosts=("testserver",))
                )
                self.assertEqual(found, [], "\n".join(str(f) for f in found))

    def test_the_confirmation_page_has_no_accessibility_errors(self):
        from quality.audit import audit_html, errors

        self.client.post("/conduct/report/", form_payload())
        response = self.client.get("/conduct/report/submitted/")
        found = errors(audit_html(response.content.decode(), internal_hosts=("testserver",)))
        self.assertEqual(found, [], "\n".join(str(f) for f in found))

    def test_the_url_tag_resolves_the_live_page(self):
        """
        The ticket purchase page used to offer the Code of Conduct behind
        href="#" -- a link to nothing, on the page where somebody hands over money
        to agree to it. It goes through this tag now, so it follows the page even
        if an organizer changes its slug.
        """
        from django.core.cache import cache

        from conduct.templatetags.conduct_tags import code_of_conduct_url, terms_url

        cache.clear()
        self.assertEqual(code_of_conduct_url(), "/code-of-conduct/")
        # No terms page exists, and the tag says so rather than inventing a link.
        self.assertEqual(terms_url(), "")

    def test_the_url_tag_falls_back_to_the_form_with_no_page(self):
        from django.core.cache import cache

        from conduct.models import CodeOfConductPage
        from conduct.templatetags.conduct_tags import code_of_conduct_url

        CodeOfConductPage.objects.all().delete()
        cache.clear()
        self.assertEqual(code_of_conduct_url(), "/conduct/report/")
