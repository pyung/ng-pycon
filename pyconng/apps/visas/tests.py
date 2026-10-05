"""
Tests for visa invitation letters.

Weighted towards two things that would be expensive to get wrong.

The first is what the letter *claims*. It is a statement to a foreign government on
our letterhead, so the tests check that eligibility comes from records rather than
from the form, that it is re-read at decision time, and that the paragraph about who
pays says the right thing for each kind of applicant.

The second is the passport data: that it is masked everywhere but one audited read,
that the read is recorded, and that it is actually deleted on schedule rather than
merely promised.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import RoleAssignment
from accounts.roles import Role, roles_for
from audit.models import AuditEntry
from editions.current import invalidate
from editions.models import Edition
from visas import eligibility, letter, retention, services
from visas.models import VisaLetterRequest, VisaSettings, generate_reference
from visas.tests_pdf import page_text

User = get_user_model()
YEAR = 2026


def make_edition(starts_in_days=90):
    starts = timezone.localdate() + timedelta(days=starts_in_days)
    Edition.objects.update_or_create(
        year=YEAR,
        defaults={
            "name": f"PyCon Nigeria {YEAR}",
            "theme": "2026",
            "is_current": True,
            "is_published": True,
            "starts_on": starts,
            "ends_on": starts + timedelta(days=2),
            "venue": "Landmark Centre, Lagos",
        },
    )
    invalidate()


def open_visas(**kwargs):
    settings_obj = VisaSettings.for_year(YEAR)
    settings_obj.status = VisaSettings.STATUS_OPEN
    settings_obj.request_deadline = timezone.now() + timedelta(days=20)
    settings_obj.organisation_name = "PyCon Nigeria"
    settings_obj.contact_email = "visa@pynigeria.com"
    settings_obj.signatory_name = "Ada Lovelace"
    settings_obj.signatory_title = "Conference Chair"
    for key, value in kwargs.items():
        setattr(settings_obj, key, value)
    settings_obj.save()
    return settings_obj


def make_user(email="ada@example.com", **kwargs):
    return User.objects.create_user(
        username=email.split("@")[0], email=email, password="secret", **kwargs
    )


def give_paid_ticket(user):
    from tickets.issuing import REASON_ORGANIZER, issue_complimentary_ticket

    ticket, _ = issue_complimentary_ticket(
        user=user, year=YEAR, reason=REASON_ORGANIZER, notify=False
    )
    return ticket


def make_speaker(user):
    from cfp.models import Proposal, Speaker, Track

    speaker = Speaker.objects.create(
        user=user,
        full_name=user.get_full_name() or user.email,
        bio="Writes Python, mostly.",
        country="KE",
        conference_year=YEAR,
    )
    track, _ = Track.objects.get_or_create(name="Web", conference_year=YEAR)
    return Proposal.objects.create(
        speaker=speaker,
        conference_year=YEAR,
        title="A talk about testing",
        abstract="Why the tests you do not write are the ones that bite.",
        description="A longer description of the same.",
        track=track,
        format="talk",
        duration=30,
        audience_level="beginner",
        status=Proposal.STATUS_ACCEPTED,
    )


def make_grantee(user, status=None):
    from grants.models import TravelGrantApplication

    return TravelGrantApplication.objects.create(
        user=user,
        conference_year=YEAR,
        status=status or TravelGrantApplication.STATUS_APPROVED,
        country_of_residence="Kenya",
        city="Nairobi",
        financial_need_reason="I cannot cover the flight.",
        employment_status="student",
        community_impact="I run a Python meetup in Nairobi.",
        estimated_transport_cost=Decimal("200000"),
        estimated_accommodation_cost=Decimal("100000"),
        approved_amount=Decimal("250000"),
        submitted_at=timezone.now(),
    )


def make_request(user, status=VisaLetterRequest.STATUS_SUBMITTED, **kwargs):
    defaults = {
        "conference_year": YEAR,
        "status": status,
        "basis": VisaLetterRequest.BASIS_ATTENDEE,
        "passport_surname": "Lovelace",
        "passport_given_names": "Ada Augusta",
        "nationality": "Kenyan",
        "passport_number": "A01234567",
        "passport_expiry": timezone.localdate() + timedelta(days=900),
        "date_of_birth": timezone.localdate() - timedelta(days=11000),
        "embassy_city": "Nairobi",
        "embassy_country": "Kenya",
        "arrival_on": timezone.localdate() + timedelta(days=88),
        "departure_on": timezone.localdate() + timedelta(days=95),
        "submitted_at": timezone.now(),
    }
    defaults.update(kwargs)
    return VisaLetterRequest.objects.create(user=user, **defaults)


class EligibilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_edition()

    def setUp(self):
        invalidate()
        open_visas()
        self.user = make_user()

    def test_somebody_with_no_standing_is_refused_and_told_how_to_qualify(self):
        result = eligibility.assess(self.user, YEAR)
        self.assertFalse(result.ok)
        self.assertIn("ticket holders", result.reason)

    def test_an_anonymous_visitor_is_asked_to_sign_in(self):
        from django.contrib.auth.models import AnonymousUser

        result = eligibility.assess(AnonymousUser(), YEAR)
        self.assertFalse(result.ok)
        self.assertIn("sign in", result.reason)

    def test_a_paid_ticket_qualifies(self):
        give_paid_ticket(self.user)
        result = eligibility.assess(self.user, YEAR)
        self.assertTrue(result.ok)
        self.assertEqual(result.basis, VisaLetterRequest.BASIS_ATTENDEE)

    def test_an_unpaid_ticket_does_not(self):
        from tickets.models import Ticket, TicketType

        ticket_type = TicketType.objects.create(
            name="Personal", conference_year=YEAR, price=Decimal("60000")
        )
        Ticket.objects.create(
            user=self.user, conference_year=YEAR, ticket_type=ticket_type,
            status=Ticket.ISSUED, amount=Decimal("60000"), quantity=1,
        )
        self.assertFalse(eligibility.assess(self.user, YEAR).ok)

    def test_an_accepted_speaker_qualifies(self):
        make_speaker(self.user)
        self.assertEqual(
            eligibility.assess(self.user, YEAR).basis, VisaLetterRequest.BASIS_SPEAKER
        )

    def test_a_grant_offer_qualifies_before_it_is_accepted(self):
        """
        The loosening this module argues for: the acceptance window is a fortnight
        and an embassy needs three months, so waiting for the acceptance would lock
        out exactly the people the grant exists for.
        """
        from grants.models import TravelGrantApplication

        make_grantee(self.user, status=TravelGrantApplication.STATUS_APPROVED)
        result = eligibility.assess(self.user, YEAR)
        self.assertTrue(result.ok)
        self.assertEqual(result.basis, VisaLetterRequest.BASIS_GRANTEE)

    def test_a_rejected_grant_application_does_not_qualify(self):
        from grants.models import TravelGrantApplication

        make_grantee(self.user, status=TravelGrantApplication.STATUS_NOT_SELECTED)
        self.assertFalse(eligibility.assess(self.user, YEAR).ok)

    def test_a_declined_grant_does_not_qualify(self):
        from grants.models import TravelGrantApplication

        make_grantee(self.user, status=TravelGrantApplication.STATUS_DECLINED)
        self.assertFalse(eligibility.assess(self.user, YEAR).ok)

    def test_an_organizer_qualifies(self):
        RoleAssignment.objects.create(
            user=self.user, role=Role.ORGANIZER.value, conference_year=YEAR
        )
        self.assertEqual(
            eligibility.assess(self.user, YEAR).basis, VisaLetterRequest.BASIS_ORGANIZER
        )

    def test_speaker_wins_over_ticket_holder(self):
        """
        The letter should make the strongest true claim. A speaker who also bought a
        ticket is a speaker.
        """
        give_paid_ticket(self.user)
        make_speaker(self.user)
        result = eligibility.assess(self.user, YEAR)
        self.assertEqual(result.basis, VisaLetterRequest.BASIS_SPEAKER)
        self.assertIn(VisaLetterRequest.BASIS_ATTENDEE, result.all_bases)

    def test_eligibility_is_scoped_to_the_edition(self):
        give_paid_ticket(self.user)
        self.assertFalse(eligibility.assess(self.user, YEAR - 1).ok)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class RequestLifecycleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_edition()

    def setUp(self):
        invalidate()
        mail.outbox = []
        open_visas()
        self.user = make_user()
        give_paid_ticket(self.user)
        self.officer = make_user("officer@example.com")
        RoleAssignment.objects.create(
            user=self.officer, role=Role.VISA_OFFICER.value, conference_year=YEAR
        )

    def test_submitting_records_the_basis_from_records_not_the_form(self):
        obj = make_request(
            self.user,
            status=VisaLetterRequest.STATUS_DRAFT,
            basis=VisaLetterRequest.BASIS_SPEAKER,   # a lie, as if posted
            submitted_at=None,
        )
        services.submit(obj)
        obj.refresh_from_db()
        self.assertEqual(obj.basis, VisaLetterRequest.BASIS_ATTENDEE)
        self.assertEqual(obj.status, VisaLetterRequest.STATUS_SUBMITTED)
        self.assertIsNotNone(obj.submitted_at)

    def test_submitting_without_standing_is_refused(self):
        stranger = make_user("nobody@example.com")
        obj = make_request(stranger, status=VisaLetterRequest.STATUS_DRAFT)
        with self.assertRaises(services.VisaError):
            services.submit(obj)

    def test_the_applicant_is_told_it_arrived(self):
        obj = make_request(
            self.user, status=VisaLetterRequest.STATUS_DRAFT, submitted_at=None
        )
        services.submit(obj)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("visa letter request", mail.outbox[0].subject.lower())

    def test_correcting_a_submitted_request_does_not_resend_the_receipt(self):
        obj = make_request(
            self.user, status=VisaLetterRequest.STATUS_DRAFT, submitted_at=None
        )
        services.submit(obj)
        mail.outbox = []
        services.submit(obj)
        self.assertEqual(len(mail.outbox), 0)

    def test_correcting_keeps_the_original_submission_time(self):
        """
        Otherwise fixing a typo sends somebody to the back of a queue ordered by how
        long people have waited, which punishes exactly the care we asked for.
        """
        obj = make_request(
            self.user, status=VisaLetterRequest.STATUS_DRAFT, submitted_at=None
        )
        services.submit(obj)
        obj.refresh_from_db()
        first = obj.submitted_at
        services.submit(obj)
        obj.refresh_from_db()
        self.assertEqual(obj.submitted_at, first)

    def test_approving_issues_a_reference_and_a_letter(self):
        obj = make_request(self.user)
        services.approve(obj, by=self.officer)
        obj.refresh_from_db()
        self.assertEqual(obj.status, VisaLetterRequest.STATUS_APPROVED)
        self.assertTrue(obj.reference.startswith(f"PYNG-{YEAR}-"))
        self.assertIsNotNone(obj.letter_issued_at)
        self.assertTrue(obj.has_letter)

    def test_the_approval_email_carries_the_letter(self):
        obj = make_request(self.user)
        services.approve(obj, by=self.officer)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(len(message.attachments), 1)
        name, content, mimetype = message.attachments[0]
        self.assertTrue(name.endswith(".pdf"))
        self.assertEqual(mimetype, "application/pdf")
        self.assertTrue(content.startswith(b"%PDF-"))

    def test_eligibility_is_checked_again_at_the_decision(self):
        """
        The gap between submission and decision is where a refunded ticket turns a
        true statement on our letterhead into a false one.
        """
        from tickets.models import Ticket

        obj = make_request(self.user)
        Ticket.objects.filter(user=self.user).update(status=Ticket.REFUNDED)
        with self.assertRaises(services.VisaError) as caught:
            services.approve(obj, by=self.officer)
        self.assertIn("no longer eligible", str(caught.exception))
        obj.refresh_from_db()
        self.assertEqual(obj.status, VisaLetterRequest.STATUS_SUBMITTED)

    def test_the_basis_is_refreshed_at_the_decision(self):
        obj = make_request(self.user, basis=VisaLetterRequest.BASIS_ATTENDEE)
        make_speaker(self.user)
        services.approve(obj, by=self.officer)
        obj.refresh_from_db()
        self.assertEqual(obj.basis, VisaLetterRequest.BASIS_SPEAKER)

    def test_approving_twice_keeps_one_reference(self):
        obj = make_request(self.user)
        services.approve(obj, by=self.officer)
        first = obj.reference
        services.approve(obj, by=self.officer)
        obj.refresh_from_db()
        self.assertEqual(obj.reference, first)

    def test_rejecting_requires_a_reason(self):
        obj = make_request(self.user)
        with self.assertRaises(services.VisaError) as caught:
            services.reject(obj, by=self.officer, note="  ")
        self.assertIn("Say why", str(caught.exception))
        obj.refresh_from_db()
        self.assertEqual(obj.status, VisaLetterRequest.STATUS_SUBMITTED)

    def test_rejecting_tells_the_applicant_why(self):
        obj = make_request(self.user)
        services.reject(
            obj, by=self.officer, note="Your passport expires before the conference."
        )
        obj.refresh_from_db()
        self.assertEqual(obj.status, VisaLetterRequest.STATUS_REJECTED)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("expires before the conference", mail.outbox[0].body)

    def test_withdrawing_an_issued_letter_keeps_the_reference(self):
        """
        An embassy holding the letter will check that reference, and must be told it
        is not valid -- which is impossible if the reference has been thrown away.
        """
        obj = make_request(self.user)
        services.approve(obj, by=self.officer)
        reference = obj.reference
        services.reject(obj, by=self.officer, note="Issued in error.")
        obj.refresh_from_db()
        self.assertEqual(obj.reference, reference)
        self.assertIsNone(obj.letter_issued_at)
        self.assertFalse(obj.has_letter)

    def test_an_applicant_can_withdraw(self):
        obj = make_request(self.user)
        services.withdraw(obj)
        obj.refresh_from_db()
        self.assertEqual(obj.status, VisaLetterRequest.STATUS_WITHDRAWN)

    def test_a_decided_request_cannot_be_withdrawn(self):
        obj = make_request(self.user)
        services.approve(obj, by=self.officer)
        with self.assertRaises(services.VisaError):
            services.withdraw(obj)

    def test_every_transition_lands_on_the_audit_trail(self):
        obj = make_request(
            self.user, status=VisaLetterRequest.STATUS_DRAFT, submitted_at=None
        )
        services.submit(obj)
        services.approve(obj, by=self.officer)
        actions = list(
            AuditEntry.objects.filter(target_id=str(obj.pk)).values_list("action", flat=True)
        )
        self.assertTrue(any("Submitted" in a for a in actions))
        self.assertTrue(any("Approved" in a for a in actions))

    def test_one_request_per_person_per_edition(self):
        from django.db import IntegrityError, transaction

        make_request(self.user)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_request(self.user)


class ReferenceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_edition()

    def test_the_alphabet_avoids_the_characters_people_mishear(self):
        """A consular officer reads these down a phone line."""
        from visas.models import REFERENCE_ALPHABET

        for confusable in "O0I1L":
            self.assertNotIn(confusable, REFERENCE_ALPHABET)

    def test_references_are_unique_over_many_draws(self):
        taken = set()
        for _ in range(500):
            reference = generate_reference(YEAR, existing=taken)
            self.assertTrue(reference.startswith(f"PYNG-{YEAR}-"))
        self.assertEqual(len(taken), 500)


class PassportDataTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_edition()

    def setUp(self):
        invalidate()
        open_visas()
        self.user = make_user()
        give_paid_ticket(self.user)
        self.officer = make_user("officer@example.com")
        RoleAssignment.objects.create(
            user=self.officer, role=Role.VISA_OFFICER.value, conference_year=YEAR
        )
        self.request_obj = make_request(self.user)

    def test_the_mask_shows_enough_to_recognise_and_not_enough_to_use(self):
        self.assertEqual(self.request_obj.passport_number_masked, "A•••••567")
        self.assertNotIn("01234", self.request_obj.passport_number_masked)

    def test_a_short_number_is_masked_completely(self):
        self.request_obj.passport_number = "A123"
        self.assertEqual(self.request_obj.passport_number_masked, "••••")

    def test_reading_the_full_number_is_recorded_before_it_is_returned(self):
        number = services.reveal_passport_number(
            self.request_obj, by=self.officer, reason="Checking against the letter"
        )
        self.assertEqual(number, "A01234567")
        entry = AuditEntry.objects.filter(action="Viewed full passport number").first()
        self.assertIsNotNone(entry)
        self.assertIn("Checking against the letter", entry.note)

    def test_the_trail_never_copies_the_number_into_itself(self):
        """Recording the value would create a second, longer-lived copy."""
        services.reveal_passport_number(self.request_obj, by=self.officer)
        for entry in AuditEntry.objects.all():
            blob = " ".join(
                str(getattr(entry, field, "")) for field in
                ("note", "old_value", "new_value", "action")
            )
            self.assertNotIn("A01234567", blob)

    def test_nothing_but_the_visa_officer_holds_the_role(self):
        self.assertTrue(roles_for(self.officer, YEAR).has(Role.VISA_OFFICER))
        self.assertFalse(roles_for(self.user, YEAR).has(Role.VISA_OFFICER))

    def test_a_superuser_does_not_get_the_role_by_being_a_superuser(self):
        """
        Same reasoning as the Code of Conduct team: read access to passport numbers
        is given to named people, not inherited from being able to log in.
        """
        root = make_user("root@example.com", is_superuser=True, is_staff=True)
        self.assertFalse(roles_for(root, YEAR).has(Role.VISA_OFFICER))


class RetentionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_edition(starts_in_days=90)

    def setUp(self):
        invalidate()
        open_visas(retain_details_days=30)
        self.user = make_user()
        give_paid_ticket(self.user)
        self.request_obj = make_request(self.user)

    def test_nothing_is_deleted_before_the_conference(self):
        self.assertEqual(retention.due(YEAR).count(), 0)
        self.assertEqual(retention.purge_year(YEAR), 0)
        self.request_obj.refresh_from_db()
        self.assertTrue(self.request_obj.details_retained)

    def test_the_deletion_date_comes_from_the_edition_plus_the_window(self):
        edition = Edition.objects.get(year=YEAR)
        settings_obj = VisaSettings.for_year(YEAR)
        self.assertEqual(
            settings_obj.purge_on_or_after(),
            edition.ends_on + timedelta(days=30),
        )

    def test_details_are_deleted_once_the_window_has_passed(self):
        make_edition(starts_in_days=-100)
        invalidate()
        self.assertEqual(retention.due(YEAR).count(), 1)
        self.assertEqual(retention.purge_year(YEAR), 1)

        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.passport_number, "")
        self.assertIsNone(self.request_obj.date_of_birth)
        self.assertIsNone(self.request_obj.passport_expiry)
        self.assertIsNotNone(self.request_obj.purged_at)

    def test_the_letter_and_its_reference_outlive_the_passport_details(self):
        officer = make_user("officer@example.com")
        services.approve(self.request_obj, by=officer, notify=False)
        reference = self.request_obj.reference

        make_edition(starts_in_days=-100)
        invalidate()
        retention.purge_year(YEAR)

        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.reference, reference)
        self.assertEqual(self.request_obj.passport_surname, "Lovelace")
        self.assertTrue(self.request_obj.has_letter)
        # And the reference still resolves, which is the point of keeping it.
        self.assertEqual(services.verify(reference).pk, self.request_obj.pk)

    def test_purging_is_idempotent(self):
        make_edition(starts_in_days=-100)
        invalidate()
        retention.purge_year(YEAR)
        self.assertEqual(retention.purge_year(YEAR), 0)

    def test_a_dry_run_changes_nothing(self):
        make_edition(starts_in_days=-100)
        invalidate()
        self.assertEqual(retention.purge_year(YEAR, dry_run=True), 1)
        self.request_obj.refresh_from_db()
        self.assertTrue(self.request_obj.details_retained)

    def test_an_officer_can_delete_early_and_it_is_recorded(self):
        officer = make_user("officer@example.com")
        self.assertEqual(retention.purge_year(YEAR, by=officer, force=True), 1)
        self.request_obj.refresh_from_db()
        self.assertFalse(self.request_obj.details_retained)
        entry = AuditEntry.objects.filter(action="Deleted passport details").first()
        self.assertIsNotNone(entry)
        self.assertIn("Manual deletion", entry.note)

    def test_an_edition_without_dates_deletes_nothing_rather_than_guessing(self):
        Edition.objects.filter(year=YEAR).update(starts_on=None, ends_on=None)
        invalidate()
        self.assertIsNone(VisaSettings.for_year(YEAR).purge_on_or_after())
        self.assertEqual(retention.purge_year(YEAR), 0)


class DeadlineTests(TestCase):
    def setUp(self):
        invalidate()

    def test_the_recommended_deadline_is_derived_from_the_conference_dates(self):
        make_edition(starts_in_days=200)
        settings_obj = open_visas(embassy_lead_days=90)
        edition = Edition.objects.get(year=YEAR)
        self.assertEqual(
            settings_obj.recommended_deadline, edition.starts_on - timedelta(days=90)
        )

    def test_a_deadline_later_than_the_lead_time_is_flagged(self):
        """
        A deadline two weeks before the conference reads as organised and is, for
        this one thing, useless. Better to say so than to accept it quietly.
        """
        make_edition(starts_in_days=60)
        settings_obj = open_visas(
            embassy_lead_days=90,
            request_deadline=timezone.now() + timedelta(days=50),
        )
        self.assertTrue(settings_obj.deadline_is_too_late)

    def test_a_deadline_with_enough_room_is_not_flagged(self):
        make_edition(starts_in_days=200)
        settings_obj = open_visas(
            embassy_lead_days=90,
            request_deadline=timezone.now() + timedelta(days=30),
        )
        self.assertFalse(settings_obj.deadline_is_too_late)

    def test_an_edition_without_dates_makes_no_recommendation(self):
        make_edition()
        Edition.objects.filter(year=YEAR).update(starts_on=None, ends_on=None)
        invalidate()
        settings_obj = open_visas()
        self.assertIsNone(settings_obj.recommended_deadline)
        self.assertFalse(settings_obj.deadline_is_too_late)

    def test_a_passed_deadline_closes_the_window_on_save(self):
        make_edition()
        settings_obj = open_visas(request_deadline=timezone.now() - timedelta(days=1))
        self.assertEqual(settings_obj.status, VisaSettings.STATUS_CLOSED)
        self.assertFalse(settings_obj.is_open)

    def test_a_window_that_has_not_opened_yet_is_not_open(self):
        make_edition()
        settings_obj = open_visas(opens_on=timezone.now() + timedelta(days=5))
        self.assertFalse(settings_obj.has_opened)
        self.assertFalse(settings_obj.is_open)


class LetterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_edition()

    def setUp(self):
        invalidate()
        self.settings = open_visas(
            organisation_address="12 Herbert Macaulay Way\nYaba, Lagos",
            organisation_registration="RC 1234567",
        )
        self.user = make_user()
        give_paid_ticket(self.user)
        self.officer = make_user("officer@example.com")

    def _approved(self, **kwargs):
        obj = make_request(self.user, **kwargs)
        services.approve(obj, by=self.officer, notify=False)
        return obj

    def test_a_letter_needs_an_approved_request(self):
        obj = make_request(self.user)
        with self.assertRaises(ValueError):
            letter.build(obj)

    def test_the_letter_carries_the_things_an_embassy_looks_for(self):
        obj = self._approved()
        text = " ".join(page_text(letter.build(obj)))
        self.assertIn(obj.reference, text)
        self.assertIn("LOVELACE, Ada Augusta", text)
        self.assertIn("A01234567", text)
        self.assertIn("Kenyan", text)
        self.assertIn("RC 1234567", text)
        self.assertIn("Ada Lovelace", text)        # the signatory
        self.assertIn("Conference Chair", text)

    def test_the_letter_is_addressed_to_the_mission_the_applicant_named(self):
        obj = self._approved()
        text = " ".join(page_text(letter.build(obj)))
        self.assertIn("Nairobi, Kenya", text)

    def test_a_self_funding_delegate_is_not_described_as_funded(self):
        """
        Saying otherwise to a foreign government, on our letterhead, about money we
        are not providing.
        """
        obj = self._approved()
        text = " ".join(page_text(letter.build(obj)))
        self.assertIn("not responsible for this delegate's travel", text)
        self.assertNotIn("has awarded this delegate a travel grant", text)

    def test_a_grant_recipient_letter_says_what_we_are_paying(self):
        make_grantee(self.user)
        obj = self._approved(basis=VisaLetterRequest.BASIS_GRANTEE)
        text = " ".join(page_text(letter.build(obj)))
        self.assertIn("has awarded this delegate a travel grant", text)
        self.assertIn("remain the delegate's own", text)

    def test_a_speaker_letter_says_the_registration_is_waived_and_no_more(self):
        make_speaker(self.user)
        obj = self._approved(basis=VisaLetterRequest.BASIS_SPEAKER)
        text = " ".join(page_text(letter.build(obj)))
        self.assertIn("registration has been waived", text)
        self.assertIn("is not responsible for their travel", text)

    def test_the_letter_says_what_it_is_not(self):
        obj = self._approved()
        text = " ".join(page_text(letter.build(obj)))
        self.assertIn("It is not a visa", text)

    def test_the_letter_carries_the_verification_route(self):
        obj = self._approved()
        text = " ".join(page_text(letter.build(obj)))
        self.assertIn("/visa/verify/", text)
        self.assertIn(obj.reference, text)

    def test_verification_is_replaced_by_an_address_when_it_is_switched_off(self):
        self.settings.verification_enabled = False
        self.settings.save()
        obj = self._approved()
        text = " ".join(page_text(letter.build(obj)))
        self.assertNotIn("/visa/verify/", text)
        self.assertIn("visa@pynigeria.com", text)

    def test_a_purged_request_still_produces_a_letter_without_the_passport_number(self):
        """
        An applicant re-downloading after the event should get a letter, not an
        error -- and it must not contain details we have deleted.
        """
        obj = self._approved()
        obj.purge_details()
        text = " ".join(page_text(letter.build(obj)))
        self.assertIn(obj.reference, text)
        self.assertIn("LOVELACE, Ada Augusta", text)
        self.assertNotIn("A01234567", text)

    def test_the_filename_is_one_an_applicant_can_send_as_is(self):
        obj = self._approved()
        name = letter.filename(obj)
        self.assertTrue(name.endswith(".pdf"))
        self.assertIn("Lovelace", name)
        self.assertIn(obj.reference, name)


class OverviewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_edition()

    def setUp(self):
        invalidate()
        open_visas(processing_days=5)

    def _submitted(self, email, days_ago):
        user = make_user(email)
        give_paid_ticket(user)
        return make_request(
            user, submitted_at=timezone.now() - timedelta(days=days_ago)
        )

    def test_the_queue_puts_the_longest_waiting_first(self):
        recent = self._submitted("recent@example.com", 1)
        old = self._submitted("old@example.com", 9)
        order = [r.pk for r in services.queue(YEAR)]
        self.assertEqual(order, [old.pk, recent.pk])

    def test_overdue_counts_the_ones_past_the_promised_turnaround(self):
        self._submitted("recent@example.com", 1)
        self._submitted("old@example.com", 9)
        self.assertEqual(services.overview(YEAR)["overdue"], 1)

    def test_a_decided_request_stops_counting_as_waiting(self):
        obj = self._submitted("old@example.com", 9)
        services.approve(obj, by=make_user("officer@example.com"), notify=False)
        self.assertIsNone(obj.days_waiting)
        self.assertEqual(services.overview(YEAR)["overdue"], 0)

    def test_drafts_stay_out_of_the_queue(self):
        user = make_user("drafting@example.com")
        give_paid_ticket(user)
        make_request(user, status=VisaLetterRequest.STATUS_DRAFT)
        self.assertEqual(services.queue(YEAR).count(), 0)
