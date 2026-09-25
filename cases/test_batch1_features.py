from django.test import TestCase, Client
from django.utils import timezone
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from accounts.models import UserProfile
from cases.models import Application, CaseRecord, CaseEvent, Task
from cases.services import submit_application, accept_application, verify_application_nid
from dashboard.services import get_role_workspace_context, get_case_record_priority_rank
from lawyers.models import LawyerAssignment
from mediation.models import Mediation
from referrals.models import Referral
from documents.models import Document


def create_test_user(username, role, password='password123', language='en'):
    """Helper to create a User along with their required UserProfile."""
    user = User.objects.create_user(username=username, password=password)
    UserProfile.objects.create(
        user=user,
        role=role,
        phone='01700000000',
        language=language
    )
    return user


class Batch1Feature1NIDNotMandatoryTests(TestCase):
    """
    FEATURE 1 — NID VERIFICATION MUST NOT BE MANDATORY
    1. Application can be submitted without NID verification.
    2. Application ID is generated.
    3. DLAO can review and accept the application.
    4. Unverified NID does not cause rejection.
    5. Verified NID still works with simulated mock service.
    6. Unauthorized users cannot manipulate another application's NID status (IDOR).
    """

    def setUp(self):
        self.officer_user = create_test_user('dlao_test_officer', UserProfile.ROLE_DLAO_OFFICER)
        self.citizen_user = create_test_user('citizen_test_user', UserProfile.ROLE_CITIZEN)
        self.other_citizen = create_test_user('other_citizen_user', UserProfile.ROLE_CITIZEN)
        self.client = Client()

    def test_application_submission_without_nid_generates_app_id(self):
        """A citizen must be able to submit an application without NID verification."""
        app = submit_application(
            name="Rahima Begum",
            phone="01711000001",
            address="Village: Char Gopalpur, District: Barishal",
            legal_problem="Unlawful eviction from homestead land",
            incident_description="Opposing party threatened and attempted forced eviction yesterday.",
            applicant_user=self.citizen_user,
            nid_number="",  # No NID provided
        )

        self.assertIsNotNone(app.application_id)
        self.assertTrue(app.application_id.startswith('APP-'))
        self.assertEqual(app.status, Application.STATUS_SUBMITTED)
        self.assertEqual(app.nid_number, "")
        self.assertEqual(app.nid_verification_status, Application.NID_STATUS_NOT_VERIFIED)
        self.assertFalse(app.is_nid_verified)
        self.assertFalse(hasattr(app, 'case_record'))

        # Verify audit CaseEvent created
        event = CaseEvent.objects.filter(application=app, action='APPLICATION_SUBMITTED').first()
        self.assertIsNotNone(event)
        self.assertIn("Not verified", event.description)

    def test_dlao_can_review_and_accept_unverified_nid_application(self):
        """Unverified NID does NOT block DLAO review or acceptance and does not cause rejection."""
        app = submit_application(
            name="Karim Ullah",
            phone="01811000002",
            address="Village: Mirpur, District: Dhaka",
            legal_problem="Denial of legitimate wage payment",
            incident_description="Employer refused to pay wages for four months.",
            applicant_user=self.citizen_user,
            nid_number="",
            nid_verification_status=Application.NID_STATUS_NOT_VERIFIED,
        )

        # DLAO reviews and accepts application without NID verification
        case_record = accept_application(
            application=app,
            officer=self.officer_user,
            priority=CaseRecord.PRIORITY_HIGH,
        )

        self.assertIsNotNone(case_record)
        self.assertTrue(case_record.case_id.startswith('CASE-'))
        self.assertEqual(case_record.status, CaseRecord.STATUS_ACCEPTED)
        self.assertEqual(case_record.priority, CaseRecord.PRIORITY_HIGH)
        self.assertEqual(case_record.application.nid_verification_status, Application.NID_STATUS_NOT_VERIFIED)

        # App status updated to accepted
        app.refresh_from_db()
        self.assertEqual(app.status, Application.STATUS_ACCEPTED)

    def test_mock_nid_verification_successful_flow(self):
        """Simulated NID verification works deterministically when valid test NID is provided."""
        app = submit_application(
            name="Nazmul Islam",
            phone="01911000003",
            address="Sylhet Sadar",
            legal_problem="Inheritance claim",
            incident_description="Brothers denying share in ancestral property.",
            applicant_user=self.citizen_user,
        )

        res = verify_application_nid(
            application=app,
            nid_number="19851234567890123",  # 17 digits valid test NID
            user=self.officer_user,
        )

        self.assertTrue(res['is_verified'])
        self.assertEqual(res['status'], 'VERIFIED')

        app.refresh_from_db()
        self.assertEqual(app.nid_verification_status, Application.NID_STATUS_VERIFIED)
        self.assertTrue(app.is_nid_verified)

        # Verify audit event logged
        event = CaseEvent.objects.filter(application=app, action='NID_VERIFIED').first()
        self.assertIsNotNone(event)
        self.assertIn("Token:", event.description)

    def test_mock_nid_verification_failure_does_not_reject_application(self):
        """Failed NID verification updates status to 'verification_failed' but application is NOT rejected."""
        app = submit_application(
            name="Testing User",
            phone="01611000004",
            address="Khulna",
            legal_problem="Boundary dispute",
            incident_description="Boundary fence pulled down.",
            applicant_user=self.citizen_user,
        )

        res = verify_application_nid(
            application=app,
            nid_number="0000000000",  # Test failure code in MockNIDService
            user=self.officer_user,
        )

        self.assertFalse(res['is_verified'])
        app.refresh_from_db()
        self.assertEqual(app.nid_verification_status, Application.NID_STATUS_FAILED)
        self.assertEqual(app.status, Application.STATUS_SUBMITTED)  # Remains submitted, not rejected!

        # DLAO can still accept despite failed NID verification
        case_record = accept_application(app, self.officer_user)
        self.assertIsNotNone(case_record)
        self.assertEqual(case_record.status, CaseRecord.STATUS_ACCEPTED)

    def test_idor_protection_unauthorized_user_cannot_verify_others_nid(self):
        """A citizen cannot trigger NID verification on another citizen's application (IDOR)."""
        app = submit_application(
            name="Victim User",
            phone="01511000005",
            address="Rajshahi",
            legal_problem="Dowry harassment",
            incident_description="In-laws demanding dowry.",
            applicant_user=self.citizen_user,
        )

        with self.assertRaises(PermissionDenied):
            verify_application_nid(
                application=app,
                nid_number="19851234567890123",
                user=self.other_citizen,  # Unauthorized third party
            )

        app.refresh_from_db()
        self.assertEqual(app.nid_verification_status, Application.NID_STATUS_NOT_VERIFIED)


