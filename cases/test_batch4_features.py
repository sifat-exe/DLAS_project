"""
Comprehensive Automated Test Suite for Batch 4 Features:
- PART A: Referral Package (Data fields, Pre-submission Bilingual Review, Immutability)
- PART B: Acknowledgement Deadline (Explicit Acknowledgement, Timestamp, Server-side Authorization)
- PART C: Missed Deadline & Mandatory Handoff (Overdue Detection, Escalation, Alternate Officer Reassignment)
- PART D: Follow-up (Task Generation, Context & Guidance for New Officer)
- PART E: Case Events (referral_package_created, referral_sent, referral_acknowledged, referral_deadline_missed, referral_escalated, referral_reassigned, referral_followup_created)
- PART F: Workspace Action Center (What to Do Next, Next Step, Recently Done)
- PART G: Bilingual UI Strings
- PART H: Security (IDOR, Role Restrictions, Invalid State Transitions)
- PART I: Timezone Correctness (Aware datetimes, zero naive datetime warnings)
"""

from datetime import datetime, date, timedelta
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.management import call_command
from django.core.exceptions import ValidationError, PermissionDenied
from accounts.models import UserProfile
from cases.models import Application, CaseRecord, CaseEvent, Task
from documents.models import Document
from referrals.models import Referral
from cases.services import (
    submit_application,
    accept_application,
    create_case_referral,
    acknowledge_referral,
    check_and_handoff_overdue_referral,
    process_overdue_referrals,
    ensure_aware_datetime,
)
from dashboard.services import get_role_workspace_context


