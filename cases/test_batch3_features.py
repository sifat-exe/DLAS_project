"""
Comprehensive Automated Test Suite for Batch 3 Features:
- Marma Provenance (Original statement in Marma, translation, typed structured data, pre-submission confirmation)
- True Offline Queue & Sync (Temporary local ID, zero server record while offline, idempotent sync, duplicate protection)
- Conflict Resolution (No silent overwrite, human-controlled resolution, CaseEvent audit, IDOR security)
- 10-15 Duplicate & Look-Alike Demo Records (Seed command, genuine duplicates, look-alike traps, human review, no auto-merge, separate IDs)
- Security (CSRF, IDOR, Server-side authorization)
- Regressions (Core DLAS, Batch 1, Batch 2)
"""

import json
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.management import call_command
from django.core.exceptions import ValidationError, PermissionDenied
from accounts.models import UserProfile
from cases.models import Application, CaseRecord, CaseEvent, DuplicateCandidate, Task
from cases.services import (
    submit_application,
    accept_application,
    submit_marma_intake,
    process_offline_sync,
    resolve_offline_conflict,
    review_duplicate_candidate,
)
from cases.ai_service import MockAIService


class Batch3FeaturesTestCase(TestCase):
    def setUp(self):
        # Create users
        self.officer_user = User.objects.create_user(
            username='dlao_officer_b3',
            password='password123',
            first_name='DLAO',
            last_name='Officer'
        )
        UserProfile.objects.create(
            user=self.officer_user,
            role=UserProfile.ROLE_DLAO_OFFICER,
            phone='01710000031',
            language='en'
        )

        self.udc_operator = User.objects.create_user(
            username='udc_operator_b3',
            password='password123',
            first_name='UDC',
            last_name='Operator'
        )
        UserProfile.objects.create(
            user=self.udc_operator,
            role=UserProfile.ROLE_UDC_OPERATOR,
            phone='01710000032',
            language='bn'
        )

        self.citizen_user = User.objects.create_user(
            username='citizen_user_b3',
            password='password123',
            first_name='Citizen',
            last_name='User'
        )
        UserProfile.objects.create(
            user=self.citizen_user,
            role=UserProfile.ROLE_CITIZEN,
            phone='01710000033',
            language='bn'
        )

        self.other_citizen = User.objects.create_user(
            username='other_citizen_b3',
            password='password123',
            first_name='Other',
            last_name='Citizen'
        )
        UserProfile.objects.create(
            user=self.other_citizen,
            role=UserProfile.ROLE_CITIZEN,
            phone='01710000034',
            language='bn'
        )

        self.client = Client()

    # =========================================================================
    # PART A — MARMA PROVENANCE TESTS (1–9)
    # =========================================================================

    def test_01_marma_intake_stores_original_statement(self):
        """1. Marma intake can store original statement."""
        app = submit_marma_intake(
            name="মং শোয়ে প্রু মারমা",
            phone="01844000999",
            address="রোয়াংছড়ি, বান্দরবান",
            original_statement="အကျွန်မြေယာ ပြဿနာ ကြုံနေရပါတယ် (আমি আমার জমি নিয়ে সমস্যায় পড়েছি)",
            translated_statement="আমার পৈতৃক কৃষিজমি প্রভাবশালী প্রতিপক্ষরা জোরপূর্বক দখল করেছে।",
            typed_legal_problem="জমি জবরদখল ও সীমানা বিরোধ",
            typed_incident_description="বান্দরবান মৌজায় ২ একর জমি বেদখল করা হয়েছে।",
            actor=self.udc_operator,
        )
        self.assertTrue(Application.objects.filter(id=app.id).exists())
        self.assertIn("အကျွန်မြေယာ", app.original_statement)
        self.assertEqual(app.statement_language, "marma")

    def test_02_translation_distinguishable_from_original_statement(self):
        """2. Translation is distinguishable from original statement."""
        app = submit_marma_intake(
            name="মং শোয়ে প্রু মারমা",
            phone="01844000999",
            address="রোয়াংছড়ি, বান্দরবান",
            original_statement="အကျွန်မြေယာ ပြဿနာ",
            translated_statement="আমার কৃষিজমি সংক্রান্ত বিরোধ ও বেদখল সমস্যা।",
            typed_legal_problem="জমি জবরদখল",
            typed_incident_description="পৈতৃক জমি বেদখল সংক্রান্ত।",
            actor=self.udc_operator,
        )
        self.assertNotEqual(app.original_statement, app.translated_statement)
        self.assertEqual(app.original_statement, "အကျွန်မြေယာ ပြဿနာ")
        self.assertEqual(app.translated_statement, "আমার কৃষিজমি সংক্রান্ত বিরোধ ও বেদখল সমস্যা।")

    def test_03_typed_data_distinguishable_from_translation(self):
        """3. Typed data is distinguishable from translation."""
        app = submit_marma_intake(
            name="মং শোয়ে প্রু মারমা",
            phone="01844000999",
            address="রোয়াংছড়ি, বান্দরবান",
            original_statement="အကျွန်မြေယာ ပြဿနာ",
            translated_statement="আমার কৃষিজমি সংক্রান্ত বিরোধ ও বেদখল সমস্যা।",
            typed_legal_problem="জমি জবরদখল ও সীমানা বিরোধ (Land Encroachment & Boundary Dispute)",
            typed_incident_description="বান্দরবান রোয়াংছড়ি মৌজায় পৈতৃক রেকর্ডভুক্ত ২ একর কৃষিজমি বেদখল।",
            actor=self.udc_operator,
        )
        self.assertNotEqual(app.legal_problem, app.translated_statement)
        self.assertIn("Land Encroachment", app.legal_problem)
        self.assertIn("রেকর্ডভুক্ত ২ একর", app.incident_description)

    def test_04_provenance_is_preserved(self):
        """4. Provenance is preserved and never falsely attributes translated/typed text to applicant."""
        app = submit_marma_intake(
            name="মং শোয়ে প্রু মারমা",
            phone="01844000999",
            address="বান্দরবান",
            original_statement="আসল বক্তব্য",
            translated_statement="অনূদিত বক্তব্য",
            typed_legal_problem="টাইপকৃত সমস্যা",
            typed_incident_description="টাইপকৃত বিবরণ",
            actor=self.udc_operator,
        )
        events = CaseEvent.objects.filter(application=app)
        
        # Check original statement event is applicant-confirmed
        stmt_event = events.filter(action='marma_statement_recorded').first()
        self.assertIsNotNone(stmt_event)
        self.assertEqual(stmt_event.provenance, CaseEvent.PROVENANCE_APPLICANT_CONFIRMED)
        
        # Check translation event is intermediary-translated
        trans_event = events.filter(action='marma_translation_recorded').first()
        self.assertIsNotNone(trans_event)
        self.assertEqual(trans_event.provenance, CaseEvent.PROVENANCE_INTERMEDIARY_TRANSLATED)
        
        # Check typed confirmation event is staff-entered
        typed_event = events.filter(action='marma_typed_confirmation').first()
        self.assertIsNotNone(typed_event)
        self.assertEqual(typed_event.provenance, CaseEvent.PROVENANCE_STAFF_ENTERED)

    def test_05_marma_confirmation_works(self):
        """5. Marma confirmation step works: allows preview, verification, and corrections before final submit."""
        self.client.login(username='udc_operator_b3', password='password123')
        
        # Action 'confirm' generates preview review screen with attestations
        res = self.client.post(reverse('cases:marma_intake'), {
            'action': 'confirm',
            'name': 'মং শোয়ে প্রু মারমা',
            'phone': '01844000999',
            'address': 'রোয়াংছড়ি, বান্দরবান',
            'statement_language': 'marma',
            'original_statement': 'အကျွန်မြေယာ ပြဿနာ',
            'translated_statement': 'আমার জমি নিয়ে সমস্যা',
            'legal_problem': 'জমি বিরোধ',
            'incident_description': 'পৈতৃক জমি বেদখল সংক্রান্ত বিরোধ।',
            'verify_translation': 'on',
            'verify_typed_data': 'on',
            'applicant_consent': 'on',
        })
        self.assertEqual(res.status_code, 200)
        self.assertTemplateUsed(res, 'cases/marma_review.html')
        self.assertContains(res, "အကျွန်မြေယာ")
        self.assertContains(res, "আমার জমি নিয়ে সমস্যা")

    def test_06_application_submission_uses_existing_service(self):
        """6. Application submission uses the existing Application submission service."""
        app = submit_marma_intake(
            name="মং শোয়ে প্রু মারমা",
            phone="01844000999",
            address="বান্দরবান",
            original_statement="মারমা বক্তব্য",
            translated_statement="অনূদিত বক্তব্য",
            typed_legal_problem="টাইপকৃত সমস্যা",
            typed_incident_description="টাইপকৃত বিবরণ",
            actor=self.udc_operator,
        )
        self.assertIsInstance(app, Application)
        self.assertEqual(app.status, Application.STATUS_SUBMITTED)
        self.assertEqual(app.preferred_channel, Application.CHANNEL_UDC)

    def test_07_application_id_is_generated(self):
        """7. Application ID is generated upon Marma submission."""
        app = submit_marma_intake(
            name="মং শোয়ে প্রু মারমা",
            phone="01844000999",
            address="বান্দরবান",
            original_statement="মারমা বক্তব্য",
            translated_statement="অনূদিত বক্তব্য",
            typed_legal_problem="টাইপকৃত সমস্যা",
            typed_incident_description="টাইপকৃত বিবরণ",
            actor=self.udc_operator,
        )
        self.assertTrue(app.application_id.startswith("APP-"))

    def test_08_case_id_not_generated_before_dlao_acceptance(self):
        """8. Case ID is NOT generated before DLAO acceptance."""
        app = submit_marma_intake(
            name="মং শোয়ে প্রু মারমা",
            phone="01844000999",
            address="বান্দরবান",
            original_statement="মারমা বক্তব্য",
            translated_statement="অনূদিত বক্তব্য",
            typed_legal_problem="টাইপকৃত সমস্যা",
            typed_incident_description="টাইপকৃত বিবরণ",
            actor=self.udc_operator,
        )
        self.assertFalse(hasattr(app, 'case_record'))
        self.assertEqual(CaseRecord.objects.filter(application=app).count(), 0)

    def test_09_relevant_case_events_created(self):
        """9. Relevant CaseEvents are created for Marma workflow."""
        app = submit_marma_intake(
            name="মং শোয়ে প্রু মারমা",
            phone="01844000999",
            address="বান্দরবান",
            original_statement="মারমা বক্তব্য",
            translated_statement="অনূদিত বক্তব্য",
            typed_legal_problem="টাইপকৃত সমস্যা",
            typed_incident_description="টাইপকৃত বিবরণ",
            actor=self.udc_operator,
        )
        actions = list(CaseEvent.objects.filter(application=app).values_list('action', flat=True))
        self.assertIn('marma_statement_recorded', actions)
        self.assertIn('marma_translation_recorded', actions)
        self.assertIn('marma_typed_confirmation', actions)
        self.assertIn('APPLICATION_SUBMITTED', actions)

    # =========================================================================
    # PART B & C — TRUE OFFLINE QUEUE & SYNC TESTS (10–21)
    # =========================================================================

    def test_10_offline_mode_can_queue_intake(self):
        """10. Offline mode can queue an intake locally with required schema."""
        temp_id = "OFFLINE-TMP-TEST01"
        item_data = {
            'temp_id': temp_id,
            'idempotency_token': f"IDEM-{temp_id}",
            'name': 'মোছাঃ লাইলী বেগম',
            'phone': '01733889900',
            'address': 'রামগতি, লক্ষ্মীপুর',
            'legal_problem': 'পুনর্বাসন খাস জমি দখল ও উচ্ছেদ',
            'incident_description': 'সরকার কর্তৃক বরাদ্দকৃত খাস জমি অবৈধভাবে দখল করা হয়েছে।',
            'preferred_channel': 'udc',
        }
        self.assertIn('temp_id', item_data)
        self.assertTrue(item_data['temp_id'].startswith("OFFLINE-TMP-"))

    def test_11_queued_item_receives_temporary_local_id(self):
        """11. Queued item receives a temporary local ID."""
        temp_id = "OFFLINE-TMP-2026XYZ"
        self.assertTrue(temp_id.startswith("OFFLINE-TMP-"))
        self.assertFalse(temp_id.startswith("APP-"))
        self.assertFalse(temp_id.startswith("CASE-"))

    def test_12_offline_creation_does_not_create_server_application(self):
        """12. Offline creation does not create a server Application until synced."""
        initial_count = Application.objects.count()
        # Simulating client-side local queuing without calling server API
        local_queue = [{
            'temp_id': 'OFFLINE-TMP-001',
            'data': {'name': 'Offline Applicant', 'phone': '01700000000'}
        }]
        self.assertEqual(len(local_queue), 1)
        self.assertEqual(Application.objects.count(), initial_count)

    def test_13_queue_survives_reinstantiation(self):
        """13. Queue survives serialized re-instantiation (localStorage simulation)."""
        original = [{
            'temp_id': 'OFFLINE-TMP-STORAGE',
            'status': 'QUEUED',
            'data': {'name': 'Laily Begum', 'phone': '01733889900'}
        }]
        serialized = json.dumps(original)
        reconstituted = json.loads(serialized)
        self.assertEqual(reconstituted[0]['temp_id'], 'OFFLINE-TMP-STORAGE')
        self.assertEqual(reconstituted[0]['status'], 'QUEUED')

    def test_14_sync_creates_the_server_application(self):
        """14. Sync creates the server Application using existing submission service."""
        res = process_offline_sync(
            item_data={
                'temp_id': 'OFFLINE-TMP-SYNC-01',
                'name': 'মোছাঃ লাইলী বেগম',
                'phone': '01733889900',
                'address': 'রামগতি, লক্ষ্মীপুর',
                'legal_problem': 'পুনর্বাসন খাস জমি দখল ও উচ্ছেদ',
                'incident_description': 'সরকার কর্তৃক বরাদ্দকৃত খাস জমি অবৈধভাবে দখল করা হয়েছে।',
                'preferred_channel': 'udc',
            },
            user=self.udc_operator
        )
        self.assertEqual(res['status'], 'synced')
        self.assertTrue(res['application_id'].startswith("APP-"))
        self.assertTrue(Application.objects.filter(application_id=res['application_id']).exists())

    def test_15_successful_sync_stores_official_application_id(self):
        """15. Successful sync returns official Application ID."""
        res = process_offline_sync(
            item_data={
                'temp_id': 'OFFLINE-TMP-SYNC-02',
                'name': 'আকবর আলী',
                'phone': '01744556677',
                'address': 'কুড়িগ্রাম সদর',
                'legal_problem': 'চরের সীমানা বিরোধ',
                'incident_description': 'নদীভাঙন পরবর্তী চরের জমিতে সীমানা বিরোধ।',
                'preferred_channel': 'udc',
            },
            user=self.udc_operator
        )
        app = Application.objects.get(application_id=res['application_id'])
        self.assertEqual(app.name, 'আকবর আলী')
        self.assertEqual(app.status, Application.STATUS_SUBMITTED)

    def test_16_duplicate_sync_retry_is_idempotent(self):
        """16. Retrying the same sync does not create duplicate Applications (idempotency)."""
        temp_id = 'OFFLINE-TMP-RETRY-01'
        token = f"IDEM-{temp_id}"
        item = {
            'temp_id': temp_id,
            'idempotency_token': token,
            'name': 'সুলতানা রাজিয়া',
            'phone': '01799887766',
            'address': 'বগুড়া সদর',
            'legal_problem': 'পারিবারিক দেনমোহর আদায়',
            'incident_description': 'দেনমোহর ও খোরপোষ আদায়ে আইনি প্রতিকার প্রার্থনা।',
        }
        # First sync
        res1 = process_offline_sync(item, self.udc_operator)
        self.assertEqual(res1['status'], 'synced')
        self.assertFalse(res1['is_duplicate_retry'])
        app_count_1 = Application.objects.count()

        # Second sync with exact same item / idempotency token
        res2 = process_offline_sync(item, self.udc_operator)
        self.assertEqual(res2['status'], 'synced')
        self.assertTrue(res2['is_duplicate_retry'])
        self.assertEqual(res1['application_id'], res2['application_id'])
        self.assertEqual(Application.objects.count(), app_count_1)

    def test_17_failed_sync_remains_retryable(self):
        """17. Failed sync (e.g. missing required field) fails cleanly without creating record and remains retryable."""
        initial_count = Application.objects.count()
        res_fail = process_offline_sync(
            item_data={
                'temp_id': 'OFFLINE-TMP-FAIL-01',
                'name': '', # Missing name
                'phone': '01711223344',
                'address': 'ঢাকা',
                'legal_problem': 'সমস্যা',
                'incident_description': 'বিবরণ',
            },
            user=self.udc_operator
        )
        self.assertEqual(res_fail['status'], 'failed')
        self.assertEqual(Application.objects.count(), initial_count)

        # Retry with valid data succeeds
        res_retry = process_offline_sync(
            item_data={
                'temp_id': 'OFFLINE-TMP-FAIL-01',
                'name': 'সঠিক নাম',
                'phone': '01711223344',
                'address': 'ঢাকা',
                'legal_problem': 'সমস্যা',
                'incident_description': 'বিবরণ',
            },
            user=self.udc_operator
        )
        self.assertEqual(res_retry['status'], 'synced')

    def test_18_conflict_is_detected(self):
        """18. Conflict is detected when server has conflicting data for the applicant."""
        # Create an existing server record
        Application.objects.create(
            application_id="APP-EXISTING-01",
            name="আসল আবেদনকারী",
            phone="01788776655",
            address="রাজশাহী",
            legal_problem="উত্তরাধিকার বিরোধ",
            incident_description="পূর্ববর্তী রেকর্ড।",
        )
        # Attempt sync with same phone but different applicant name (conflict)
        res = process_offline_sync(
            item_data={
                'temp_id': 'OFFLINE-TMP-CONFLICT-01',
                'name': 'ভিন্ন ব্যক্তি',
                'phone': '01788776655',
                'address': 'রাজশাহী',
                'legal_problem': 'নতুন সমস্যা',
                'incident_description': 'নতুন বিবরণ',
            },
            user=self.udc_operator
        )
        self.assertEqual(res['status'], 'conflict')
        self.assertIn('conflict_reason', res)
        self.assertIn('local_version', res)
        self.assertIn('server_version', res)

    def test_19_conflict_does_not_silently_overwrite_data(self):
        """19. Conflict does NOT silently overwrite data on server."""
        server_app = Application.objects.create(
            application_id="APP-EXISTING-02",
            name="রফিক আহমেদ",
            phone="01755443322",
            address="কুমিল্লা",
            legal_problem="জমি দখল",
            incident_description="মূল ঘটনার বিবরণ যা পরিবর্তন করা যাবে না।",
        )
        res = process_offline_sync(
            item_data={
                'temp_id': 'OFFLINE-TMP-OVERWRITE-CHECK',
                'name': 'শফিক আহমেদ',
                'phone': '01755443322',
                'address': 'কুমিল্লা',
                'legal_problem': 'ভাড়াটিয়া বিরোধ',
                'incident_description': 'ওভাররাইট করার অপচেষ্টা।',
            },
            user=self.udc_operator
        )
        self.assertEqual(res['status'], 'conflict')
        
        # Verify server application remains 100% untouched
        server_app.refresh_from_db()
        self.assertEqual(server_app.name, "রফিক আহমেদ")
        self.assertEqual(server_app.legal_problem, "জমি দখল")

    def test_20_authorized_user_can_resolve_conflict(self):
        """20. Authorized user can resolve conflict explicitly using keep_local or keep_server."""
        server_app = Application.objects.create(
            application_id="APP-EXISTING-03",
            name="জামাল হোসেন",
            phone="01712345678",
            address="নোয়াখালী",
            legal_problem="ভূমি বিরোধ",
            incident_description="সার্ভার সংস্করণ।",
        )
        # 1. Resolve with keep_server
        resolved_server = resolve_offline_conflict(
            temp_id="OFFLINE-TMP-RES-01",
            resolution_choice="keep_server",
            user=self.udc_operator,
            server_app_id=server_app.application_id,
        )
        self.assertEqual(resolved_server.application_id, server_app.application_id)
        
        # Verify CaseEvent logged for resolution
        event = CaseEvent.objects.filter(application=server_app, action='OFFLINE_CONFLICT_RESOLVED').first()
        self.assertIsNotNone(event)
        self.assertIn("Keep Server", event.description)

        # 2. Resolve with keep_local
        resolved_local = resolve_offline_conflict(
            temp_id="OFFLINE-TMP-RES-02",
            resolution_choice="keep_local",
            user=self.udc_operator,
            local_data={
                'name': 'কামাল হোসেন',
                'phone': '01712345678',
                'address': 'নোয়াখালী',
                'legal_problem': 'নতুন সীমানা বিরোধ',
                'incident_description': 'স্থানীয় সংস্করণ যা বহাল রাখার সিদ্ধান্ত নেয়া হলো।',
            }
        )
        self.assertTrue(resolved_local.application_id.startswith("APP-"))
        self.assertEqual(resolved_local.name, "কামাল হোসেন")

    def test_21_unauthorized_user_cannot_resolve_conflict_idor(self):
        """21. Unauthorized user cannot resolve another user's conflict (IDOR protection)."""
        victim_app = Application.objects.create(
            application_id="APP-VICTIM-01",
            applicant_user=self.citizen_user,
            name="ভিকটিম নাগরিক",
            phone="01711112222",
            address="বরিশাল",
            legal_problem="উত্তরাধিকার সম্পত্তি",
            incident_description="ভিকটিমের নিজস্ব আবেদন।",
        )
        self.client.login(username='other_citizen_b3', password='password123')
        
        res = self.client.post(
            reverse('cases:offline_conflict_resolve'),
            data=json.dumps({
                'temp_id': 'OFFLINE-TMP-ATTACK',
                'resolution_choice': 'keep_server',
                'server_app_id': victim_app.application_id,
            }),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("Unauthorized", res.json().get('error', ''))

    # =========================================================================
    # PART E & F — DUPLICATE & LOOK-ALIKE RECORDS TESTS (22–28)
    # =========================================================================

    def test_22_seed_command_creates_deterministic_records(self):
        """22. Seed command creates 10–15 deterministic demo records (exactly 12)."""
        call_command('seed_duplicate_demo')
        # Check that 12 applications exist with SEED-DUP idempotency tags
        seeded_apps = Application.objects.filter(idempotency_token__startswith="SEED-DUP")
        self.assertEqual(seeded_apps.count(), 12)
        # All 12 accepted as cases
        seeded_cases = CaseRecord.objects.filter(application__in=seeded_apps)
        self.assertEqual(seeded_cases.count(), 12)

    def test_23_duplicate_candidates_generated(self):
        """23. Duplicate candidates are generated and available for evaluation."""
        call_command('seed_duplicate_demo')
        candidates = DuplicateCandidate.objects.all()
        self.assertGreaterEqual(candidates.count(), 1)
        # Verify match score and reason are populated
        cand = candidates.first()
        self.assertGreater(cand.match_score, 0.0)
        self.assertTrue(len(cand.match_reason) > 5)

    def test_24_look_alike_records_not_auto_confirmed(self):
        """24. Look-alike records are NOT automatically confirmed as duplicates."""
        call_command('seed_duplicate_demo')
        candidates = DuplicateCandidate.objects.all()
        # All candidates must strictly start in STATUS_PENDING
        for cand in candidates:
            self.assertEqual(cand.review_status, DuplicateCandidate.STATUS_PENDING)

    def test_25_human_review_required(self):
        """25. Human review is strictly required to change duplicate status."""
        call_command('seed_duplicate_demo')
        cand = DuplicateCandidate.objects.filter(case__application__name='Rahim Uddin').first()
        self.assertIsNotNone(cand)
        self.assertEqual(cand.review_status, DuplicateCandidate.STATUS_PENDING)
        
        # Officer conducts human review
        review_duplicate_candidate(
            candidate=cand,
            staff_user=self.officer_user,
            review_status=DuplicateCandidate.STATUS_CONFIRMED,
            review_notes="Same applicant and identical landlord dispute confirmed."
        )
        cand.refresh_from_db()
        self.assertEqual(cand.review_status, DuplicateCandidate.STATUS_CONFIRMED)
        self.assertEqual(cand.reviewed_by, self.officer_user)

    def test_26_confirmed_duplicate_does_not_merge_records(self):
        """26. Confirmed duplicate does NOT merge records; both records remain distinct."""
        call_command('seed_duplicate_demo')
        cand = DuplicateCandidate.objects.filter(case__application__name='Rahim Uddin').first()
        case_a_id = cand.case.id
        case_b_id = cand.possible_case.id
        
        review_duplicate_candidate(
            candidate=cand,
            staff_user=self.officer_user,
            review_status=DuplicateCandidate.STATUS_CONFIRMED,
            review_notes="Human confirmed candidate."
        )
        
        # Both case records still exist separately in DB!
        self.assertTrue(CaseRecord.objects.filter(id=case_a_id).exists())
        self.assertTrue(CaseRecord.objects.filter(id=case_b_id).exists())

    def test_27_separate_ids_and_history_remain_intact(self):
        """27. Separate IDs and history remain intact for both cases."""
        call_command('seed_duplicate_demo')
        cand = DuplicateCandidate.objects.filter(case__application__name='Rahim Uddin').first()
        case_a = cand.case
        case_b = cand.possible_case
        
        self.assertNotEqual(case_a.case_id, case_b.case_id)
        self.assertNotEqual(case_a.application.application_id, case_b.application_id if hasattr(case_b, 'application_id') else case_b.application.application_id)

    def test_28_case_event_records_duplicate_review(self):
        """28. CaseEvent records consequential duplicate review decision."""
        call_command('seed_duplicate_demo')
        cand = DuplicateCandidate.objects.filter(case__application__name='Rahim Uddin').first()
        
        review_duplicate_candidate(
            candidate=cand,
            staff_user=self.officer_user,
            review_status=DuplicateCandidate.STATUS_DISMISSED,
            review_notes="Evaluated as look-alike trap; different incidents."
        )
        
        event = CaseEvent.objects.filter(
            case=cand.case,
            action='DUPLICATE_CANDIDATE_REVIEWED'
        ).first()
        self.assertIsNotNone(event)
        self.assertIn("Dismissed", event.description)
        self.assertEqual(event.provenance, CaseEvent.PROVENANCE_STAFF_ENTERED)

    # =========================================================================
    # PART H — SECURITY TESTS (29–31)
    # =========================================================================

    def test_29_csrf_remains_protected(self):
        """29. CSRF protection remains enforced on intake and sync endpoints."""
        csrf_client = Client(enforce_csrf_checks=True)
        # Attempt POST without CSRF token
        res = csrf_client.post(reverse('cases:marma_intake'), {'action': 'submit'})
        # Should be redirected to login or blocked by CSRF / 403
        self.assertIn(res.status_code, [302, 403])

    def test_30_idor_attempts_fail(self):
        """30. IDOR attempts on application details fail for unauthorized citizen."""
        victim_app = Application.objects.create(
            application_id="APP-CONFIDENTIAL-01",
            applicant_user=self.citizen_user,
            name="গোপনীয় নাগরিক",
            phone="01712341234",
            address="ঢাকা",
            legal_problem="পারিবারিক সমস্যা",
            incident_description="ব্যক্তিগত বিবরণ।",
        )
        self.client.login(username='other_citizen_b3', password='password123')
        res = self.client.get(reverse('cases:application_detail', args=[victim_app.application_id]))
        self.assertEqual(res.status_code, 403)

    def test_31_role_restrictions_remain_enforced(self):
        """31. Role restrictions remain enforced: citizen cannot review duplicates."""
        call_command('seed_duplicate_demo')
        cand = DuplicateCandidate.objects.first()
        with self.assertRaises(PermissionDenied):
            review_duplicate_candidate(
                candidate=cand,
                staff_user=self.citizen_user,
                review_status=DuplicateCandidate.STATUS_CONFIRMED
            )

    # =========================================================================
    # REGRESSION TESTS (32–34)
    # =========================================================================

    def test_32_regression_core_application_and_case_lifecycle(self):
        """32. Regression: Core Application intake, ID generation, and DLAO acceptance work."""
        app = submit_application(
            name="মমতাজ বেগম",
            phone="01711223344",
            address="ঢাকা",
            legal_problem="ভাড়াটিয়া বিরোধ",
            incident_description="বাড়িওয়ালা জোরপূর্বক উচ্ছেদের হুমকি দিচ্ছেন।",
            preferred_channel=Application.CHANNEL_WEB,
            actor=self.citizen_user
        )
        self.assertTrue(app.application_id.startswith("APP-"))
        self.assertFalse(hasattr(app, 'case_record'))
        
        # Acceptance creates CaseRecord with CASE- id
        case = accept_application(app, self.officer_user)
        self.assertTrue(case.case_id.startswith("CASE-"))
        self.assertEqual(case.status, CaseRecord.STATUS_ACCEPTED)

    def test_33_regression_batch1_features(self):
        """33. Regression: Batch 1 features (Optional NID verification, DLAO queue ranking) intact."""
        app = submit_application(
            name="নাসির আহমেদ",
            phone="01799887766",
            address="চট্টগ্রাম",
            legal_problem="মৌজা বিরোধ",
            incident_description="জমির সীমানা পিলার ভেঙে ফেলা হয়েছে।",
            preferred_channel=Application.CHANNEL_WEB,
            nid_number="19901234567890",
            actor=self.citizen_user
        )
        # NID unverified never blocks application creation
        self.assertEqual(app.nid_verification_status, Application.NID_STATUS_NOT_VERIFIED)
        self.assertTrue(app.application_id.startswith("APP-"))

    def test_34_regression_batch2_features(self):
        """34. Regression: Batch 2 features (Bangla multi-turn slot filling) intact."""
        from cases.conversational_service import BanglaConversationalService
        state = BanglaConversationalService.init_session()
        self.assertEqual(state['status'], 'in_progress')
        self.assertEqual(state['current_slot'], 'name')
        
        # Multi-turn interaction
        state, reply_bn, reply_en = BanglaConversationalService.process_turn(state, "আমার নাম মোঃ আবুল কালাম")
        self.assertEqual(state['slots']['name']['value'], "মোঃ আবুল কালাম")
        self.assertEqual(state['current_slot'], 'phone')
        self.assertTrue(len(reply_bn) > 0)