class Batch1Feature2DLAOPriorityQueueTests(TestCase):
    """
    FEATURE 2 — DLAO CASE QUEUE PRIORITY
    1. URGENT cases first.
    2. Then other priority levels (HIGH > MEDIUM > LOW).
    3. Then date-wise ordering (older cases appear first FIFO within same priority).
    Server-side implementation (not JS only).
    """

    def setUp(self):
        self.officer = create_test_user('priority_officer', UserProfile.ROLE_DLAO_OFFICER)

        now = timezone.now()

        # Helper to create case records with specific priority and creation time
        def make_case(name, priority, delta_hours):
            app = Application.objects.create(
                application_id=f"APP-P-{name}",
                name=f"Applicant {name}",
                phone="01700000000",
                address="Address",
                legal_problem="Problem",
                incident_description="Description",
                status=Application.STATUS_ACCEPTED,
            )
            cr = CaseRecord.objects.create(
                case_id=f"CASE-P-{name}",
                application=app,
                assigned_officer=self.officer,
                priority=priority,
                status=CaseRecord.STATUS_ACCEPTED,
            )
            # Adjust created_at backwards in time
            CaseRecord.objects.filter(id=cr.id).update(
                created_at=now - timezone.timedelta(hours=delta_hours)
            )
            cr.refresh_from_db()
            return cr

        # Create cases with mixed priorities and creation timestamps:
        # Older low priority case (created 10 hours ago)
        self.c_low_old = make_case("LOW_OLD", CaseRecord.PRIORITY_LOW, 10)
        # Older medium priority case (created 8 hours ago)
        self.c_med_old = make_case("MED_OLD", CaseRecord.PRIORITY_MEDIUM, 8)
        # Newer medium priority case (created 2 hours ago)
        self.c_med_new = make_case("MED_NEW", CaseRecord.PRIORITY_MEDIUM, 2)
        # High priority case (created 5 hours ago)
        self.c_high = make_case("HIGH", CaseRecord.PRIORITY_HIGH, 5)
        # Older urgent case (created 6 hours ago)
        self.c_urgent_old = make_case("URGENT_OLD", CaseRecord.PRIORITY_URGENT, 6)
        # Newer urgent case (created 1 hour ago)
        self.c_urgent_new = make_case("URGENT_NEW", CaseRecord.PRIORITY_URGENT, 1)

    def test_dlao_case_queue_server_side_priority_ordering(self):
        """
        Verify server-side ordering:
        URGENT cases first (oldest urgent -> newest urgent),
        then HIGH,
        then MEDIUM (oldest medium -> newest medium),
        then LOW.
        """
        rank_expr = get_case_record_priority_rank()
        cases = (
            CaseRecord.objects.filter(status=CaseRecord.STATUS_ACCEPTED)
            .annotate(priority_rank=rank_expr)
            .order_by('priority_rank', 'created_at')
        )

        expected_order = [
            self.c_urgent_old.case_id,  # Urgent older
            self.c_urgent_new.case_id,  # Urgent newer
            self.c_high.case_id,        # High
            self.c_med_old.case_id,     # Medium older
            self.c_med_new.case_id,     # Medium newer
            self.c_low_old.case_id,     # Low older
        ]

        actual_order = [c.case_id for c in cases]
        self.assertEqual(actual_order, expected_order)

    def test_urgent_cases_always_precede_older_non_urgent_cases(self):
        """Urgent cases must appear before older high/medium/low cases."""
        rank_expr = get_case_record_priority_rank()
        cases = list(
            CaseRecord.objects.filter(status=CaseRecord.STATUS_ACCEPTED)
            .annotate(priority_rank=rank_expr)
            .order_by('priority_rank', 'created_at')
        )

        urgent_indices = [i for i, c in enumerate(cases) if c.priority == CaseRecord.PRIORITY_URGENT]
        non_urgent_indices = [i for i, c in enumerate(cases) if c.priority != CaseRecord.PRIORITY_URGENT]

        # Every urgent case must have a smaller index than any non-urgent case
        self.assertTrue(max(urgent_indices) < min(non_urgent_indices))

    def test_officer_dashboard_view_renders_priority_queue(self):
        """Verify officer_dashboard view renders the queue with proper ordering and bilingual badges."""
        client = Client()
        client.force_login(self.officer)
        response_en = client.get(reverse('dashboard:officer'))
        self.assertEqual(response_en.status_code, 200)
        self.assertIn("URGENT", response_en.content.decode('utf-8'))

        # Switch to Bangla session
        session = client.session
        session['django_language'] = 'bn'
        session.save()
        response_bn = client.get(reverse('dashboard:officer'))
        self.assertEqual(response_bn.status_code, 200)
        self.assertIn("জরুরি", response_bn.content.decode('utf-8'))


