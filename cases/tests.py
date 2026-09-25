from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from django.core.files.uploadedfile import SimpleUploadedFile
from accounts.models import UserProfile
from accounts.permissions import can_access_application, can_access_case
from cases.models import Application, CaseRecord, CaseEvent, Task, RelatedCase, DuplicateCandidate
from referrals.models import Referral
from lawyers.models import LawyerAssignment
from mediation.models import Mediation
from documents.models import Document
from cases.ai_service import MockAIService
from cases.services import (
    submit_application,
    accept_application,
    reject_application,
    generate_application_id,
    generate_case_id,
    change_case_priority,
    assign_lawyer_to_case,
    accept_lawyer_assignment,
    decline_lawyer_assignment,
    add_lawyer_case_update,
    request_lawyer_change,
    create_case_referral,
    acknowledge_referral,
    return_referral,
    complete_referral,
    initiate_case_mediation,
    update_mediation_outcome,
    transition_case_status,
    upload_case_document,
    verify_case_document,
    link_related_cases,
    review_duplicate_candidate,
)


class Phase2DatabaseAndAuthTests(TestCase):
    def setUp(self):
        # Create users for multiple roles
        self.citizen_a = User.objects.create_user(username='citizen_a', password='password123')
        self.profile_a = UserProfile.objects.create(
            user=self.citizen_a,
            role=UserProfile.ROLE_CITIZEN,
            phone='01711111111',
            language='bn'
        )

        self.citizen_b = User.objects.create_user(username='citizen_b', password='password123')
        self.profile_b = UserProfile.objects.create(
            user=self.citizen_b,
            role=UserProfile.ROLE_CITIZEN,
            phone='01722222222',
            language='en'
        )

        self.officer = User.objects.create_user(username='officer_dhaka', password='password123')
        self.profile_officer = UserProfile.objects.create(
            user=self.officer,
            role=UserProfile.ROLE_DLAO_OFFICER,
            phone='01733333333',
            language='en'
        )

        self.lawyer = User.objects.create_user(username='panel_lawyer_1', password='password123')
        self.profile_lawyer = UserProfile.objects.create(
            user=self.lawyer,
            role=UserProfile.ROLE_PANEL_LAWYER,
            phone='01744444444',
            language='en'
        )

    def test_user_profile_all_nine_roles(self):
        """Verifies that all 9 roles defined in the PRD can be created and queried."""
        roles = [
            UserProfile.ROLE_CITIZEN,
            UserProfile.ROLE_REPRESENTATIVE,
            UserProfile.ROLE_HELPLINE_AGENT,
            UserProfile.ROLE_DLAO_OFFICER,
            UserProfile.ROLE_DLAO_SUPPORT_STAFF,
            UserProfile.ROLE_UDC_OPERATOR,
            UserProfile.ROLE_PANEL_LAWYER,
            UserProfile.ROLE_MEDIATOR,
            UserProfile.ROLE_ADMIN,
        ]
        self.assertEqual(len(roles), 9)

        for idx, role in enumerate(roles):
            u = User.objects.create_user(username=f'user_role_{idx}', password='password123')
            p = UserProfile.objects.create(user=u, role=role, phone=f'0170000000{idx}')
            self.assertEqual(p.role, role)
            self.assertEqual(u.profile.role, role)

    def test_application_id_generation_and_submission(self):
        """Verifies unique Application ID generation and intake submission."""
        app = submit_application(
            name="Fatema Begum",
            phone="01711111111",
            address="Keraniganj, Dhaka",
            legal_problem="Land dispute / eviction notice",
            incident_description="Received unlawful notice without court order.",
            applicant_user=self.citizen_a,
            safe_contact_number="01711111111",
            safe_contact_time="Morning 10am-12pm",
            language="bn"
        )
        self.assertIsNotNone(app.id)
        self.assertTrue(app.application_id.startswith("APP-"))
        self.assertEqual(app.status, Application.STATUS_SUBMITTED)
        self.assertEqual(app.applicant_user, self.citizen_a)

    def test_case_id_absent_before_acceptance(self):
        """Verifies that Case ID is strictly absent upon application submission."""
        app = submit_application(
            name="Rohim Mia",
            phone="01722222222",
            address="Mirpur, Dhaka",
            legal_problem="Family maintenance",
            incident_description="Non-payment of child support.",
            applicant_user=self.citizen_b
        )
        # Check that no CaseRecord is associated with the submitted application
        self.assertFalse(hasattr(app, 'case_record'))
        self.assertEqual(CaseRecord.objects.filter(application=app).count(), 0)

    def test_case_id_generated_after_officer_acceptance(self):
        """Verifies that Case ID is generated ONLY when a DLAO officer accepts."""
        app = submit_application(
            name="Karim Ullah",
            phone="01733333333",
            address="Dhanmondi, Dhaka",
            legal_problem="Civil property trespass",
            incident_description="Boundary dispute.",
            applicant_user=self.citizen_a
        )
        self.assertFalse(hasattr(app, 'case_record'))

        # DLAO Officer accepts the application
        case_record = accept_application(
            application=app,
            officer=self.officer,
            priority=CaseRecord.PRIORITY_HIGH
        )

        self.assertIsNotNone(case_record.id)
        self.assertTrue(case_record.case_id.startswith("CASE-"))
        self.assertEqual(case_record.assigned_officer, self.officer)
        self.assertEqual(case_record.priority, CaseRecord.PRIORITY_HIGH)
        self.assertEqual(case_record.status, CaseRecord.STATUS_ACCEPTED)

        # Refresh application state
        app.refresh_from_db()
        self.assertEqual(app.status, Application.STATUS_ACCEPTED)
        self.assertEqual(app.case_record, case_record)

    def test_unauthorized_users_cannot_accept_applications(self):
        """Verifies that unauthorized roles (citizen, lawyer) cannot accept applications."""
        app = submit_application(
            name="Test Applicant",
            phone="01744444444",
            address="Savar, Dhaka",
            legal_problem="Wage dispute",
            incident_description="Withheld factory wages.",
            applicant_user=self.citizen_a
        )

        # Citizen attempts to accept
        with self.assertRaises(PermissionDenied):
            accept_application(app, self.citizen_a)

        # Panel lawyer attempts to accept
        with self.assertRaises(PermissionDenied):
            accept_application(app, self.lawyer)

        # Application must remain in SUBMITTED state without a CaseRecord
        app.refresh_from_db()
        self.assertEqual(app.status, Application.STATUS_SUBMITTED)
        self.assertFalse(hasattr(app, 'case_record'))

    def test_case_event_created_for_acceptance(self):
        """Verifies that accepting an application creates an append-only CaseEvent."""
        app = submit_application(
            name="Salma Khatun",
            phone="01755555555",
            address="Uttara, Dhaka",
            legal_problem="Domestic violence protection",
            incident_description="Physical abuse and threats.",
            applicant_user=self.citizen_a
        )
        case_record = accept_application(app, self.officer)

        events = CaseEvent.objects.filter(case=case_record)
        # Should have intake event + acceptance event
        self.assertGreaterEqual(events.count(), 1)
        acceptance_event = events.filter(action='APPLICATION_ACCEPTED').first()
        self.assertIsNotNone(acceptance_event)
        self.assertEqual(acceptance_event.actor, self.officer)
        self.assertEqual(acceptance_event.actor_role, UserProfile.ROLE_DLAO_OFFICER)
        self.assertEqual(acceptance_event.provenance, CaseEvent.PROVENANCE_STAFF_ENTERED)
        self.assertEqual(acceptance_event.authority, 'DLAO Officer')
        self.assertIn(case_record.case_id, acceptance_event.description)

    def test_consequential_operations_are_atomic(self):
        """Verifies transaction.atomic() rollback if an exception occurs during acceptance."""
        app = submit_application(
            name="Atomic Test Applicant",
            phone="01766666666",
            address="Gazipur",
            legal_problem="Contract breach",
            incident_description="Breached agreement.",
            applicant_user=self.citizen_b
        )

        initial_cases_count = CaseRecord.objects.count()

        # Simulate a transaction failure midway
        try:
            with transaction.atomic():
                CaseRecord.objects.create(
                    case_id="TEMP-SIMULATE-FAIL",
                    application=app,
                    assigned_officer=self.officer
                )
                raise RuntimeError("Simulated database failure during transaction.")
        except RuntimeError:
            pass

        # Database state must be rolled back completely
        self.assertEqual(CaseRecord.objects.count(), initial_cases_count)
        app.refresh_from_db()
        self.assertEqual(app.status, Application.STATUS_SUBMITTED)

    def test_citizens_cannot_access_another_citizens_application_or_case(self):
        """Verifies strict citizen isolation."""
        app_a = submit_application(
            name="Citizen A Application",
            phone="01711111111",
            address="Dhaka",
            legal_problem="Problem A",
            incident_description="Description A",
            applicant_user=self.citizen_a
        )
        case_a = accept_application(app_a, self.officer)

        # Citizen A can access their own application and case
        self.assertTrue(can_access_application(self.citizen_a, app_a))
        self.assertTrue(can_access_case(self.citizen_a, case_a))

        # Citizen B CANNOT access Citizen A's application or case
        self.assertFalse(can_access_application(self.citizen_b, app_a))
        self.assertFalse(can_access_case(self.citizen_b, case_a))

        # DLAO Officer can access both
        self.assertTrue(can_access_application(self.officer, app_a))
        self.assertTrue(can_access_case(self.officer, case_a))

    def test_case_event_cannot_be_modified_or_deleted(self):
        """Verifies that CaseEvent is strictly append-only (cannot be modified or deleted)."""
        app = submit_application(
            name="Audit Test Applicant",
            phone="01777777777",
            address="Khulna",
            legal_problem="Guardianship",
            incident_description="Custody dispute.",
            applicant_user=self.citizen_a
        )
        case_record = accept_application(app, self.officer)
        event = CaseEvent.objects.filter(case=case_record).first()

        # Modifying existing CaseEvent must raise PermissionError
        event.description = "Tampered description attempt"
        with self.assertRaises(PermissionError):
            event.save()

        # Deleting existing CaseEvent must raise PermissionError
        with self.assertRaises(PermissionError):
            event.delete()