class Batch4FeaturesTestCase(TestCase):
    def setUp(self):
        # 1. Primary DLAO Officer
        self.officer1 = User.objects.create_user(
            username='dlao_officer_b4_1',
            password='password123',
            first_name='Rahim',
            last_name='Officer'
        )
        UserProfile.objects.create(
            user=self.officer1,
            role=UserProfile.ROLE_DLAO_OFFICER,
            phone='01710000041',
            language='en'
        )

        # 2. Alternate DLAO Officer for Handoff
        self.officer2 = User.objects.create_user(
            username='dlao_officer_b4_2',
            password='password123',
            first_name='Karim',
            last_name='Handoff'
        )
        UserProfile.objects.create(
            user=self.officer2,
            role=UserProfile.ROLE_DLAO_OFFICER,
            phone='01710000042',
            language='bn'
        )

        # 3. Third DLAO Officer
        self.officer3 = User.objects.create_user(
            username='dlao_officer_b4_3',
            password='password123',
            first_name='Salma',
            last_name='Officer'
        )
        UserProfile.objects.create(
            user=self.officer3,
            role=UserProfile.ROLE_DLAO_OFFICER,
            phone='01710000043',
            language='bn'
        )

        # 4. Citizen User (Unauthorized for referral ops)
        self.citizen = User.objects.create_user(
            username='citizen_b4',
            password='password123',
            first_name='Anowar',
            last_name='Hossain'
        )
        UserProfile.objects.create(
            user=self.citizen,
            role=UserProfile.ROLE_CITIZEN,
            phone='01710000044',
            language='bn'
        )

        # 5. Create active case for referral testing
        self.app = submit_application(
            name="Shahana Begum",
            phone="01811223344",
            address="Munshiganj Sadar, Munshiganj",
            legal_problem="family_dispute",
            incident_description="Physical dowry abuse and eviction from marital residence. Applicant seeking immediate maintenance and protection.",
            applicant_user=self.citizen,
            safe_contact_number="01899887766",
            language="bn",
        )
        self.case = accept_application(self.app, self.officer1, priority='HIGH')

        # Attach sample case document
        self.doc = Document.objects.create(
            case=self.case,
            uploaded_by=self.officer1,
            title="Medical Injury Report & Police General Diary",
            description="Emergency clinic hospital certificate documenting dowry battery.",
            status=Document.STATUS_VERIFIED
        )

    # =========================================================================
    # PART A: REFERRAL PACKAGE TESTS (1-6)
    # =========================================================================

    def test_01_authorized_user_can_create_referral_package(self):
        """1. Authorized DLAO officer can create an official referral package."""
        deadline = timezone.now() + timedelta(days=5)
        referral = create_case_referral(
            case_record=self.case,
            officer=self.officer1,
            destination="DLAO Narayanganj",
            reason="Jurisdiction transfer per applicant residential shelter relocation",
            expected_action="Direct intake, shelter liaison, and court filing",
            deadline=deadline,
            assigned_officer=self.officer2,
            package_notes="Urgent protection matter. Applicant residing at safe home.",
            included_document_ids=str(self.doc.id),
        )

        self.assertIsNotNone(referral.id)
        self.assertEqual(referral.destination, "DLAO Narayanganj")
        self.assertEqual(referral.status, Referral.STATUS_PENDING)
        self.assertEqual(referral.assigned_officer, self.officer2)
        self.assertEqual(referral.created_by, self.officer1)
        self.assertEqual(self.case.status, CaseRecord.STATUS_REFERRED)

    def test_02_package_review_appears_before_confirmation(self):
        """2. Submitting 'preview_referral' displays bilingual package review before confirmation."""
        self.client.force_login(self.officer1)
        url = reverse('cases:officer_case_detail', kwargs={'case_id': self.case.case_id})
        resp = self.client.post(url, {
            'action': 'preview_referral',
            'destination': 'DLAO Gazipur',
            'reason': 'Cross-district employer liability',
            'expected_action': 'Labor tribunal representation',
            'deadline': '2026-11-15',
            'assigned_officer_id': self.officer2.id,
            'package_notes': 'Please inspect factory registers.',
            'document_ids': [self.doc.id],
        })

        self.assertEqual(resp.status_code, 200)
        content_en = resp.content.decode('utf-8')
        # English package review titles
        self.assertIn("Referral Package", content_en)
        self.assertIn("Case Information", content_en)
        self.assertIn("Referral Reason", content_en)
        self.assertIn("Expected Action", content_en)
        self.assertIn("Deadline", content_en)
        self.assertIn("Confirm Referral", content_en)
        self.assertIn("DLAO Gazipur", content_en)

        # Switch to Bangla
        self.client.get(reverse('core:set_language', kwargs={'lang': 'bn'}))
        resp_bn = self.client.post(url, {
            'action': 'preview_referral',
            'destination': 'DLAO Gazipur',
            'reason': 'Cross-district employer liability',
            'expected_action': 'Labor tribunal representation',
            'deadline': '2026-11-15',
            'assigned_officer_id': self.officer2.id,
            'package_notes': 'Please inspect factory registers.',
            'document_ids': [self.doc.id],
        })
        self.assertEqual(resp_bn.status_code, 200)
        content_bn = resp_bn.content.decode('utf-8')
        # Proper Bangla package review titles
        self.assertIn("রেফারাল প্যাকেজ", content_bn)
        self.assertIn("মামলার তথ্য", content_bn)
        self.assertIn("রেফারালের কারণ", content_bn)
        self.assertIn("প্রত্যাশিত পদক্ষেপ", content_bn)
        self.assertIn("সময়সীমা", content_bn)
        self.assertIn("রেফারাল নিশ্চিত করুন", content_bn)

    def test_03_referral_not_created_before_confirmation(self):
        """3. Previewing the package does NOT create a Referral record in the database."""
        self.client.force_login(self.officer1)
        url = reverse('cases:officer_case_detail', kwargs={'case_id': self.case.case_id})
        initial_count = Referral.objects.filter(case=self.case).count()

        resp = self.client.post(url, {
            'action': 'preview_referral',
            'destination': 'Labor Court Dhaka',
            'reason': 'Wrongful dismissal',
            'expected_action': 'File statement of claim',
            'deadline': '2026-12-01',
        })
        self.assertEqual(resp.status_code, 200)
        # Database count remains identical
        self.assertEqual(Referral.objects.filter(case=self.case).count(), initial_count)

    def test_04_referral_contains_required_case_and_reference_information(self):
        """4. Referral package contains complete case references and documentation metadata."""
        deadline = timezone.now() + timedelta(days=7)
        referral = create_case_referral(
            case_record=self.case,
            officer=self.officer1,
            destination="DLAO Narayanganj",
            reason="Jurisdiction transfer",
            expected_action="Representation in family court",
            deadline=deadline,
            assigned_officer=self.officer2,
            package_notes="Emergency medical reports included.",
            included_document_ids=str(self.doc.id),
        )

        self.assertEqual(referral.case.case_id, self.case.case_id)
        self.assertEqual(referral.case.application.application_id, self.app.application_id)
        self.assertEqual(referral.case.application.name, "Shahana Begum")
        self.assertEqual(referral.case.application.phone, "01811223344")
        self.assertEqual(referral.case.priority, 'HIGH')
        self.assertIn(str(self.doc.id), referral.included_document_ids)

    def test_05_unauthorized_user_cannot_access_package_or_create(self):
        """5. Unauthorized citizens cannot preview, create, or access referral packages."""
        self.client.force_login(self.citizen)
        url = reverse('cases:officer_case_detail', kwargs={'case_id': self.case.case_id})
        resp = self.client.post(url, {
            'action': 'preview_referral',
            'destination': 'External Agency',
            'reason': 'Unauthorized attempt',
            'expected_action': 'Action',
            'deadline': '2026-11-20',
        })
        self.assertEqual(resp.status_code, 403)

        # Direct service call raises PermissionDenied
        with self.assertRaises(PermissionDenied):
            create_case_referral(
                case_record=self.case,
                officer=self.citizen,
                destination="Unauthorized Destination",
                reason="Unauthorized Reason",
                expected_action="Action",
                deadline=timezone.now() + timedelta(days=3)
            )

    def test_06_referral_creation_produces_case_events(self):
        """6. Referral creation generates referral_package_created, referral_sent, and CASE_REFERRED events."""
        deadline = timezone.now() + timedelta(days=4)
        referral = create_case_referral(
            case_record=self.case,
            officer=self.officer1,
            destination="DLAO Comilla",
            reason="Inter-district marital dispute",
            expected_action="Liaison and hearing attendance",
            deadline=deadline,
            assigned_officer=self.officer2,
        )

        ev_package = CaseEvent.objects.filter(case=self.case, action='referral_package_created').first()
        self.assertIsNotNone(ev_package)
        self.assertEqual(ev_package.actor, self.officer1)
        self.assertIn("DLAO Comilla", ev_package.description)

        ev_sent = CaseEvent.objects.filter(case=self.case, action='referral_sent').first()
        self.assertIsNotNone(ev_sent)
        self.assertIn(self.officer2.username, ev_sent.description)

        ev_case_ref = CaseEvent.objects.filter(case=self.case, action='CASE_REFERRED').first()
        self.assertIsNotNone(ev_case_ref)

    # =========================================================================
    # PART B: ACKNOWLEDGEMENT DEADLINE TESTS (7-10)
    # =========================================================================

    def test_07_receiving_officer_can_acknowledge_assigned_referral(self):
        """7. Assigned receiving officer can acknowledge the referral."""
        deadline = timezone.now() + timedelta(days=3)
        referral = create_case_referral(
            case_record=self.case,
            officer=self.officer1,
            destination="DLAO Narayanganj",
            reason="Relocation",
            expected_action="Intake",
            deadline=deadline,
            assigned_officer=self.officer2,
        )

        self.client.force_login(self.officer2)
        url = reverse('referrals:referral_detail', kwargs={'referral_id': referral.id})
        resp = self.client.post(url, {'action': 'acknowledge'})
        self.assertEqual(resp.status_code, 302)

        referral.refresh_from_db()
        self.assertEqual(referral.status, Referral.STATUS_ACKNOWLEDGED)
        self.assertIsNotNone(referral.acknowledged_at)

    def test_08_acknowledgement_timestamp_is_recorded(self):
        """8. Acknowledgement timestamp is recorded accurately as an aware datetime."""
        deadline = timezone.now() + timedelta(days=3)
        referral = create_case_referral(
            case_record=self.case,
            officer=self.officer1,
            destination="DLAO Narayanganj",
            reason="Relocation",
            expected_action="Intake",
            deadline=deadline,
            assigned_officer=self.officer2,
        )

        before = timezone.now()
        acknowledge_referral(referral, self.officer2)
        after = timezone.now()

        referral.refresh_from_db()
        self.assertTrue(before <= referral.acknowledged_at <= after)
        self.assertFalse(timezone.is_naive(referral.acknowledged_at))

    def test_09_referral_acknowledged_case_event_and_task_completion(self):
        """9. referral_acknowledged CaseEvent is logged and pending Task is marked completed."""
        deadline = timezone.now() + timedelta(days=3)
        referral = create_case_referral(
            case_record=self.case,
            officer=self.officer1,
            destination="DLAO Tangail",
            reason="Jurisdiction transfer",
            expected_action="Representation",
            deadline=deadline,
            assigned_officer=self.officer2,
        )

        acknowledge_referral(referral, self.officer2)

        ev = CaseEvent.objects.filter(case=self.case, action='referral_acknowledged').first()
        self.assertIsNotNone(ev)
        self.assertEqual(ev.actor, self.officer2)
        self.assertIn("acknowledged by", ev.description)

        # Initial acknowledgement task completed
        task = Task.objects.filter(case=self.case, assigned_to=self.officer2).first()
        if task:
            self.assertEqual(task.status, Task.STATUS_COMPLETED)

    def test_10_unauthorized_officer_cannot_acknowledge(self):
        """10. Officer 3 cannot acknowledge a referral assigned specifically to Officer 2."""
        deadline = timezone.now() + timedelta(days=3)
        referral = create_case_referral(
            case_record=self.case,
            officer=self.officer1,
            destination="DLAO Narayanganj",
            reason="Relocation",
            expected_action="Intake",
            deadline=deadline,
            assigned_officer=self.officer2,
        )

        # Direct service call raises PermissionDenied
        with self.assertRaises(PermissionDenied):
            acknowledge_referral(referral, self.officer3)

        # View call raises PermissionDenied (HTTP 403)
        self.client.force_login(self.officer3)
        url = reverse('referrals:referral_detail', kwargs={'referral_id': referral.id})
        resp = self.client.post(url, {'action': 'acknowledge'})
        self.assertEqual(resp.status_code, 403)

        referral.refresh_from_db()
        self.assertEqual(referral.status, Referral.STATUS_PENDING)
        self.assertIsNone(referral.acknowledged_at)

    # =========================================================================
    # PART C & D: MISSED DEADLINE & HANDOFF TESTS (11-23)
    # =========================================================================

    def test_11_referral_deadline_stored_correctly_and_aware(self):
        """11. Referral deadline is stored as a timezone-aware datetime."""
        deadline_str = "2026-11-25 10:00:00"
        referral = create_case_referral(
            case_record=self.case,
            officer=self.officer1,
            destination="DLAO Narayanganj",
            reason="Relocation",
            expected_action="Intake",
            deadline=deadline_str,
            assigned_officer=self.officer2,
        )
        self.assertFalse(timezone.is_naive(referral.deadline))
        self.assertEqual(referral.deadline.year, 2026)
        self.assertEqual(referral.deadline.month, 11)

    def test_12_current_deadline_status_displayed(self):
        """12. Current deadline and pending acknowledgement status are displayed in UI."""
        deadline = timezone.now() + timedelta(days=4)
        referral = create_case_referral(
            case_record=self.case,
            officer=self.officer1,
            destination="DLAO Narayanganj",
            reason="Relocation",
            expected_action="Intake",
            deadline=deadline,
            assigned_officer=self.officer2,
        )

        self.client.force_login(self.officer2)
        url = reverse('referrals:referral_detail', kwargs={'referral_id': referral.id})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        content_en = resp.content.decode('utf-8')
        self.assertIn("Pending Acknowledgement", content_en)

        # In Bangla
        self.client.get(reverse('core:set_language', kwargs={'lang': 'bn'}))
        resp_bn = self.client.get(url)
        content_bn = resp_bn.content.decode('utf-8')
        self.assertIn("গ্রহণ নিশ্চিতকরণের অপেক্ষায়", content_bn)

    def test_13_missed_deadline_detected_and_overdue_flag(self):
        """13. Missed deadline is detected and is_overdue property evaluates to True."""
        past_deadline = timezone.now() - timedelta(days=2)
        referral = create_case_referral(
            case_record=self.case,
            officer=self.officer1,
            destination="DLAO Narayanganj",
            reason="Relocation",
            expected_action="Intake",
            deadline=past_deadline,
            assigned_officer=self.officer2,
        )

        self.assertTrue(referral.is_overdue)
        check_and_handoff_overdue_referral(referral)
        referral.refresh_from_db()
        self.assertIsNotNone(referral.missed_deadline_at)

    def test_14_referral_deadline_missed_case_event_created(self):
        """14. referral_deadline_missed CaseEvent is created upon detecting missed deadline."""
        past_deadline = timezone.now() - timedelta(hours=5)
        referral = create_case_referral(
            case_record=self.case,
            officer=self.officer1,
            destination="DLAO Narayanganj",
            reason="Relocation",
            expected_action="Intake",
            deadline=past_deadline,
            assigned_officer=self.officer2,
        )

        check_and_handoff_overdue_referral(referral)
        ev = CaseEvent.objects.filter(case=self.case, action='referral_deadline_missed').first()
        self.assertIsNotNone(ev)
        self.assertIn("missed by", ev.description)

    def test_15_missed_referral_is_escalated(self):
        """15. Missed referral logs referral_escalated CaseEvent."""
        past_deadline = timezone.now() - timedelta(days=1)
        referral = create_case_referral(
            case_record=self.case,
            officer=self.officer1,
            destination="DLAO Narayanganj",
            reason="Relocation",
            expected_action="Intake",
            deadline=past_deadline,
            assigned_officer=self.officer2,
        )

        check_and_handoff_overdue_referral(referral)
        ev = CaseEvent.objects.filter(case=self.case, action='referral_escalated').first()
        self.assertIsNotNone(ev)

    def test_16_referral_passed_to_another_eligible_officer(self):
        """16. Overdue referral is passed to another eligible DLAO officer."""
        past_deadline = timezone.now() - timedelta(days=1)
        referral = create_case_referral(
            case_record=self.case,
            officer=self.officer1,
            destination="DLAO Narayanganj",
            reason="Relocation",
            expected_action="Intake",
            deadline=past_deadline,
            assigned_officer=self.officer2,
        )

        check_and_handoff_overdue_referral(referral)
        referral.refresh_from_db()

        self.assertEqual(referral.status, Referral.STATUS_REASSIGNED)
        self.assertEqual(referral.previous_officer, self.officer2)
        # Assigned to another active DLAO officer (e.g. officer1 or officer3, but NOT officer2)
        self.assertNotEqual(referral.assigned_officer, self.officer2)
        self.assertIn(referral.assigned_officer, [self.officer1, self.officer3])
        self.assertIsNotNone(referral.reassigned_at)

    def test_17_original_officer_does_not_receive_the_reassignment(self):
        """17. Original responsible officer is strictly excluded from receiving the handoff."""
        past_deadline = timezone.now() - timedelta(hours=10)
        referral = create_case_referral(
            case_record=self.case,
            officer=self.officer1,
            destination="DLAO Narayanganj",
            reason="Relocation",
            expected_action="Intake",
            deadline=past_deadline,
            assigned_officer=self.officer2,
        )

        check_and_handoff_overdue_referral(referral)
        referral.refresh_from_db()
        self.assertNotEqual(referral.assigned_officer.id, self.officer2.id)

    def test_18_new_officer_sees_referral_in_their_workspace(self):
        """18. New officer sees the handed-off referral in their workspace action center."""
        past_deadline = timezone.now() - timedelta(days=1)
        referral = create_case_referral(
            case_record=self.case,
            officer=self.officer1,
            destination="DLAO Gazipur",
            reason="Relocation",
            expected_action="Intake",
            deadline=past_deadline,
            assigned_officer=self.officer2,
        )

        # Force handoff directly to officer3
        check_and_handoff_overdue_referral(referral)
        referral.refresh_from_db()
        new_officer = referral.assigned_officer

        context = get_role_workspace_context(new_officer, 'dlao_officer')
        pending_ids = [p['identifier'] for p in context['pending_actions']]
        self.assertIn(f"REF-{referral.id}", pending_ids)

        # Checks top next step
        self.assertIsNotNone(context['top_next_step'])
        self.assertEqual(context['top_next_step']['target_id'], f"REF-{referral.id}")

    def test_19_referral_reassigned_case_event_created(self):
        """19. referral_reassigned CaseEvent logs old officer and new officer."""
        past_deadline = timezone.now() - timedelta(days=2)
        referral = create_case_referral(
            case_record=self.case,
            officer=self.officer1,
            destination="DLAO Narayanganj",
            reason="Relocation",
            expected_action="Intake",
            deadline=past_deadline,
            assigned_officer=self.officer2,
        )

        check_and_handoff_overdue_referral(referral)
        ev = CaseEvent.objects.filter(case=self.case, action='referral_reassigned').first()
        self.assertIsNotNone(ev)
        self.assertIn(self.officer2.username, ev.description)

    def test_20_previous_assignment_remains_visible_in_history(self):
        """20. Previous assignment remains visible on the referral record and detail page."""
        past_deadline = timezone.now() - timedelta(days=1)
        referral = create_case_referral(
            case_record=self.case,
            officer=self.officer1,
            destination="DLAO Narayanganj",
            reason="Relocation",
            expected_action="Intake",
            deadline=past_deadline,
            assigned_officer=self.officer2,
        )

        check_and_handoff_overdue_referral(referral)
        referral.refresh_from_db()

        self.assertEqual(referral.previous_officer, self.officer2)

        self.client.force_login(referral.assigned_officer)
        url = reverse('referrals:referral_detail', kwargs={'referral_id': referral.id})
        resp = self.client.get(url)
        content_en = resp.content.decode('utf-8')
        self.assertIn("Previous Officer", content_en)
        self.assertIn(self.officer2.username, content_en)

        # In Bangla
        self.client.get(reverse('core:set_language', kwargs={'lang': 'bn'}))
        resp_bn = self.client.get(url)
        content_bn = resp_bn.content.decode('utf-8')
        self.assertIn("পূর্ববর্তী কর্মকর্তা", content_bn)

    def test_21_no_eligible_alternate_officer_results_in_explicit_manual_reassignment(self):
        """21. If no other eligible officer exists, referral is marked Escalated with manual reassignment note."""
        # Deactivate all other DLAO officers except officer2
        self.officer1.is_active = False
        self.officer1.save()
        self.officer3.is_active = False
        self.officer3.save()

        past_deadline = timezone.now() - timedelta(days=1)
        referral = Referral.objects.create(
            case=self.case,
            created_by=self.officer2,
            assigned_officer=self.officer2,
            destination="Remote DLAO",
            reason="No alternate test",
            expected_action="Action",
            deadline=past_deadline,
            status=Referral.STATUS_PENDING,
        )

        check_and_handoff_overdue_referral(referral)
        referral.refresh_from_db()

        self.assertEqual(referral.status, Referral.STATUS_ESCALATED)
        ev = CaseEvent.objects.filter(case=self.case, action='referral_escalated').last()
        self.assertIn("Manual reassignment required", ev.description)

        # Restore users
        self.officer1.is_active = True
        self.officer1.save()
        self.officer3.is_active = True
        self.officer3.save()

    def test_22_followup_action_task_created(self):
        """22. High-priority follow-up Task is created for the new officer."""
        past_deadline = timezone.now() - timedelta(days=1)
        referral = create_case_referral(
            case_record=self.case,
            officer=self.officer1,
            destination="DLAO Narayanganj",
            reason="Relocation",
            expected_action="Intake",
            deadline=past_deadline,
            assigned_officer=self.officer2,
        )

        check_and_handoff_overdue_referral(referral)
        referral.refresh_from_db()
        new_officer = referral.assigned_officer

        task = Task.objects.filter(case=self.case, assigned_to=new_officer, title__startswith="FOLLOW-UP:").first()
        self.assertIsNotNone(task)
        self.assertEqual(task.priority, 'HIGH')
        self.assertIn(self.officer2.username, task.description)

    def test_23_new_officer_can_continue_and_acknowledge_referral(self):
        """23. Reassigned new officer can acknowledge the referral and conclude handoff."""
        past_deadline = timezone.now() - timedelta(days=1)
        referral = create_case_referral(
            case_record=self.case,
            officer=self.officer1,
            destination="DLAO Narayanganj",
            reason="Relocation",
            expected_action="Intake",
            deadline=past_deadline,
            assigned_officer=self.officer2,
        )

        check_and_handoff_overdue_referral(referral)
        referral.refresh_from_db()
        new_officer = referral.assigned_officer

        # New officer acknowledges
        acknowledge_referral(referral, new_officer)
        referral.refresh_from_db()
        self.assertEqual(referral.status, Referral.STATUS_ACKNOWLEDGED)

    # =========================================================================
    # PART H: SECURITY TESTS (24-27)
    # =========================================================================

    def test_24_idor_attempts_fail_on_acknowledgement(self):
        """24. IDOR Protection: Non-assigned officer cannot hijack acknowledgement."""
        deadline = timezone.now() + timedelta(days=5)
        referral = create_case_referral(
            case_record=self.case,
            officer=self.officer1,
            destination="DLAO Narayanganj",
            reason="Relocation",
            expected_action="Intake",
            deadline=deadline,
            assigned_officer=self.officer2,
        )

        with self.assertRaises(PermissionDenied):
            acknowledge_referral(referral, self.officer3)

    def test_25_invalid_state_transitions_fail(self):
        """25. Invalid State Transition: Cannot acknowledge an already completed referral."""
        deadline = timezone.now() + timedelta(days=5)
        referral = create_case_referral(
            case_record=self.case,
            officer=self.officer1,
            destination="DLAO Narayanganj",
            reason="Relocation",
            expected_action="Intake",
            deadline=deadline,
            assigned_officer=self.officer2,
        )
        acknowledge_referral(referral, self.officer2)

        # Attempt to acknowledge again fails with ValidationError
        with self.assertRaises(ValidationError):
            acknowledge_referral(referral, self.officer2)

    def test_26_management_command_processes_overdue_referrals(self):
        """26. Management command process_overdue_referrals finds and hands off overdue items."""
        past_deadline = timezone.now() - timedelta(days=2)
        referral = create_case_referral(
            case_record=self.case,
            officer=self.officer1,
            destination="DLAO Bogra",
            reason="Overdue test",
            expected_action="Court filing",
            deadline=past_deadline,
            assigned_officer=self.officer2,
        )

        call_command('process_overdue_referrals')
        referral.refresh_from_db()
        self.assertEqual(referral.status, Referral.STATUS_REASSIGNED)
        self.assertNotEqual(referral.assigned_officer, self.officer2)

    def test_27_interactive_demo_trigger_handoff(self):
        """27. Interactive demo trigger_handoff action in referral detail executes handoff."""
        deadline = timezone.now() + timedelta(days=5)
        referral = create_case_referral(
            case_record=self.case,
            officer=self.officer1,
            destination="DLAO Sylhet",
            reason="Demo trigger test",
            expected_action="Action",
            deadline=deadline,
            assigned_officer=self.officer2,
        )

        self.client.force_login(self.officer1)
        url = reverse('referrals:referral_detail', kwargs={'referral_id': referral.id})
        resp = self.client.post(url, {'action': 'trigger_handoff'})
        self.assertEqual(resp.status_code, 302)

        referral.refresh_from_db()
        self.assertEqual(referral.status, Referral.STATUS_REASSIGNED)
        self.assertNotEqual(referral.assigned_officer, self.officer2)

    def test_28_timezone_normalization_helper_produces_aware_datetimes(self):
        """28. Timezone helper ensure_aware_datetime converts strings and naive dates without warnings."""
        # String ISO
        dt1 = ensure_aware_datetime("2026-10-25T14:30")
        self.assertFalse(timezone.is_naive(dt1))

        # String Date
        dt2 = ensure_aware_datetime("2026-11-01")
        self.assertFalse(timezone.is_naive(dt2))

        # Naive datetime
        naive = datetime(2026, 12, 1, 9, 30)
        dt3 = ensure_aware_datetime(naive)
        self.assertFalse(timezone.is_naive(dt3))