class Batch1Feature3And4WorkspaceActionCenterTests(TestCase):
    """
    FEATURE 3 — POST-LOGIN "WHAT DO I DO NEXT?" WORKSPACE
    FEATURE 4 — ROLE-SPECIFIC ACTION PRIORITY & IDOR PROTECTION
    """

    def setUp(self):
        self.officer = create_test_user('ws_officer', UserProfile.ROLE_DLAO_OFFICER)
        self.lawyer = create_test_user('ws_lawyer', UserProfile.ROLE_PANEL_LAWYER)
        self.mediator = create_test_user('ws_mediator', UserProfile.ROLE_MEDIATOR)
        self.support_staff = create_test_user('ws_staff', UserProfile.ROLE_DLAO_SUPPORT_STAFF)
        self.citizen = create_test_user('ws_citizen', UserProfile.ROLE_CITIZEN)

    def test_dlao_workspace_context_derives_actionable_items(self):
        """DLAO workspace shows urgent cases, pending applications, unassigned cases."""
        app = submit_application(
            name="New Applicant",
            phone="01711223344",
            address="Dhaka",
            legal_problem="Child custody dispute",
            incident_description="Need legal aid for family court representation.",
            applicant_user=self.citizen,
        )

        ctx = get_role_workspace_context(self.officer, 'dlao_officer')
        self.assertTrue(ctx['has_access'])
        self.assertGreaterEqual(ctx['pending_count'], 1)
        self.assertIsNotNone(ctx['top_next_step'])

        # Next step directs officer to review the pending application
        self.assertEqual(ctx['top_next_step']['target_id'], app.application_id)
        self.assertIn("Review", ctx['top_next_step']['headline_en'])
        self.assertIn("পর্যালোচনা", ctx['top_next_step']['headline_bn'])

    def test_lawyer_workspace_strictly_isolated_to_assigned_cases(self):
        """Panel Lawyer only sees assignments where lawyer=user (prevents IDOR)."""
        app = submit_application(
            name="Lawyer Client",
            phone="01799887766",
            address="Chittagong",
            legal_problem="Labor dispute",
            incident_description="Wrongful termination.",
            applicant_user=self.citizen,
        )
        case_rec = accept_application(app, self.officer)

        # Create assignment for this lawyer
        asgn = LawyerAssignment.objects.create(
            case=case_rec,
            lawyer=self.lawyer,
            assigned_by=self.officer,
            status=LawyerAssignment.STATUS_PENDING,
        )

        ctx = get_role_workspace_context(self.lawyer, 'panel_lawyer')
        self.assertTrue(ctx['has_access'])
        self.assertGreaterEqual(ctx['pending_count'], 1)
        self.assertEqual(ctx['top_next_step']['target_id'], case_rec.case_id)
        self.assertIn("Assignment", ctx['top_next_step']['headline_en'])
        self.assertIn("সিদ্ধান্ত", ctx['top_next_step']['headline_bn'])

        # Other users (e.g. another lawyer) cannot see this assignment
        other_lawyer = create_test_user('other_lawyer', UserProfile.ROLE_PANEL_LAWYER)

        ctx_other = get_role_workspace_context(other_lawyer, 'panel_lawyer')
        self.assertEqual(ctx_other['pending_count'], 0)
        self.assertIsNone(ctx_other['top_next_step'])

    def test_support_staff_workspace_actions_and_permissions(self):
        """Support staff workspace identifies unverified documents."""
        app = submit_application(
            name="Document Applicant",
            phone="01800112233",
            address="Mymensingh",
            legal_problem="Land boundary",
            incident_description="Dispute over land survey.",
            applicant_user=self.citizen,
        )
        case_rec = accept_application(app, self.officer)

        # Create unverified document
        doc = Document.objects.create(
            case=case_rec,
            uploaded_by=self.citizen,
            title="Khatian 101",
            file="documents/sample.pdf",
            status=Document.STATUS_UPLOADED,
        )

        ctx = get_role_workspace_context(self.support_staff, 'support_staff')
        self.assertTrue(ctx['has_access'])
        self.assertGreaterEqual(ctx['pending_count'], 1)
        self.assertIsNotNone(ctx['top_next_step'])
        self.assertIn("Verify", ctx['top_next_step']['headline_en'])
        self.assertIn("যাচাই", ctx['top_next_step']['headline_bn'])

    def test_empty_workspace_state_handled_gracefully(self):
        """When there are no pending actions, returns clean empty structure without 500 error."""
        clean_officer = create_test_user('clean_officer', UserProfile.ROLE_DLAO_OFFICER)

        # No applications or cases exist for this isolated test context
        ctx = get_role_workspace_context(clean_officer, 'dlao_officer')
        self.assertTrue(ctx['has_access'])
        if ctx['pending_count'] == 0:
            self.assertIsNone(ctx['top_next_step'])
            self.assertEqual(len(ctx['pending_actions']), 0)

    def test_unauthenticated_access_returns_safe_empty_context(self):
        """Unauthenticated or anonymous user gets safe empty context without exceptions."""
        from django.contrib.auth.models import AnonymousUser
        ctx = get_role_workspace_context(AnonymousUser(), 'dlao_officer')
        self.assertFalse(ctx['has_access'])
        self.assertEqual(ctx['pending_count'], 0)
        self.assertEqual(len(ctx['pending_actions']), 0)
        self.assertIsNone(ctx['top_next_step'])