class Phase3CitizenAndUDCIntakeTests(TestCase):
    """
    Automated test suite for Phase 3:
    Citizen intake workflow, form validation, review step, application detail tracking,
    citizen isolation, duplicate prevention, and UDC assisted intake.
    """
    def setUp(self):
        self.client = Client()

        # Create citizen user
        self.citizen = User.objects.create_user(username='citizen_user', password='password123')
        self.citizen_profile = UserProfile.objects.create(
            user=self.citizen,
            role=UserProfile.ROLE_CITIZEN,
            phone='01811111111',
            language='bn'
        )

        # Create another citizen user for isolation check
        self.other_citizen = User.objects.create_user(username='other_citizen', password='password123')
        self.other_profile = UserProfile.objects.create(
            user=self.other_citizen,
            role=UserProfile.ROLE_CITIZEN,
            phone='01822222222',
            language='en'
        )

        # Create UDC operator user
        self.udc_operator = User.objects.create_user(username='udc_operator_user', password='password123')
        self.udc_profile = UserProfile.objects.create(
            user=self.udc_operator,
            role=UserProfile.ROLE_UDC_OPERATOR,
            phone='01833333333',
            language='bn'
        )

    def test_citizen_can_open_new_application_and_form_renders(self):
        """Verifies that authenticated citizen can open New Application page and form renders."""
        self.client.force_login(self.citizen)
        response = self.client.get(reverse('cases:application_create'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Legal Aid Intake Application")
        self.assertContains(response, 'name="name"')
        self.assertContains(response, 'name="phone"')
        self.assertContains(response, 'name="address"')
        self.assertContains(response, 'name="legal_problem"')
        self.assertContains(response, 'name="incident_description"')
        self.assertContains(response, 'name="preferred_channel"')
        self.assertContains(response, 'name="safe_contact_number"')
        self.assertContains(response, 'name="safe_contact_time"')
        self.assertContains(response, 'name="language"')

    def test_application_review_step_works(self):
        """Verifies that submitting with action='review' shows the review verification screen."""
        self.client.force_login(self.citizen)
        payload = {
            'action': 'review',
            'name': 'Kamrul Hasan',
            'phone': '01811111111',
            'address': 'Dohar, Dhaka',
            'legal_problem': 'Arbitrary workplace termination',
            'incident_description': 'Terminated from textile mill without legal notice or due compensation.',
            'preferred_channel': 'web',
            'safe_contact_number': '01811111111',
            'safe_contact_time': 'Evening 5pm-7pm',
            'language': 'bn',
        }
        response = self.client.post(reverse('cases:application_create'), payload)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Review Your Legal Aid Application")
        self.assertContains(response, "Arbitrary workplace termination")
        self.assertContains(response, "Confirm & Submit Application")
        # Ensure Application was NOT created during review
        self.assertEqual(Application.objects.filter(name='Kamrul Hasan').count(), 0)

    def test_valid_application_can_be_submitted_generating_app_id_and_not_case_id(self):
        """Verifies successful application submission creates Application ID, CaseEvent, and NO Case ID."""
        self.client.force_login(self.citizen)
        payload = {
            'action': 'submit',
            'name': 'Anowara Begum',
            'phone': '01812345678',
            'address': 'Sreepur, Gazipur',
            'legal_problem': 'Inheritance land share denied',
            'incident_description': 'Brothers denied lawful inheritance share of paternal agricultural land.',
            'preferred_channel': 'web',
            'safe_contact_number': '',
            'safe_contact_time': 'Morning',
            'language': 'bn',
        }
        response = self.client.post(reverse('cases:application_create'), payload)
        self.assertEqual(response.status_code, 302)

        # Verify application in database
        app = Application.objects.get(name='Anowara Begum')
        self.assertTrue(app.application_id.startswith('APP-'))
        self.assertEqual(app.status, Application.STATUS_SUBMITTED)
        self.assertEqual(app.applicant_user, self.citizen)

        # STRICT PRD REQUIREMENT: CaseRecord and Case ID must NOT exist
        self.assertFalse(hasattr(app, 'case_record'))
        self.assertEqual(CaseRecord.objects.filter(application=app).count(), 0)

        # Verify CaseEvent was logged for intake
        event = CaseEvent.objects.filter(application=app).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.action, 'APPLICATION_SUBMITTED')
        self.assertEqual(event.provenance, CaseEvent.PROVENANCE_APPLICANT_CONFIRMED)
        self.assertIsNone(event.case)

        # Verify redirected to application detail tracking
        self.assertRedirects(response, reverse('cases:application_detail', args=[app.application_id]))

    def test_invalid_form_cannot_be_submitted(self):
        """Verifies that invalid form (e.g. missing required fields or invalid phone) fails server-side."""
        self.client.force_login(self.citizen)
        payload = {
            'action': 'submit',
            'name': 'A',  # too short
            'phone': '123',  # invalid phone
            'address': '',
            'legal_problem': '',
            'incident_description': '',
            'preferred_channel': 'web',
            'language': 'bn',
        }
        response = self.client.post(reverse('cases:application_create'), payload)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please correct the following errors")
        self.assertEqual(Application.objects.count(), 0)

    def test_citizen_sees_submitted_application_on_dashboard(self):
        """Verifies submitted application is listed on citizen dashboard."""
        self.client.force_login(self.citizen)
        app = submit_application(
            name="Citizen Self",
            phone="01811111111",
            address="Tejgaon, Dhaka",
            legal_problem="Rental eviction notice",
            incident_description="Landlord served illegal 24hr eviction notice.",
            applicant_user=self.citizen
        )
        response = self.client.get(reverse('dashboard:citizen'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, app.application_id)
        self.assertContains(response, "Rental eviction notice")
        self.assertContains(response, "Track / Details")

    def test_citizen_cannot_access_another_citizens_application_detail(self):
        """Verifies citizen isolation: citizen B cannot view citizen A's application detail."""
        app_a = submit_application(
            name="Confidential Applicant A",
            phone="01811111111",
            address="Dhaka",
            legal_problem="Family matter",
            incident_description="Confidential incident description.",
            applicant_user=self.citizen
        )

        # Citizen A can access
        self.client.force_login(self.citizen)
        response = self.client.get(reverse('cases:application_detail', args=[app_a.application_id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, app_a.application_id)
        self.assertContains(response, "Confidential Applicant A")

        # Citizen B CANNOT access (403 Forbidden)
        self.client.force_login(self.other_citizen)
        response = self.client.get(reverse('cases:application_detail', args=[app_a.application_id]))
        self.assertEqual(response.status_code, 403)

    def test_udc_consent_and_read_back_required(self):
        """Verifies that UDC intake strictly requires citizen consent and read-back checkboxes."""
        self.client.force_login(self.udc_operator)
        payload = {
            'action': 'submit',
            'name': 'Rural Applicant',
            'phone': '01899999999',
            'address': 'Charfasson, Bhola',
            'legal_problem': 'Land grabbing',
            'incident_description': 'Farmland grabbed by local extortionists.',
            'preferred_channel': 'udc',
            'language': 'bn',
            # citizen_consent and review_read_back missing!
        }
        response = self.client.post(reverse('cases:udc_intake'), payload)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Consent")
        self.assertEqual(Application.objects.count(), 0)

    def test_udc_submission_creates_application_and_ends_operator_access(self):
        """
        Verifies that UDC assisted intake creates an application with:
        - channel = UDC
        - applicant_user is None (operator does not own the case)
        - CaseEvent with provenance = intermediary_translated
        - operator redirected to confirmation page ending active session.
        """
        self.client.force_login(self.udc_operator)
        payload = {
            'action': 'submit',
            'name': 'Monowara Khatun',
            'phone': '01855555555',
            'address': 'Bhedarganj, Shariatpur',
            'legal_problem': 'Dowry demand and domestic dispute',
            'incident_description': 'Husband and in-laws demanded 200,000 BDT dowry and physically assaulted.',
            'preferred_channel': 'udc',
            'safe_contact_number': '01855555555',
            'safe_contact_time': 'Morning',
            'language': 'bn',
            'citizen_consent': 'on',
            'review_read_back': 'on',
        }
        response = self.client.post(reverse('cases:udc_intake'), payload)
        self.assertEqual(response.status_code, 302)

        app = Application.objects.get(name='Monowara Khatun')
        self.assertTrue(app.application_id.startswith('APP-'))
        self.assertEqual(app.preferred_channel, Application.CHANNEL_UDC)
        # Operator does NOT own the case
        self.assertIsNone(app.applicant_user)
        # CaseRecord is NOT created
        self.assertFalse(hasattr(app, 'case_record'))

        # CaseEvent created with intermediary_translated provenance
        event = CaseEvent.objects.filter(application=app).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.provenance, CaseEvent.PROVENANCE_INTERMEDIARY_TRANSLATED)
        self.assertEqual(event.actor, self.udc_operator)
        self.assertEqual(event.actor_role, UserProfile.ROLE_UDC_OPERATOR)

        # Redirected to UDC confirmation
        self.assertRedirects(response, reverse('cases:udc_confirmation', args=[app.application_id]))

        # Confirmation screen displays Application ID and session termination notice
        conf_response = self.client.get(reverse('cases:udc_confirmation', args=[app.application_id]))
        self.assertEqual(conf_response.status_code, 200)
        self.assertContains(conf_response, app.application_id)
        self.assertContains(conf_response, "UDC Session Access Terminated")

    def test_non_udc_user_cannot_access_udc_intake(self):
        """Verifies that non-UDC users (e.g. standard citizens) cannot access UDC assisted intake."""
        self.client.force_login(self.citizen)
        response = self.client.get(reverse('cases:udc_intake'))
        self.assertEqual(response.status_code, 403)


class Phase4WorkflowsTests(TestCase):
    """
    Automated test suite for Phase 4:
    DLAO Officer review, application accept/reject, Case ID generation, priority change,
    panel lawyer assignment and worklist, proceeding updates, lawyer reassignment requests,
    inter-agency referrals (acknowledgement, return count escalation, completion),
    mediation scheduling and human outcome recording, and server-side security.
    """
    def setUp(self):
        self.client = Client()

        # DLAO Officer
        self.officer = User.objects.create_user(username='dlao_officer_p4', password='password123')
        self.officer_profile = UserProfile.objects.create(
            user=self.officer,
            role=UserProfile.ROLE_DLAO_OFFICER,
            phone='01700000001',
            language='en'
        )

        # Citizen
        self.citizen = User.objects.create_user(username='citizen_p4', password='password123')
        self.citizen_profile = UserProfile.objects.create(
            user=self.citizen,
            role=UserProfile.ROLE_CITIZEN,
            phone='01700000002',
            language='bn'
        )

        # Panel Lawyer 1
        self.lawyer1 = User.objects.create_user(username='panel_lawyer_1', password='password123')
        self.lawyer1_profile = UserProfile.objects.create(
            user=self.lawyer1,
            role=UserProfile.ROLE_PANEL_LAWYER,
            phone='01700000003',
            language='en'
        )

        # Panel Lawyer 2 (for unauthorized access testing)
        self.lawyer2 = User.objects.create_user(username='panel_lawyer_2', password='password123')
        self.lawyer2_profile = UserProfile.objects.create(
            user=self.lawyer2,
            role=UserProfile.ROLE_PANEL_LAWYER,
            phone='01700000004',
            language='en'
        )

        # Mediator
        self.mediator = User.objects.create_user(username='mediator_dhaka', password='password123')
        self.mediator_profile = UserProfile.objects.create(
            user=self.mediator,
            role=UserProfile.ROLE_MEDIATOR,
            phone='01700000005',
            language='en'
        )

        # Unrelated Mediator
        self.other_mediator = User.objects.create_user(username='mediator_chittagong', password='password123')
        self.other_mediator_profile = UserProfile.objects.create(
            user=self.other_mediator,
            role=UserProfile.ROLE_MEDIATOR,
            phone='01700000006',
            language='en'
        )

    def _create_submitted_app(self, name="Test Applicant"):
        return submit_application(
            name=name,
            phone="01711223344",
            address="Mirpur 10, Dhaka",
            legal_problem="Wrongful eviction from residential lease",
            incident_description="Landlord served verbal notice without judicial sanction.",
            applicant_user=self.citizen,
            safe_contact_number="01711223344",
            safe_contact_time="Evening 6pm",
            language="bn"
        )

    def test_officer_sees_submitted_application_on_dashboard(self):
        """Verifies DLAO officer sees submitted applications in the New/Pending intake queue."""
        app = self._create_submitted_app("Applicant For Officer")
        self.client.force_login(self.officer)
        response = self.client.get(reverse('dashboard:officer'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, app.application_id)
        self.assertContains(response, "Applicant For Officer")
        self.assertContains(response, "Review & Decide")

    def test_officer_can_accept_application_and_case_id_generated_only_after_acceptance(self):
        """
        Verifies workflow:
        Application -> DLAO Officer reviews -> Accept -> transaction.atomic() ->
        Create CaseRecord -> Generate Case ID -> Update status -> Create CaseEvent -> Commit.
        Case ID must ONLY be generated at this point.
        """
        app = self._create_submitted_app("Acceptance Candidate")
        self.assertFalse(hasattr(app, 'case_record'))

        self.client.force_login(self.officer)
        url = reverse('cases:officer_application_detail', args=[app.application_id])
        response = self.client.post(url, {
            'action': 'accept',
            'priority': CaseRecord.PRIORITY_HIGH,
        })
        self.assertEqual(response.status_code, 302)

        app.refresh_from_db()
        self.assertEqual(app.status, Application.STATUS_ACCEPTED)
        self.assertTrue(hasattr(app, 'case_record'))
        case_record = app.case_record
        self.assertTrue(case_record.case_id.startswith('CASE-'))
        self.assertEqual(case_record.assigned_officer, self.officer)
        self.assertEqual(case_record.priority, CaseRecord.PRIORITY_HIGH)
        self.assertEqual(case_record.status, CaseRecord.STATUS_ACCEPTED)

        # Verify CaseEvent created
        event = CaseEvent.objects.filter(case=case_record, action='APPLICATION_ACCEPTED').first()
        self.assertIsNotNone(event)
        self.assertEqual(event.actor, self.officer)
        self.assertEqual(event.actor_role, UserProfile.ROLE_DLAO_OFFICER)
        self.assertEqual(event.authority, 'DLAO Officer')

    def test_cannot_accept_already_accepted_application(self):
        """Verifies that an already accepted application cannot be accepted again."""
        app = self._create_submitted_app("Double Accept Candidate")
        accept_application(app, self.officer)
        with self.assertRaises(ValidationError):
            accept_application(app, self.officer)

    def test_officer_can_reject_application_without_creating_case_record(self):
        """
        Verifies workflow:
        Application -> Reject -> reason recorded -> CaseEvent created -> no CaseRecord created.
        """
        app = self._create_submitted_app("Rejection Candidate")
        self.client.force_login(self.officer)
        url = reverse('cases:officer_application_detail', args=[app.application_id])
        response = self.client.post(url, {
            'action': 'reject',
            'reason': 'Income exceeds legal aid eligibility ceiling',
        })
        self.assertEqual(response.status_code, 302)

        app.refresh_from_db()
        self.assertEqual(app.status, Application.STATUS_REJECTED)
        self.assertFalse(hasattr(app, 'case_record'))
        self.assertEqual(CaseRecord.objects.filter(application=app).count(), 0)

        # Verify CaseEvent was created with rejection reason
        event = CaseEvent.objects.filter(application=app, action='APPLICATION_REJECTED').first()
        self.assertIsNotNone(event)
        self.assertEqual(event.actor, self.officer)
        self.assertIn("Income exceeds legal aid eligibility ceiling", event.description)

    def test_cannot_reject_already_rejected_or_accepted_application(self):
        """Verifies rejected or accepted application cannot be rejected again."""
        app = self._create_submitted_app("State Guard Candidate")
        reject_application(app, self.officer, reason="Initial reject")
        with self.assertRaises(ValidationError):
            reject_application(app, self.officer, reason="Second reject")

    def test_priority_change_creates_case_event(self):
        """Verifies authorized DLAO officer changes priority with atomic service and CaseEvent."""
        app = self._create_submitted_app("Priority Candidate")
        case = accept_application(app, self.officer, priority=CaseRecord.PRIORITY_MEDIUM)

        self.client.force_login(self.officer)
        url = reverse('cases:officer_case_detail', args=[case.case_id])
        response = self.client.post(url, {
            'action': 'change_priority',
            'priority': CaseRecord.PRIORITY_URGENT,
        })
        self.assertEqual(response.status_code, 302)

        case.refresh_from_db()
        self.assertEqual(case.priority, CaseRecord.PRIORITY_URGENT)

        event = CaseEvent.objects.filter(case=case, action='PRIORITY_CHANGED').first()
        self.assertIsNotNone(event)
        self.assertEqual(event.actor, self.officer)
        self.assertIn(CaseRecord.PRIORITY_URGENT, event.description)

    def test_lawyer_assignment_and_worklist(self):
        """
        Verifies DLAO officer assigns panel lawyer:
        - LawyerAssignment created with status pending
        - CaseRecord.assigned_lawyer set
        - CaseEvent created
        - Lawyer sees case in worklist
        """
        app = self._create_submitted_app("Lawyer Assign Candidate")
        case = accept_application(app, self.officer)

        self.client.force_login(self.officer)
        url = reverse('cases:officer_case_detail', args=[case.case_id])
        response = self.client.post(url, {
            'action': 'assign_lawyer',
            'lawyer_id': self.lawyer1.id,
        })
        self.assertEqual(response.status_code, 302)

        case.refresh_from_db()
        self.assertEqual(case.assigned_lawyer, self.lawyer1)

        assignment = LawyerAssignment.objects.filter(case=case, lawyer=self.lawyer1).first()
        self.assertIsNotNone(assignment)
        self.assertEqual(assignment.status, LawyerAssignment.STATUS_PENDING)

        event = CaseEvent.objects.filter(case=case, action='LAWYER_ASSIGNED').first()
        self.assertIsNotNone(event)

        # Panel Lawyer checks worklist
        self.client.force_login(self.lawyer1)
        resp = self.client.get(reverse('dashboard:panel_lawyer'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, case.case_id)
        self.assertContains(resp, "Pending Case Assignments")

    def test_lawyer_can_accept_assignment(self):
        """Verifies lawyer can accept assigned case and it moves to active cases."""
        app = self._create_submitted_app("Lawyer Accept Candidate")
        case = accept_application(app, self.officer)
        assignment = assign_lawyer_to_case(case, self.officer, self.lawyer1)

        self.client.force_login(self.lawyer1)
        url = reverse('lawyers:respond_assignment', args=[assignment.id])
        response = self.client.post(url, {'decision': 'accept'})
        self.assertEqual(response.status_code, 302)

        assignment.refresh_from_db()
        self.assertEqual(assignment.status, LawyerAssignment.STATUS_ACCEPTED)

        event = CaseEvent.objects.filter(case=case, action='LAWYER_ASSIGNMENT_ACCEPTED').first()
        self.assertIsNotNone(event)
        self.assertEqual(event.actor, self.lawyer1)

    def test_lawyer_can_decline_assignment(self):
        """Verifies lawyer can decline assigned case, recording reason and clearing assignment."""
        app = self._create_submitted_app("Lawyer Decline Candidate")
        case = accept_application(app, self.officer)
        assignment = assign_lawyer_to_case(case, self.officer, self.lawyer1)

        self.client.force_login(self.lawyer1)
        url = reverse('lawyers:respond_assignment', args=[assignment.id])
        response = self.client.post(url, {
            'decision': 'decline',
            'reason': 'Conflict of interest with opposing party',
        })
        self.assertEqual(response.status_code, 302)

        assignment.refresh_from_db()
        self.assertEqual(assignment.status, LawyerAssignment.STATUS_DECLINED)
        case.refresh_from_db()
        self.assertIsNone(case.assigned_lawyer)

        event = CaseEvent.objects.filter(case=case, action='LAWYER_ASSIGNMENT_DECLINED').first()
        self.assertIsNotNone(event)
        self.assertIn("Conflict of interest", event.description)

    def test_unauthorized_lawyer_cannot_access_another_lawyers_case(self):
        """Verifies lawyer isolation: lawyer 2 cannot view lawyer 1's assigned case detail."""
        app = self._create_submitted_app("Isolated Case")
        case = accept_application(app, self.officer)
        assign_lawyer_to_case(case, self.officer, self.lawyer1)

        self.client.force_login(self.lawyer2)
        url = reverse('lawyers:case_detail', args=[case.case_id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_lawyer_case_update_creates_case_event(self):
        """Verifies assigned lawyer can add proceeding updates and creates CaseEvent."""
        app = self._create_submitted_app("Update Candidate")
        case = accept_application(app, self.officer)
        assignment = assign_lawyer_to_case(case, self.officer, self.lawyer1)
        accept_lawyer_assignment(assignment, self.lawyer1)

        self.client.force_login(self.lawyer1)
        url = reverse('lawyers:case_detail', args=[case.case_id])
        response = self.client.post(url, {
            'action': 'add_update',
            'notes': 'Written statement filed in District Court. Next hearing 20 November.',
        })
        self.assertEqual(response.status_code, 302)

        event = CaseEvent.objects.filter(case=case, action='LAWYER_CASE_UPDATE').first()
        self.assertIsNotNone(event)
        self.assertEqual(event.actor, self.lawyer1)
        self.assertIn("Written statement filed", event.description)

    def test_lawyer_request_change_workflow(self):
        """Verifies lawyer reassignment request generates high-priority Task and CaseEvent."""
        app = self._create_submitted_app("Change Request Candidate")
        case = accept_application(app, self.officer)
        assignment = assign_lawyer_to_case(case, self.officer, self.lawyer1)
        accept_lawyer_assignment(assignment, self.lawyer1)

        self.client.force_login(self.lawyer1)
        url = reverse('lawyers:case_detail', args=[case.case_id])
        response = self.client.post(url, {
            'action': 'request_change',
            'reason': 'Medical emergency requires extended leave',
        })
        self.assertEqual(response.status_code, 302)

        task = Task.objects.filter(case=case, priority='HIGH').first()
        self.assertIsNotNone(task)
        self.assertIn("Review Lawyer Change Request", task.title)
        self.assertIn("Medical emergency", task.description)

        event = CaseEvent.objects.filter(case=case, action='LAWYER_CHANGE_REQUESTED').first()
        self.assertIsNotNone(event)

    def test_referral_creation_and_acknowledgement(self):
        """Verifies referral creation, CaseRecord status transition to REFERRED, and acknowledgement."""
        app = self._create_submitted_app("Referral Candidate")
        case = accept_application(app, self.officer)

        self.client.force_login(self.officer)
        url = reverse('cases:officer_case_detail', args=[case.case_id])
        response = self.client.post(url, {
            'action': 'create_referral',
            'destination': 'DLAO Narayanganj',
            'reason': 'Jurisdiction transfer per applicant residential relocation',
            'expected_action': 'Intake and legal representation',
            'deadline': '2026-11-01',
        })
        self.assertEqual(response.status_code, 302)

        referral = Referral.objects.filter(case=case).first()
        self.assertIsNotNone(referral)
        self.assertEqual(referral.destination, 'DLAO Narayanganj')
        self.assertEqual(referral.status, Referral.STATUS_PENDING)

        case.refresh_from_db()
        self.assertEqual(case.status, CaseRecord.STATUS_REFERRED)

        # Acknowledge referral
        ref_url = reverse('referrals:referral_detail', args=[referral.id])
        resp = self.client.post(ref_url, {'action': 'acknowledge'})
        self.assertEqual(resp.status_code, 302)

        referral.refresh_from_db()
        self.assertEqual(referral.status, Referral.STATUS_ACKNOWLEDGED)
        self.assertIsNotNone(referral.acknowledged_at)

        event = CaseEvent.objects.filter(case=case, action='REFERRAL_ACKNOWLEDGED').first()
        self.assertIsNotNone(event)

    def test_referral_return_increments_return_count_and_escalates_on_repeated_returns(self):
        """
        Verifies referral return workflow:
        Return 1: returned_count = 1, status = returned.
        Return 2: returned_count = 2, status = escalated, High-priority escalation Task generated.
        """
        app = self._create_submitted_app("Return Referral Candidate")
        case = accept_application(app, self.officer)
        referral = create_case_referral(
            case, self.officer,
            destination="Labour Court 1",
            reason="Workplace termination",
            expected_action="Adjudication",
            deadline="2026-11-15"
        )

        self.client.force_login(self.officer)
        ref_url = reverse('referrals:referral_detail', args=[referral.id])

        # Return 1
        self.client.post(ref_url, {'action': 'return', 'reason': 'Missing termination letter copy'})
        referral.refresh_from_db()
        self.assertEqual(referral.returned_count, 1)
        self.assertEqual(referral.status, Referral.STATUS_RETURNED)

        # Return 2 -> Escalation
        self.client.post(ref_url, {'action': 'return', 'reason': 'Court jurisdiction dispute'})
        referral.refresh_from_db()
        self.assertEqual(referral.returned_count, 2)
        self.assertEqual(referral.status, Referral.STATUS_ESCALATED)

        # Escalation task created
        task = Task.objects.filter(case=case, priority='HIGH').first()
        self.assertIsNotNone(task)
        self.assertIn("ESCALATION", task.title)

        event = CaseEvent.objects.filter(case=case, action='REFERRAL_RETURNED').order_by('-created_at').first()
        self.assertIsNotNone(event)
        self.assertIn("Count: 2", event.description)

    def test_referral_completion(self):
        """Verifies referral completion and CaseEvent logging."""
        app = self._create_submitted_app("Complete Referral Candidate")
        case = accept_application(app, self.officer)
        referral = create_case_referral(
            case, self.officer,
            destination="Family Welfare Directorate",
            reason="Livelihood grant support",
            expected_action="Disbursement",
            deadline="2026-11-20"
        )

        self.client.force_login(self.officer)
        ref_url = reverse('referrals:referral_detail', args=[referral.id])
        self.client.post(ref_url, {
            'action': 'complete',
            'outcome': 'Support grant disbursed successfully.',
        })

        referral.refresh_from_db()
        self.assertEqual(referral.status, Referral.STATUS_COMPLETED)

        event = CaseEvent.objects.filter(case=case, action='REFERRAL_COMPLETED').first()
        self.assertIsNotNone(event)

    def test_mediation_creation_and_outcome_recording(self):
        """
        Verifies mediation workflow:
        - Officer initiates mediation -> status = MEDIATION on CaseRecord
        - Mediator conducts session and records human outcome
        - If agreed, CaseRecord transitions to RESOLVED
        - CaseEvents logged at each consequential stage
        """
        app = self._create_submitted_app("Mediation Candidate")
        case = accept_application(app, self.officer)

        self.client.force_login(self.officer)
        url = reverse('cases:officer_case_detail', args=[case.case_id])
        response = self.client.post(url, {
            'action': 'initiate_mediation',
            'mediator_id': self.mediator.id,
            'mode': 'hybrid',
            'scheduled_at': '2026-10-25T14:30',
        })
        self.assertEqual(response.status_code, 302)

        case.refresh_from_db()
        self.assertEqual(case.status, CaseRecord.STATUS_MEDIATION)
        mediation = case.mediation
        self.assertEqual(mediation.mediator, self.mediator)
        self.assertEqual(mediation.mode, 'hybrid')

        event = CaseEvent.objects.filter(case=case, action='MEDIATION_INITIATED').first()
        self.assertIsNotNone(event)

        # Mediator records outcome
        self.client.force_login(self.mediator)
        med_url = reverse('mediation:mediation_detail', args=[mediation.id])
        resp = self.client.post(med_url, {
            'action': 'record_outcome',
            'attendance_status': 'both_present',
            'status': Mediation.STATUS_AGREED,
            'outcome': 'Parties executed formal written accord on property boundary.',
        })
        self.assertEqual(resp.status_code, 302)

        mediation.refresh_from_db()
        self.assertEqual(mediation.status, Mediation.STATUS_AGREED)
        self.assertIsNotNone(mediation.completed_at)

        case.refresh_from_db()
        self.assertEqual(case.status, CaseRecord.STATUS_RESOLVED)

        outcome_event = CaseEvent.objects.filter(case=case, action='MEDIATION_OUTCOME_RECORDED').first()
        self.assertIsNotNone(outcome_event)
        self.assertEqual(outcome_event.actor, self.mediator)
        self.assertEqual(outcome_event.authority, 'Mediator')

    def test_unauthorized_users_cannot_perform_protected_actions(self):
        """
        Comprehensive security verification:
        - Citizen cannot access officer actions
        - Citizen cannot accept or reject applications
        - Citizen cannot initiate mediation or refer cases
        - Mediator cannot access another mediator's session
        - Lawyer cannot transition case status
        """
        app = self._create_submitted_app("Security Candidate")
        case = accept_application(app, self.officer)
        mediation = initiate_case_mediation(case, self.officer, self.mediator)

        # Citizen attempts officer review
        self.client.force_login(self.citizen)
        resp1 = self.client.get(reverse('cases:officer_application_detail', args=[app.application_id]))
        self.assertEqual(resp1.status_code, 403)

        # Citizen attempts to accept application
        resp2 = self.client.post(reverse('cases:officer_application_detail', args=[app.application_id]), {'action': 'accept'})
        self.assertEqual(resp2.status_code, 403)

        # Citizen attempts to access officer case workspace
        resp3 = self.client.get(reverse('cases:officer_case_detail', args=[case.case_id]))
        self.assertEqual(resp3.status_code, 403)

        # Unrelated mediator attempts to access mediation session
        self.client.force_login(self.other_mediator)
        med_url = reverse('mediation:mediation_detail', args=[mediation.id])
        resp4 = self.client.get(med_url)
        self.assertEqual(resp4.status_code, 403)

        # Panel lawyer attempts to transition case status
        self.client.force_login(self.lawyer1)
        resp5 = self.client.post(reverse('cases:officer_case_detail', args=[case.case_id]), {
            'action': 'transition_status',
            'status': CaseRecord.STATUS_CLOSED,
            'reason': 'Attempted bypass',
        })
        self.assertEqual(resp5.status_code, 403)

    def test_controlled_case_status_transitions(self):
        """Verifies only valid statuses are permitted and CaseEvent is logged."""
        app = self._create_submitted_app("Status Transition Candidate")
        case = accept_application(app, self.officer)

        # Invalid status must fail
        with self.assertRaises(ValidationError):
            transition_case_status(case, self.officer, "INVALID_STATE", reason="Bad transition")

        # Valid transition: IN_PROGRESS
        transition_case_status(case, self.officer, CaseRecord.STATUS_IN_PROGRESS, reason="Assigned to litigation")
        case.refresh_from_db()
        self.assertEqual(case.status, CaseRecord.STATUS_IN_PROGRESS)

        event = CaseEvent.objects.filter(case=case, action='CASE_STATUS_CHANGED').first()
        self.assertIsNotNone(event)
        self.assertEqual(event.actor, self.officer)
        self.assertIn(CaseRecord.STATUS_IN_PROGRESS, event.description)


class Phase5DocumentsAndAITests(TestCase):
    """
    Automated test suite for Phase 5:
    Document upload, verification, secure download, related cases linking,
    duplicate candidate generation, human review, MockAIService operations,
    and AI safety restrictions.
    """
    def setUp(self):
        self.client = Client()

        # DLAO Officer
        self.officer = User.objects.create_user(username='dlao_officer_p5', password='password123')
        self.officer_profile = UserProfile.objects.create(
            user=self.officer,
            role=UserProfile.ROLE_DLAO_OFFICER,
            phone='01710000001',
            language='en'
        )

        # Citizen 1 (Case 1 Owner)
        self.citizen1 = User.objects.create_user(username='citizen1_p5', password='password123')
        self.citizen1_profile = UserProfile.objects.create(
            user=self.citizen1,
            role=UserProfile.ROLE_CITIZEN,
            phone='01710000002',
            language='bn'
        )

        # Citizen 2 (Case 2 Owner / Unauthorized for Case 1)
        self.citizen2 = User.objects.create_user(username='citizen2_p5', password='password123')
        self.citizen2_profile = UserProfile.objects.create(
            user=self.citizen2,
            role=UserProfile.ROLE_CITIZEN,
            phone='01710000003',
            language='en'
        )

        # Panel Lawyer
        self.lawyer = User.objects.create_user(username='lawyer_p5', password='password123')
        self.lawyer_profile = UserProfile.objects.create(
            user=self.lawyer,
            role=UserProfile.ROLE_PANEL_LAWYER,
            phone='01710000004',
            language='en'
        )

        # Create Case 1 (for Citizen 1)
        self.app1 = submit_application(
            name="Shahidul Alam",
            phone="01710000002",
            address="Mirpur, Dhaka",
            legal_problem="Unlawful eviction notice without due court decree",
            incident_description="Landlord served arbitrary 24hr eviction notice on shop premises.",
            applicant_user=self.citizen1,
            language="bn"
        )
        self.case1 = accept_application(self.app1, self.officer)

        # Create Case 2 (for Citizen 2)
        self.app2 = submit_application(
            name="Shahidul Alam",  # Matching name for duplicate scan test
            phone="01710000002",  # Matching phone for duplicate scan test
            address="Mirpur Section 10, Dhaka",
            legal_problem="Commercial shop partition and rent dispute",
            incident_description="Dispute over possession and boundary of commercial shop lot.",
            applicant_user=self.citizen2,
            language="en"
        )
        self.case2 = accept_application(self.app2, self.officer)

    def test_authorized_document_upload_and_ai_preview(self):
        """Verifies authorized DLAO officer uploads document with AI summary preview and CaseEvent."""
        file_content = b"%PDF-1.4 Simulated eviction notice content for testing"
        uploaded_file = SimpleUploadedFile("eviction_notice.pdf", file_content, content_type="application/pdf")

        doc = upload_case_document(
            case_record=self.case1,
            user=self.officer,
            title="Landlord Eviction Notice",
            file_obj=uploaded_file,
            description="Notice dated 10 October served by commercial landlord.",
        )

        self.assertIsNotNone(doc.id)
        self.assertEqual(doc.title, "Landlord Eviction Notice")
        self.assertEqual(doc.status, Document.STATUS_UPLOADED)
        self.assertIsNotNone(doc.ai_summary)
        self.assertIn("Simulated", doc.ai_summary)
        self.assertGreaterEqual(doc.ai_confidence, 0.70)

        # Verify CaseEvent created
        event = CaseEvent.objects.filter(case=self.case1, action='DOCUMENT_UPLOADED').first()
        self.assertIsNotNone(event)
        self.assertEqual(event.actor, self.officer)
        self.assertIn("Landlord Eviction Notice", event.description)

    def test_citizen_can_upload_document_to_own_case(self):
        """Verifies citizen can upload documents to their own accepted case."""
        self.client.force_login(self.citizen1)
        file_content = b"Simulated rent payment receipt"
        uploaded_file = SimpleUploadedFile("rent_receipt.jpg", file_content, content_type="image/jpeg")

        url = reverse('cases:application_detail', args=[self.app1.application_id])
        response = self.client.post(url, {
            'action': 'upload_document',
            'title': 'Rent Receipt October 2026',
            'file': uploaded_file,
            'description': 'Proof of timely bank deposit',
        })
        self.assertEqual(response.status_code, 302)

        doc = Document.objects.filter(case=self.case1, title='Rent Receipt October 2026').first()
        self.assertIsNotNone(doc)
        self.assertEqual(doc.uploaded_by, self.citizen1)

        event = CaseEvent.objects.filter(case=self.case1, action='DOCUMENT_UPLOADED').order_by('-created_at').first()
        self.assertIsNotNone(event)
        self.assertEqual(event.provenance, CaseEvent.PROVENANCE_APPLICANT_CONFIRMED)

    def test_unauthorized_document_upload_denied(self):
        """Verifies citizen 2 cannot upload documents to citizen 1's case."""
        file_content = b"Intrusion attempt document"
        uploaded_file = SimpleUploadedFile("intrusion.pdf", file_content, content_type="application/pdf")

        with self.assertRaises(PermissionDenied):
            upload_case_document(
                case_record=self.case1,
                user=self.citizen2,
                title="Malicious Upload",
                file_obj=uploaded_file
            )

    def test_unauthorized_document_access_denied_via_download_view(self):
        """
        Security Test:
        Verifies that citizen 2 CANNOT access or download citizen 1's document.
        Citizen 1 and DLAO Officer CAN access the document.
        """
        file_content = b"Confidential financial statement"
        uploaded_file = SimpleUploadedFile("financial.pdf", file_content, content_type="application/pdf")
        doc = upload_case_document(self.case1, self.citizen1, "Confidential Financials", uploaded_file)

        download_url = reverse('documents:document_download', args=[doc.id])

        # Citizen 2 attempt -> 403 Forbidden
        self.client.force_login(self.citizen2)
        resp_denied = self.client.get(download_url)
        self.assertEqual(resp_denied.status_code, 403)

        # Citizen 1 attempt -> 200 OK
        self.client.force_login(self.citizen1)
        resp_ok = self.client.get(download_url)
        self.assertEqual(resp_ok.status_code, 200)

        # Officer attempt -> 200 OK
        self.client.force_login(self.officer)
        resp_officer = self.client.get(download_url)
        self.assertEqual(resp_officer.status_code, 200)

    def test_document_status_verification_and_case_event(self):
        """Verifies DLAO staff updates document status to verified and logs CaseEvent."""
        file_content = b"Valid deed scan"
        uploaded_file = SimpleUploadedFile("deed.pdf", file_content, content_type="application/pdf")
        doc = upload_case_document(self.case1, self.officer, "Registered Title Deed", uploaded_file)

        verify_case_document(doc, self.officer, Document.STATUS_VERIFIED, review_notes="Original deed inspected")

        doc.refresh_from_db()
        self.assertEqual(doc.status, Document.STATUS_VERIFIED)

        event = CaseEvent.objects.filter(case=self.case1, action='DOCUMENT_STATUS_CHANGED').first()
        self.assertIsNotNone(event)
        self.assertEqual(event.actor, self.officer)
        self.assertEqual(event.authority, 'DLAO Staff')
        self.assertIn("verified", event.description)

    def test_unauthorized_user_cannot_verify_document(self):
        """Verifies citizens and lawyers cannot verify documents (staff authority only)."""
        file_content = b"Verification test scan"
        uploaded_file = SimpleUploadedFile("cert.pdf", file_content, content_type="application/pdf")
        doc = upload_case_document(self.case1, self.officer, "Birth Certificate", uploaded_file)

        with self.assertRaises(PermissionDenied):
            verify_case_document(doc, self.citizen1, Document.STATUS_VERIFIED)

        with self.assertRaises(PermissionDenied):
            verify_case_document(doc, self.lawyer, Document.STATUS_VERIFIED)

    def test_related_case_creation_and_events(self):
        """
        Verifies linking two cases as related:
        - RelatedCase created
        - Both cases remain independent (not merged)
        - CaseEvents created on both cases
        """
        link = link_related_cases(
            case_a=self.case1,
            case_b=self.case2,
            staff_user=self.officer,
            relationship_type="Counter-claim / Cross-suit"
        )
        self.assertIsNotNone(link.id)
        self.assertEqual(link.case, self.case1)
        self.assertEqual(link.related_case, self.case2)

        # Verify events on both cases
        event_a = CaseEvent.objects.filter(case=self.case1, action='CASE_LINKED_RELATED').first()
        self.assertIsNotNone(event_a)
        self.assertIn(self.case2.case_id, event_a.description)
        self.assertIn("Related cases are NOT merged", event_a.description)

        event_b = CaseEvent.objects.filter(case=self.case2, action='CASE_LINKED_RELATED').first()
        self.assertIsNotNone(event_b)
        self.assertIn(self.case1.case_id, event_b.description)

    def test_self_related_case_prevention(self):
        """Verifies that linking a case to itself is strictly rejected."""
        with self.assertRaises(ValidationError):
            link_related_cases(self.case1, self.case1, self.officer)

    def test_related_cases_remain_separate(self):
        """
        ARCHITECTURAL MANDATE:
        Related cases are NOT merged.
        Each case retains its own Case ID, status, outcome, and audit history.
        """
        link_related_cases(self.case1, self.case2, self.officer, "Cross-suit")

        # Mutate status on case1
        transition_case_status(self.case1, self.officer, CaseRecord.STATUS_IN_PROGRESS, reason="Commencing trial")

        self.case1.refresh_from_db()
        self.case2.refresh_from_db()

        self.assertEqual(self.case1.status, CaseRecord.STATUS_IN_PROGRESS)
        self.assertEqual(self.case2.status, CaseRecord.STATUS_ACCEPTED)  # Case 2 status is untouched!
        self.assertNotEqual(self.case1.case_id, self.case2.case_id)

    def test_duplicate_candidate_generation(self):
        """
        Verifies deterministic duplicate scan identifies possible match
        based on identical phone and matching name.
        """
        candidates = MockAIService.run_duplicate_scan(self.case1)
        self.assertGreaterEqual(len(candidates), 1)

        candidate = candidates[0]
        self.assertEqual(candidate.case, self.case1)
        self.assertEqual(candidate.possible_case, self.case2)
        self.assertGreaterEqual(candidate.match_score, 0.70)
        self.assertEqual(candidate.review_status, DuplicateCandidate.STATUS_PENDING)
        self.assertIn("phone", candidate.match_reason.lower())

    def test_human_duplicate_review_creates_case_event(self):
        """Verifies human staff reviews duplicate candidate and logs CaseEvent."""
        candidates = MockAIService.run_duplicate_scan(self.case1)
        candidate = candidates[0]

        review_duplicate_candidate(
            candidate=candidate,
            staff_user=self.officer,
            review_status=DuplicateCandidate.STATUS_CONFIRMED,
            review_notes="Verified as same applicant with multiple related tenancy claims."
        )

        candidate.refresh_from_db()
        self.assertEqual(candidate.review_status, DuplicateCandidate.STATUS_CONFIRMED)
        self.assertEqual(candidate.reviewed_by, self.officer)

        event = CaseEvent.objects.filter(case=self.case1, action='DUPLICATE_CANDIDATE_REVIEWED').first()
        self.assertIsNotNone(event)
        self.assertIn("Confirmed Duplicate", event.description)

    def test_duplicate_detection_never_automatically_rejects_or_merges(self):
        """
        STRICT PRD MANDATE:
        Duplicate detection is ONLY a suggestion.
        Running scan NEVER automatically rejects, merges, or closes cases.
        """
        initial_status_1 = self.case1.status
        initial_status_2 = self.case2.status

        candidates = MockAIService.run_duplicate_scan(self.case1)
        self.assertGreaterEqual(len(candidates), 1)

        self.case1.refresh_from_db()
        self.case2.refresh_from_db()

        # States must remain completely unaffected
        self.assertEqual(self.case1.status, initial_status_1)
        self.assertEqual(self.case2.status, initial_status_2)
        self.assertNotEqual(self.case1.id, self.case2.id)

    def test_mock_ai_service_returns_simulated_results_and_labeled(self):
        """Verifies MockAIService returns deterministic simulated outputs with proper labeling."""
        extraction = MockAIService.extract_information("Eviction notice on 15/10/2026. Contact 01711223344 for land boundary.")
        self.assertTrue(extraction['is_simulated'])
        self.assertEqual(extraction['label_en'], "SIMULATED AI ASSISTANCE")
        self.assertIn("15/10/2026", extraction['dates'])
        self.assertIn("01711223344", extraction['phones'])

        cat = MockAIService.categorize_case("Landlord threatened unlawful eviction from farmland plot.")
        self.assertTrue(cat['is_simulated'])
        self.assertIn("Land", cat['suggested_category'])

        missing = MockAIService.detect_missing_information(self.app1)
        self.assertTrue(missing['is_simulated'])

    def test_ai_cannot_perform_restricted_decisions(self):
        """Verifies AI safety barriers raise PermissionDenied if AI is invoked to make decisions."""
        with self.assertRaises(PermissionDenied):
            MockAIService.assert_ai_cannot_decide('reject_application')

        with self.assertRaises(PermissionDenied):
            MockAIService.assert_ai_cannot_decide('merge_cases')

        with self.assertRaises(PermissionDenied):
            MockAIService.assert_ai_cannot_decide('close_case')

        with self.assertRaises(PermissionDenied):
            MockAIService.assert_ai_cannot_decide('determine_eligibility')

    def test_file_size_and_extension_validation(self):
        """Verifies validation rejects unsupported file extensions and oversized files."""
        # Unsupported format (.exe)
        bad_file = SimpleUploadedFile("malware.exe", b"executable code", content_type="application/octet-stream")
        with self.assertRaises(ValidationError):
            upload_case_document(self.case1, self.officer, "Executable File", bad_file)

        # Oversized file (> 10MB)
        large_file = SimpleUploadedFile("huge.pdf", b"0" * (11 * 1024 * 1024), content_type="application/pdf")
        with self.assertRaises(ValidationError):
            upload_case_document(self.case1, self.officer, "Oversized Document", large_file)

    def test_officer_case_detail_shows_documents_related_cases_duplicates_and_ai(self):
        """Verifies officer workspace renders documents, related cases, duplicate detection, and AI modules."""
        # Upload a document and link related case
        file_content = b"Verified tenancy contract"
        uploaded_file = SimpleUploadedFile("tenancy.pdf", file_content, content_type="application/pdf")
        upload_case_document(self.case1, self.officer, "Tenancy Contract", uploaded_file)
        link_related_cases(self.case1, self.case2, self.officer, "Cross-suit")
        MockAIService.run_duplicate_scan(self.case1)

        self.client.force_login(self.officer)
        url = reverse('cases:officer_case_detail', args=[self.case1.case_id])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tenancy Contract")
        self.assertContains(response, "Related Cases Directory")
        self.assertContains(response, "Related cases are NOT merged")
        self.assertContains(response, "Duplicate Candidate Detection")
        self.assertContains(response, "SIMULATED AI ASSISTANCE")
        self.assertContains(response, "AI Legal Categorization Suggestion")


class Phase6SecurityAuditAndSimulatedServicesTests(TestCase):
    """
    Phase 6 Comprehensive Verification Test Suite:
    - Safe Contact / Safeguarding Rules
    - Mock External Services (SMS, IVR, USSD, NID, Payment, Signature)
    - Audit Trail Append-Only Verification
    - Transaction Safety & Atomic Rollback
    - Duplicate Submission Protection (Token Idempotency & Rapid Retry Guard)
    - Authorization & IDOR Access Controls Across All Roles
    - Complete End-to-End Workflow & UDC Intake Verification
    """

    def setUp(self):
        # Create users with role profiles
        self.citizen = User.objects.create_user(username='citizen_p6', password='password123', first_name='Amena', last_name='Begum')
        UserProfile.objects.create(user=self.citizen, role=UserProfile.ROLE_CITIZEN, phone='01711223344', language='bn')

        self.other_citizen = User.objects.create_user(username='other_citizen_p6', password='password123', first_name='Kashem', last_name='Ali')
        UserProfile.objects.create(user=self.other_citizen, role=UserProfile.ROLE_CITIZEN, phone='01811223344', language='bn')

        self.officer = User.objects.create_user(username='officer_p6', password='password123', first_name='Tariq', last_name='Officer')
        UserProfile.objects.create(user=self.officer, role=UserProfile.ROLE_DLAO_OFFICER, phone='01700000001', language='en')

        self.support_staff = User.objects.create_user(username='staff_p6', password='password123', first_name='Farhana', last_name='Staff')
        UserProfile.objects.create(user=self.support_staff, role=UserProfile.ROLE_DLAO_SUPPORT_STAFF, phone='01700000002', language='en')

        self.lawyer = User.objects.create_user(username='lawyer_p6', password='password123', first_name='Advocate', last_name='Zaman')
        UserProfile.objects.create(user=self.lawyer, role=UserProfile.ROLE_PANEL_LAWYER, phone='01700000003', language='en')

        self.other_lawyer = User.objects.create_user(username='other_lawyer_p6', password='password123', first_name='Advocate', last_name='Hasan')
        UserProfile.objects.create(user=self.other_lawyer, role=UserProfile.ROLE_PANEL_LAWYER, phone='01700000004', language='en')

        self.mediator = User.objects.create_user(username='mediator_p6', password='password123', first_name='Salma', last_name='Mediator')
        UserProfile.objects.create(user=self.mediator, role=UserProfile.ROLE_MEDIATOR, phone='01700000005', language='en')

        self.other_mediator = User.objects.create_user(username='other_mediator_p6', password='password123', first_name='Kamal', last_name='Mediator')
        UserProfile.objects.create(user=self.other_mediator, role=UserProfile.ROLE_MEDIATOR, phone='01700000006', language='en')

        self.udc_operator = User.objects.create_user(username='udc_p6', password='password123', first_name='Milon', last_name='Operator')
        UserProfile.objects.create(user=self.udc_operator, role=UserProfile.ROLE_UDC_OPERATOR, phone='01700000007', language='bn')

        self.admin = User.objects.create_superuser(username='admin_p6', password='password123', email='admin@dlas.gov.bd')
        UserProfile.objects.create(user=self.admin, role=UserProfile.ROLE_ADMIN, phone='01700000000', language='en')

        # Create intake application with safe contact details
        self.app = submit_application(
            name="Amena Begum",
            phone="01711223344",
            address="Village: Joypur, Upazila: Kaliganj, District: Gazipur",
            legal_problem="Unlawful Land Dispossession and Tenancy Threat",
            incident_description="Landlord illegally dispossessed applicant on 10/10/2026. Primary mobile monitored by opposing parties.",
            applicant_user=self.citizen,
            preferred_channel=Application.CHANNEL_WEB,
            safe_contact_number="01999887766",
            safe_contact_time="Evening 6:00 PM - 8:00 PM",
            language="bn",
            actor=self.citizen,
        )

        # Accept application to create CaseRecord
        self.case_record = accept_application(self.app, self.officer, priority=CaseRecord.PRIORITY_HIGH)

    def test_safe_contact_rules_and_safeguarding_outbound(self):
        """
        Verifies that outbound communications prioritize safe_contact_number
        and record safe_contact_used=True in Communication records.
        """
        from core.mock_services import MockSMSService, MockIVRService
        from cases.models import Communication

        # 1. SMS Dispatch respecting safe contact
        sms_res = MockSMSService.send_sms(
            case_record=self.case_record,
            message="Hearing scheduled for tomorrow.",
            actor=self.officer
        )
        self.assertTrue(sms_res['is_simulated'])
        self.assertEqual(sms_res['recipient'], "01999887766")
        self.assertTrue(sms_res['safe_contact_used'])

        # Verify communication record
        comm = Communication.objects.get(id=sms_res['communication_id'])
        self.assertEqual(comm.recipient, "01999887766")
        self.assertTrue(comm.safe_contact_used)
        self.assertEqual(comm.channel, Communication.CHANNEL_SMS)

        # 2. IVR Call respecting safe contact
        ivr_res = MockIVRService.initiate_call(
            case_record=self.case_record,
            script_summary="Proceeding Update Voice Alert",
            actor=self.officer
        )
        self.assertTrue(ivr_res['is_simulated'])
        self.assertEqual(ivr_res['recipient'], "01999887766")
        self.assertTrue(ivr_res['safe_contact_used'])
        self.assertEqual(ivr_res['permitted_hours'], "Evening 6:00 PM - 8:00 PM")

    def test_safe_contact_fallback_when_no_alternative_specified(self):
        """Verifies primary phone is used with safe_contact_used=False when no safe number provided."""
        from core.mock_services import MockSMSService

        app_no_safe = submit_application(
            name="Kashem Ali",
            phone="01811223344",
            address="Dhaka",
            legal_problem="Wage dispute",
            incident_description="Unpaid factory wages.",
            applicant_user=self.other_citizen,
            safe_contact_number="",
            safe_contact_time="",
        )
        case_no_safe = accept_application(app_no_safe, self.officer)

        sms_res = MockSMSService.send_sms(case_no_safe, "Case accepted.", actor=self.officer)
        self.assertEqual(sms_res['recipient'], "01811223344")
        self.assertFalse(sms_res['safe_contact_used'])

    def test_mock_ussd_service_simulation(self):
        """Verifies MockUSSDService simulates *16699# menu and status queries."""
        from core.mock_services import MockUSSDService

        # Initial dial
        init = MockUSSDService.process_ussd_session('sess-1', '01711223344', '*16699#')
        self.assertTrue(init['is_simulated'])
        self.assertIn("[ SIMULATED USSD *16699# ]", init['screen_text_en'])

        # Option 1: check status prompt
        prompt = MockUSSDService.process_ussd_session('sess-1', '01711223344', '1')
        self.assertTrue(prompt['is_simulated'])
        self.assertIn("Application ID", prompt['screen_text_en'])

        # Query using Application ID
        app_query = MockUSSDService.process_ussd_session('sess-1', '01711223344', self.app.application_id)
        self.assertIn(self.app.application_id, app_query['screen_text_en'])
        self.assertIn(self.case_record.case_id, app_query['screen_text_en'])

        # Query using Case ID
        case_query = MockUSSDService.process_ussd_session('sess-1', '01711223344', self.case_record.case_id)
        self.assertIn(self.case_record.case_id, case_query['screen_text_en'])
        self.assertIn(self.case_record.get_status_display(), case_query['screen_text_en'])

        # Option 2: Safe contact helpline info
        safe_info = MockUSSDService.process_ussd_session('sess-1', '01711223344', '2')
        self.assertIn("Safe Contact", safe_info['screen_text_en'])

    def test_mock_nid_service_simulation(self):
        """Verifies deterministic simulated NID verification and negative test cases."""
        from core.mock_services import MockNIDService

        # Valid 10-digit smart NID
        res_smart = MockNIDService.verify_nid("1234567890", name="Amena Begum")
        self.assertTrue(res_smart['is_simulated'])
        self.assertTrue(res_smart['is_verified'])
        self.assertEqual(res_smart['status'], 'VERIFIED')
        self.assertIn("SIM-NID-", res_smart['verification_token'])
        self.assertIn("[ SIMULATED ]", res_smart['label_en'])

        # Valid 17-digit old NID
        res_old = MockNIDService.verify_nid("19851234567890123")
        self.assertTrue(res_old['is_verified'])
        self.assertEqual(res_old['status'], 'VERIFIED')

        # Invalid NID (all zeroes)
        res_zero = MockNIDService.verify_nid("0000000000")
        self.assertFalse(res_zero['is_verified'])
        self.assertEqual(res_zero['status'], 'NOT_FOUND')

        # Invalid NID (too short)
        res_short = MockNIDService.verify_nid("12345")
        self.assertFalse(res_short['is_verified'])
        self.assertEqual(res_short['status'], 'NOT_FOUND')

    def test_mock_payment_service_simulation(self):
        """Verifies MockPaymentService legal aid fee exemption and simulated lawyer honorarium voucher."""
        from core.mock_services import MockPaymentService

        # Statutory court fee waiver check
        fee_check = MockPaymentService.check_fee_exemption(self.app)
        self.assertTrue(fee_check['is_simulated'])
        self.assertIn("100% EXEMPT", fee_check['exemption_status'])
        self.assertIn("SIMULATED PAYMENT", fee_check['label_en'])

        # Lawyer honorarium voucher disbursement
        pay_res = MockPaymentService.process_legal_aid_disbursement(
            case_record=self.case_record,
            amount=1500,
            lawyer=self.lawyer,
            purpose="Representation Honorarium"
        )
        self.assertTrue(pay_res['is_simulated'])
        self.assertIn("SIM-PAY-", pay_res['transaction_id'])
        self.assertEqual(pay_res['amount_bdt'], 1500.0)

        # Confirm immutable CaseEvent was created
        event = CaseEvent.objects.filter(case=self.case_record, action='PAYMENT_SIMULATED').last()
        self.assertIsNotNone(event)
        self.assertIn("SIMULATED PAYMENT", event.description)

    def test_mock_signature_service_workflow(self):
        """
        Verifies MockSignatureService:
        - Document hashing (SHA-256)
        - Simulated signature creation with disclaimer
        - Simulated signature verification
        - Detection of document tampering/modification
        """
        from core.mock_services import MockSignatureService
        from documents.models import Document, Signature

        # Upload document
        file_obj = SimpleUploadedFile("amicable_accord.pdf", b"Original Accord Terms", content_type="application/pdf")
        doc = upload_case_document(self.case_record, self.officer, "Amicable Accord", file_obj)

        # Create simulated signature
        sig = MockSignatureService.create_signature(doc, self.officer, notes="Signed before DLAO")
        self.assertIsNotNone(sig.id)
        self.assertEqual(sig.document_hash, MockSignatureService.hash_document(doc))
        self.assertTrue(sig.verified)

        # Verify audit trail event
        sig_event = CaseEvent.objects.filter(case=self.case_record, action='SIGNATURE_CREATED').last()
        self.assertIsNotNone(sig_event)
        self.assertIn("Signature created by", sig_event.description)
        self.assertIn("[ SIMULATED ]", sig_event.description)

        # Verify signature integrity
        ver_res = MockSignatureService.verify_signature(sig)
        self.assertTrue(ver_res['is_simulated'])
        self.assertTrue(ver_res['is_valid'])
        self.assertIn("SIMULATED ELECTRONIC SIGNATURE", ver_res['disclaimer_en'])
        self.assertIn("not a legally binding", ver_res['disclaimer_en'])

        # Simulate document modification / tampering
        sig.document_hash = "tampered_hash_value_12345"
        sig.save(update_fields=['document_hash'])

        tampered_res = MockSignatureService.verify_signature(sig)
        self.assertFalse(tampered_res['is_valid'])
        self.assertEqual(tampered_res['status'], 'HASH_MISMATCH')

    def test_case_event_append_only_integrity(self):
        """Verifies that CaseEvent records cannot be modified or deleted."""
        event = CaseEvent.objects.filter(case=self.case_record).first()
        self.assertIsNotNone(event)

        # Disallow modification
        event.description = "Tampered description"
        with self.assertRaises(PermissionError):
            event.save()

        # Disallow deletion
        with self.assertRaises(PermissionError):
            event.delete()

    def test_transaction_safety_and_atomic_rollback(self):
        """
        Verifies transaction.atomic() rollback behavior:
        If an exception occurs during application acceptance, all changes roll back.
        """
        from unittest.mock import patch

        unaccepted_app = submit_application(
            name="Rollback Test Citizen",
            phone="01799887766",
            address="Rollback Village",
            legal_problem="Rollback Problem",
            incident_description="Rollback narrative.",
            applicant_user=self.other_citizen,
        )

        initial_case_count = CaseRecord.objects.count()
        initial_event_count = CaseEvent.objects.count()

        # Mock an error right before completing accept_application
        with patch('cases.models.CaseEvent.objects.create', side_effect=RuntimeError("Database failure")):
            with self.assertRaises(RuntimeError):
                accept_application(unaccepted_app, self.officer)

        # Verify state is completely rolled back
        unaccepted_app.refresh_from_db()
        self.assertEqual(unaccepted_app.status, Application.STATUS_SUBMITTED)
        self.assertFalse(hasattr(unaccepted_app, 'case_record'))
        self.assertEqual(CaseRecord.objects.count(), initial_case_count)

    def test_duplicate_submission_protection_token_and_recent_guard(self):
        """Verifies backend protection against repeated POST / double-click / refresh."""
        self.client.force_login(self.citizen)

        # Step 1: GET form and obtain submission token
        get_resp = self.client.get(reverse('cases:application_create'))
        self.assertEqual(get_resp.status_code, 200)
        token = get_resp.context['submission_token']
        self.assertTrue(bool(token))

        post_data = {
            'action': 'submit',
            'submission_token': token,
            'name': 'Duplicate Guard Test',
            'phone': '01711998877',
            'address': 'Test Address',
            'legal_problem': 'Land Boundary',
            'incident_description': 'Test narrative.',
            'preferred_channel': 'web',
            'safe_contact_number': '',
            'safe_contact_time': '',
            'language': 'en',
        }

        # First submission succeeds
        first_resp = self.client.post(reverse('cases:application_create'), post_data)
        self.assertEqual(first_resp.status_code, 302)
        app_count = Application.objects.filter(name='Duplicate Guard Test').count()
        self.assertEqual(app_count, 1)

        # Repeated submission with identical token (e.g. browser refresh or double-click)
        second_resp = self.client.post(reverse('cases:application_create'), post_data)
        self.assertEqual(second_resp.status_code, 302)
        # Verify no second application created
        self.assertEqual(Application.objects.filter(name='Duplicate Guard Test').count(), 1)

    def test_idor_server_side_authorization(self):
        """
        Comprehensive IDOR & Role-Specific Authorization Audit:
        A user must NEVER gain access simply by changing an object ID in the URL.
        """
        # 1. Citizen A cannot access Citizen B's application
        self.client.force_login(self.other_citizen)
        resp = self.client.get(reverse('cases:application_detail', args=[self.app.application_id]))
        self.assertEqual(resp.status_code, 403)

        # 2. Citizen cannot access officer case workspace
        resp = self.client.get(reverse('cases:officer_case_detail', args=[self.case_record.case_id]))
        self.assertEqual(resp.status_code, 403)

        # 3. Citizen cannot access officer application review
        resp = self.client.get(reverse('cases:officer_application_detail', args=[self.app.application_id]))
        self.assertEqual(resp.status_code, 403)

        # 4. Lawyer cannot access unassigned case
        assign_lawyer_to_case(self.case_record, self.officer, self.lawyer)
        self.client.force_login(self.other_lawyer)
        resp = self.client.get(reverse('lawyers:case_detail', args=[self.case_record.case_id]))
        self.assertEqual(resp.status_code, 403)

        # 5. Lawyer cannot respond to another lawyer's assignment
        asgn = self.case_record.lawyer_assignments.first()
        resp = self.client.post(reverse('lawyers:respond_assignment', args=[asgn.id]), {'decision': 'accept'})
        self.assertEqual(resp.status_code, 403)

        # 6. Mediator cannot access unassigned mediation session
        med = initiate_case_mediation(self.case_record, self.officer, self.mediator)
        self.client.force_login(self.other_mediator)
        resp = self.client.get(reverse('mediation:mediation_detail', args=[med.id]))
        self.assertEqual(resp.status_code, 403)

        # 7. Citizen A cannot download Citizen B's document by manipulating document ID
        file_obj = SimpleUploadedFile("confidential.pdf", b"Confidential Citizen Data", content_type="application/pdf")
        doc = upload_case_document(self.case_record, self.officer, "Confidential Notice", file_obj)

        self.client.force_login(self.other_citizen)
        resp = self.client.get(reverse('documents:document_download', args=[doc.id]))
        self.assertEqual(resp.status_code, 403)

        # 8. UDC Operator cannot access case workspace after submission
        self.client.force_login(self.udc_operator)
        resp = self.client.get(reverse('cases:officer_case_detail', args=[self.case_record.case_id]))
        self.assertEqual(resp.status_code, 403)

    def test_end_to_end_complete_legal_aid_workflow(self):
        """
        Complete End-to-End Workflow Verification:
        Citizen (Create & Submit Application -> Application ID)
          ↓
        DLAO Officer (Review -> Accept -> Case ID generated)
          ↓
        Assign Panel Lawyer (Notification sent)
          ↓
        Lawyer Accepts & Adds Proceeding Update
          ↓
        Initiate Mediation & Record Outcome (Resolved)
          ↓
        Evidentiary Documents & Simulated Signatures
          ↓
        Related Case Linking & Duplicate Scan
          ↓
        Close Case (Verified in CaseEvent Audit History)
        """
        # Step 1: Citizen creates application
        e2e_app = submit_application(
            name="End-to-End Applicant",
            phone="01755443322",
            address="Mirpur 10, Dhaka",
            legal_problem="Unlawful eviction from retail shop",
            incident_description="Landlord locked shop without legal notice on 01/10/2026.",
            applicant_user=self.citizen,
            preferred_channel=Application.CHANNEL_WEB,
            safe_contact_number="01855443322",
            safe_contact_time="Morning 10am-12pm",
            language="bn",
        )
        self.assertTrue(e2e_app.application_id.startswith("APP-"))
        self.assertFalse(hasattr(e2e_app, 'case_record'))

        # Step 2: DLAO Officer reviews & accepts
        e2e_case = accept_application(e2e_app, self.officer, priority=CaseRecord.PRIORITY_HIGH)
        self.assertTrue(e2e_case.case_id.startswith("CASE-"))
        self.assertEqual(e2e_case.status, CaseRecord.STATUS_ACCEPTED)

        # Step 3: Officer assigns panel lawyer
        asgn = assign_lawyer_to_case(e2e_case, self.officer, self.lawyer)
        self.assertEqual(asgn.status, LawyerAssignment.STATUS_PENDING)

        # Step 4: Lawyer accepts assignment and submits proceeding update
        accept_lawyer_assignment(asgn, self.lawyer)
        e2e_case.refresh_from_db()
        self.assertEqual(e2e_case.status, CaseRecord.STATUS_IN_PROGRESS)

        add_lawyer_case_update(e2e_case, self.lawyer, "Filed notice of representation in court.")

        # Step 5: Officer initiates mediation
        med = initiate_case_mediation(e2e_case, self.officer, self.mediator, mode='in_person')
        e2e_case.refresh_from_db()
        self.assertEqual(e2e_case.status, CaseRecord.STATUS_MEDIATION)

        # Mediator records agreed settlement outcome
        update_mediation_outcome(med, self.mediator, "both_present", "Mutual agreement reached on lease term.", Mediation.STATUS_AGREED)
        e2e_case.refresh_from_db()
        self.assertEqual(e2e_case.status, CaseRecord.STATUS_RESOLVED)

        # Step 6: Upload document and sign with MockSignatureService
        f = SimpleUploadedFile("accord.pdf", b"Formal Settlement Agreement Accord", content_type="application/pdf")
        doc = upload_case_document(e2e_case, self.officer, "Settlement Accord", f)
        from core.mock_services import MockSignatureService
        sig = MockSignatureService.create_signature(doc, self.officer, notes="Signed accord")
        self.assertTrue(sig.verified)

        # Step 7: Close case
        transition_case_status(e2e_case, self.officer, CaseRecord.STATUS_CLOSED, reason="Settled via ADR mediation accord")
        e2e_case.refresh_from_db()
        self.assertEqual(e2e_case.status, CaseRecord.STATUS_CLOSED)
        self.assertIsNotNone(e2e_case.closed_at)

        # Step 8: Verify Complete Append-Only CaseEvent Audit History
        events = list(CaseEvent.objects.filter(case=e2e_case).order_by('created_at'))
        actions = [ev.action for ev in events]

        expected_actions = [
            'APPLICATION_SUBMITTED',
            'APPLICATION_ACCEPTED',
            'LAWYER_ASSIGNED',
            'LAWYER_ASSIGNMENT_ACCEPTED',
            'LAWYER_CASE_UPDATE',
            'MEDIATION_INITIATED',
            'MEDIATION_OUTCOME_RECORDED',
            'DOCUMENT_UPLOADED',
            'SIGNATURE_CREATED',
            'CASE_STATUS_CHANGED',
        ]
        for exp in expected_actions:
            self.assertIn(exp, actions, f"Missing expected audit action: {exp}")

    def test_udc_assisted_intake_workflow(self):
        """Verifies UDC assisted-intake workflow: consent, read-back, submit, end access."""
        self.client.force_login(self.udc_operator)

        # GET intake page
        get_res = self.client.get(reverse('cases:udc_intake'))
        self.assertEqual(get_res.status_code, 200)
        token = get_res.context['submission_token']

        # Review step
        review_data = {
            'action': 'review',
            'submission_token': token,
            'name': 'UDC Beneficiary',
            'phone': '01733445566',
            'address': 'Union: Joypur, Upazila: Kaliganj',
            'legal_problem': 'Land boundary dispute with neighbor',
            'incident_description': 'Neighbor erected unauthorized fence.',
            'preferred_channel': 'udc',
            'safe_contact_number': '',
            'safe_contact_time': '',
            'language': 'bn',
            'citizen_consent': 'on',
            'review_read_back': 'on',
        }
        rev_res = self.client.post(reverse('cases:udc_intake'), review_data)
        self.assertEqual(rev_res.status_code, 200)
        self.assertContains(rev_res, "UDC Beneficiary")

        # Submit step
        submit_data = dict(review_data)
        submit_data['action'] = 'submit'
        sub_res = self.client.post(reverse('cases:udc_intake'), submit_data)
        self.assertEqual(sub_res.status_code, 302)

        # Verify application created
        udc_app = Application.objects.get(name='UDC Beneficiary')
        self.assertEqual(udc_app.preferred_channel, Application.CHANNEL_UDC)
        self.assertIsNone(udc_app.applicant_user)  # Operator does NOT own case

        # Verify audit trail provenance
        intake_event = CaseEvent.objects.get(application=udc_app, action='APPLICATION_SUBMITTED')
        self.assertEqual(intake_event.provenance, CaseEvent.PROVENANCE_INTERMEDIARY_TRANSLATED)

        # Confirm operator cannot access citizen case detail
        case_rec = accept_application(udc_app, self.officer)
        access_res = self.client.get(reverse('cases:officer_case_detail', args=[case_rec.case_id]))
        self.assertEqual(access_res.status_code, 403)