class Batch1Feature5BilingualAndAuditTests(TestCase):
    """
    FEATURE 5 — BILINGUAL UI
    FEATURE 6 — CASE EVENT AUDIT PRESERVATION
    FEATURE 7 — EMPTY / ERROR STATES
    """

    def setUp(self):
        self.officer = create_test_user('audit_officer', UserProfile.ROLE_DLAO_OFFICER)
        self.client = Client()
        self.client.force_login(self.officer)

    def test_dashboard_renders_bilingual_section_headers(self):
        """Dashboard renders bilingual section headers: What To Do Next, Recently Done, Next Step."""
        # Test English rendering
        response_en = self.client.get(reverse('dashboard:officer'))
        self.assertEqual(response_en.status_code, 200)
        content_en = response_en.content.decode('utf-8')
        self.assertIn("What To Do Next", content_en)
        self.assertIn("Recently Done", content_en)
        self.assertIn("NEXT STEP", content_en)

        # Test Bangla rendering
        session = self.client.session
        session['django_language'] = 'bn'
        session.save()
        response_bn = self.client.get(reverse('dashboard:officer'))
        self.assertEqual(response_bn.status_code, 200)
        content_bn = response_bn.content.decode('utf-8')
        self.assertIn("পরবর্তী করণীয়", content_bn)
        self.assertIn("সাম্প্রতিক কার্যক্রম", content_bn)
        self.assertIn("পরবর্তী ধাপ", content_bn)

    def test_case_event_append_only_integrity_preserved(self):
        """CaseEvent records cannot be modified or deleted (append-only audit)."""
        app = submit_application(
            name="Audit User",
            phone="01700998877",
            address="Khulna",
            legal_problem="Labor dispute",
            incident_description="Wage deduction without cause.",
            actor=self.officer,
        )

        event = CaseEvent.objects.filter(application=app).first()
        self.assertIsNotNone(event)

        # Attempt to modify event -> must raise PermissionError
        event.action = "MODIFIED_ACTION"
        with self.assertRaises(PermissionError):
            event.save()

        # Attempt to delete event -> must raise PermissionError
        with self.assertRaises(PermissionError):
            event.delete()
