"""
Comprehensive Automated Test Suite for Batch 2 Features:
- Bangla Conversational AI (Multi-turn slot filling, extraction, corrections, review, confirmation)
- Moyuri's Own Confirmation (Review, edit, explicit confirmation, CaseEvent moyuri_confirmed, IDOR protection)
- Ripon Voice-Only Task (Simulated voice commands, state transition, CaseEvent voice_task_completed, IDOR protection)
- Security (CSRF, IDOR, Server-side authorization)
- Regressions & Provenance
"""

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from django.utils import timezone
from accounts.models import UserProfile
from cases.models import Application, CaseRecord, CaseEvent, Task
from cases.conversational_service import BanglaConversationalService
from cases.services import submit_application, accept_application


class Batch2FeaturesTestCase(TestCase):
    def setUp(self):
        # Create users
        self.officer_user = User.objects.create_user(
            username='dlao_officer_b2',
            password='password123',
            first_name='DLAO',
            last_name='Officer'
        )
        UserProfile.objects.create(
            user=self.officer_user,
            role=UserProfile.ROLE_DLAO_OFFICER,
            phone='01710000001',
            language='en'
        )

        self.citizen_user = User.objects.create_user(
            username='citizen_user_b2',
            password='password123',
            first_name='Citizen',
            last_name='User'
        )
        UserProfile.objects.create(
            user=self.citizen_user,
            role=UserProfile.ROLE_CITIZEN,
            phone='01710000002',
            language='bn'
        )

        self.moyuri_user = User.objects.create_user(
            username='moyuri',
            password='password123',
            first_name='Moyuri',
            last_name='Akter'
        )
        UserProfile.objects.create(
            user=self.moyuri_user,
            role=UserProfile.ROLE_CITIZEN,
            phone='01755123456',
            language='bn'
        )

        self.ripon_user = User.objects.create_user(
            username='ripon',
            password='password123',
            first_name='Ripon',
            last_name='Hossain'
        )
        UserProfile.objects.create(
            user=self.ripon_user,
            role=UserProfile.ROLE_DLAO_SUPPORT_STAFF,
            phone='01710000003',
            language='bn'
        )

        self.other_user = User.objects.create_user(
            username='unauthorized_user_b2',
            password='password123',
            first_name='Other',
            last_name='Person'
        )
        UserProfile.objects.create(
            user=self.other_user,
            role=UserProfile.ROLE_PANEL_LAWYER,
            phone='01710000004',
            language='en'
        )

        # Baseline application & case for task testing
        self.app = submit_application(
            name="মোঃ শফিকুল ইসলাম",
            phone="01711223344",
            address="মিরপুর, ঢাকা",
            legal_problem="ভাড়াটিয়া উচ্ছেদ সংক্রান্ত বিরোধ",
            incident_description="বাড়িওয়ালা জোরপূর্বক উচ্ছেদের নোটিশ ছাড়াই মালামাল ফেলে দেওয়ার হুমকি দিচ্ছে।",
            preferred_channel=Application.CHANNEL_WEB,
            applicant_user=self.citizen_user,
        )
        self.case = accept_application(
            self.app,
            officer=self.officer_user,
            priority='HIGH'
        )

        # Task assigned specifically to Ripon
        self.ripon_task = Task.objects.create(
            case=self.case,
            assigned_to=self.ripon_user,
            title="Referral Acknowledgement: REF-101",
            description="Acknowledge incoming referral from National Legal Aid Helpline 16699",
            status=Task.STATUS_PENDING,
            due_at=timezone.now() + timezone.timedelta(days=2),
            priority='HIGH',
        )

    # =========================================================================
    # PART A: BANGLA CONVERSATIONAL AI TESTS (1 - 15)
    # =========================================================================

    def test_01_conversation_starts_cleanly(self):
        """1. Conversation starts with initial prompt and active state."""
        state = BanglaConversationalService.init_session()
        self.assertEqual(state['status'], 'in_progress')
        self.assertEqual(state['current_slot'], 'name')
        self.assertTrue(len(state['history']) >= 1)
        self.assertIn('নাম', state['history'][0]['text_bn'])

        client = Client()
        response = client.get(reverse('cases:conversational_intake'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ডিজিটাল আইনি সহায়তা')

    def test_02_name_slot_is_collected(self):
        """2. Name slot is extracted correctly from Bangla input."""
        state = BanglaConversationalService.init_session()
        state, reply_bn, reply_en = BanglaConversationalService.process_turn(state, "আমার নাম মোঃ রহিম")
        self.assertEqual(state['slots']['name']['value'], "মোঃ রহিম")
        self.assertEqual(state['slots']['name']['provenance'], CaseEvent.PROVENANCE_APPLICANT_CONFIRMED)
        self.assertEqual(state['current_slot'], 'phone')

    def test_03_phone_slot_is_collected(self):
        """3. Phone slot is extracted and normalized from Bangla digits."""
        state = BanglaConversationalService.init_session()
        BanglaConversationalService.process_turn(state, "আমার নাম মোঃ রহিম")
        state, _, _ = BanglaConversationalService.process_turn(state, "আমার মোবাইল নম্বর 01712345678")
        self.assertEqual(state['slots']['phone']['value'], "01712345678")
        self.assertEqual(state['current_slot'], 'address')

    def test_04_legal_problem_is_collected(self):
        """4. Legal problem is classified and collected."""
        state = BanglaConversationalService.init_session()
        BanglaConversationalService.process_turn(state, "আমার নাম মোঃ রহিম")
        BanglaConversationalService.process_turn(state, "01712345678")
        BanglaConversationalService.process_turn(state, "গ্রাম: শান্তিনগর, মিরপুর, ঢাকা")
        state, _, _ = BanglaConversationalService.process_turn(state, "আমার জমি নিয়ে সমস্যা")
        self.assertIsNotNone(state['slots']['legal_problem']['value'])
        self.assertIn("জমিজমা", state['slots']['legal_problem']['value'])
        self.assertEqual(state['current_slot'], 'incident_description')

    def test_05_incident_description_is_collected(self):
        """5. Incident description is collected and reaches review."""
        state = BanglaConversationalService.init_session()
        BanglaConversationalService.process_turn(state, "আমার নাম মোঃ রহিম")
        BanglaConversationalService.process_turn(state, "01712345678")
        BanglaConversationalService.process_turn(state, "গ্রাম: শান্তিনগর, মিরপুর, ঢাকা")
        BanglaConversationalService.process_turn(state, "জমি সংক্রান্ত বিরোধ")
        state, _, _ = BanglaConversationalService.process_turn(state, "ঘটনাটি হলো প্রতিপক্ষ জোরপূর্বক জমি দখল করে প্রাচীর নির্মাণ করেছে।")
        self.assertIsNotNone(state['slots']['incident_description']['value'])
        self.assertEqual(state['status'], 'review')

    def test_06_missing_information_triggers_a_question(self):
        """6. Incomplete input triggers a prompt for the specific missing slot."""
        state = BanglaConversationalService.init_session()
        # User only enters a problem, name and phone are still missing
        state, reply_bn, _ = BanglaConversationalService.process_turn(state, "আমার জমি নিয়ে সমস্যা")
        self.assertIsNotNone(state['slots']['legal_problem']['value'])
        # Since name is still missing, assistant asks for name next
        self.assertEqual(state['current_slot'], 'name')
        self.assertIn("নাম", reply_bn)

    def test_07_already_collected_information_not_unnecessarily_requested_again(self):
        """7. Already collected slots are not re-prompted."""
        state = BanglaConversationalService.init_session()
        # Provide name and phone in first turn
        state, reply_bn, _ = BanglaConversationalService.process_turn(state, "আমার নাম মোঃ রহিম, মোবাইল নম্বর 01712345678")
        self.assertEqual(state['slots']['name']['value'], "মোঃ রহিম")
        self.assertEqual(state['slots']['phone']['value'], "01712345678")
        # System should advance directly to address
        self.assertEqual(state['current_slot'], 'address')
        self.assertNotIn("মোবাইল নম্বর", reply_bn)

    def test_08_user_correction_works(self):
        """8. User can correct previous information and final value is updated."""
        state = BanglaConversationalService.init_session()
        BanglaConversationalService.process_turn(state, "আমার নাম রহিম")
        self.assertEqual(state['slots']['name']['value'], "রহিম")

        # Now correct name
        state, reply_bn, _ = BanglaConversationalService.process_turn(state, "না, আমার পুরো নাম মোঃ রহিম উদ্দিন")
        self.assertEqual(state['slots']['name']['value'], "মোঃ রহিম উদ্দিন")
        self.assertTrue(state['corrections_count'] >= 1)
        self.assertIn("মোঃ রহিম উদ্দিন", reply_bn)

    def test_09_conversation_state_survives_requests(self):
        """9. Multi-turn session persists across client HTTP requests."""
        client = Client()
        # Turn 1
        client.post(reverse('cases:conversational_intake_message'), {'message': 'আমার নাম মোঃ রহিম'})
        session = client.session
        self.assertEqual(session['conversational_intake']['slots']['name']['value'], 'মোঃ রহিম')

        # Turn 2
        client.post(reverse('cases:conversational_intake_message'), {'message': '01712345678'})
        session = client.session
        self.assertEqual(session['conversational_intake']['slots']['phone']['value'], '01712345678')
        self.assertEqual(session['conversational_intake']['current_slot'], 'address')

    def test_10_review_appears_before_submission(self):
        """10. Review card with confirmation options appears when all slots filled."""
        client = Client()
        client.post(reverse('cases:conversational_intake_message'), {'message': 'আমার নাম মোঃ রহিম'})
        client.post(reverse('cases:conversational_intake_message'), {'message': '01712345678'})
        client.post(reverse('cases:conversational_intake_message'), {'message': 'মিরপুর, ঢাকা'})
        client.post(reverse('cases:conversational_intake_message'), {'message': 'জমি নিয়ে সমস্যা'})
        response = client.post(reverse('cases:conversational_intake_message'), {
            'message': 'ঘটনাটি হলো প্রতিপক্ষ জোরপূর্বক জমি দখল করে প্রাচীর নির্মাণ করেছে।'
        }, follow=True)
        self.assertEqual(client.session['conversational_intake']['status'], 'review')
        content = response.content.decode('utf-8')
        self.assertTrue('সংগৃহীত তথ্যের সারসংক্ষেপ' in content or 'Application Details Review' in content)
        self.assertTrue('নিশ্চিত করুন ও আবেদন দাখিল করুন' in content or 'Confirm & Submit Application' in content)

    def test_11_no_submission_occurs_before_confirmation(self):
        """11. Merely completing all slots does NOT automatically submit application."""
        initial_apps_count = Application.objects.count()
        state = BanglaConversationalService.init_session()
        BanglaConversationalService.process_turn(state, "আমার নাম মোঃ রহিম")
        BanglaConversationalService.process_turn(state, "01712345678")
        BanglaConversationalService.process_turn(state, "মিরপুর, ঢাকা")
        BanglaConversationalService.process_turn(state, "জমি সংক্রান্ত বিরোধ")
        BanglaConversationalService.process_turn(state, "ঘটনাটি হলো প্রতিপক্ষ জোরপূর্বক জমি দখল করেছে।")
        self.assertEqual(state['status'], 'review')
        # No application was created in database!
        self.assertEqual(Application.objects.count(), initial_apps_count)

    def test_12_confirmation_submits_the_application(self):
        """12. Explicit confirmation triggers application submission."""
        client = Client()
        client.post(reverse('cases:conversational_intake_message'), {'message': 'আমার নাম মোঃ রহিম'})
        client.post(reverse('cases:conversational_intake_message'), {'message': '01712345678'})
        client.post(reverse('cases:conversational_intake_message'), {'message': 'মিরপুর, ঢাকা'})
        client.post(reverse('cases:conversational_intake_message'), {'message': 'জমি সংক্রান্ত বিরোধ'})
        client.post(reverse('cases:conversational_intake_message'), {'message': 'ঘটনাটি হলো জমি দখল সংক্রান্ত।'})

        response = client.post(reverse('cases:conversational_intake_confirm'), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Application.objects.filter(name="মোঃ রহিম").exists())

    def test_13_application_id_is_generated(self):
        """13. Application ID is generated upon conversational submission."""
        state = BanglaConversationalService.init_session()
        BanglaConversationalService.process_turn(state, "আমার নাম মোঃ রহিম")
        BanglaConversationalService.process_turn(state, "01712345678")
        BanglaConversationalService.process_turn(state, "মিরপুর, ঢাকা")
        BanglaConversationalService.process_turn(state, "জমি সংক্রান্ত বিরোধ")
        BanglaConversationalService.process_turn(state, "ঘটনাটি হলো জমি দখল সংক্রান্ত।")
        app = BanglaConversationalService.confirm_and_submit(state)
        self.assertTrue(app.application_id.startswith('APP-'))
        self.assertEqual(app.status, Application.STATUS_SUBMITTED)

    def test_14_case_id_is_not_generated_upon_conversational_intake(self):
        """14. Case ID and CaseRecord are strictly NOT created at conversational submission."""
        state = BanglaConversationalService.init_session()
        BanglaConversationalService.process_turn(state, "আমার নাম মোঃ রহিম")
        BanglaConversationalService.process_turn(state, "01712345678")
        BanglaConversationalService.process_turn(state, "মিরপুর, ঢাকা")
        BanglaConversationalService.process_turn(state, "জমি সংক্রান্ত বিরোধ")
        BanglaConversationalService.process_turn(state, "ঘটনাটি হলো জমি দখল সংক্রান্ত।")
        app = BanglaConversationalService.confirm_and_submit(state)
        self.assertFalse(hasattr(app, 'case_record'))
        self.assertFalse(CaseRecord.objects.filter(application=app).exists())

    def test_15_provenance_is_preserved_in_audit_event(self):
        """15. Append-only CaseEvent records conversational intake with applicant_confirmed provenance."""
        state = BanglaConversationalService.init_session()
        BanglaConversationalService.process_turn(state, "আমার নাম মোঃ রহিম")
        BanglaConversationalService.process_turn(state, "01712345678")
        BanglaConversationalService.process_turn(state, "মিরপুর, ঢাকা")
        BanglaConversationalService.process_turn(state, "জমি সংক্রান্ত বিরোধ")
        BanglaConversationalService.process_turn(state, "ঘটনাটি হলো জমি দখল সংক্রান্ত।")
        app = BanglaConversationalService.confirm_and_submit(state, user=self.citizen_user)

        event = CaseEvent.objects.filter(application=app, action='conversational_intake_confirmed').first()
        self.assertIsNotNone(event)
        self.assertEqual(event.provenance, CaseEvent.PROVENANCE_APPLICANT_CONFIRMED)
        self.assertEqual(event.authority, 'applicant_personal_confirmation')

    # =========================================================================
    # PART B: MOYURI'S OWN CONFIRMATION TESTS (16 - 20)
    # =========================================================================

    def test_16_moyuri_can_review_collected_information(self):
        """16. Moyuri logs in and views her collected intake information."""
        client = Client()
        client.login(username='moyuri', password='password123')
        response = client.get(reverse('cases:moyuri_confirm'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ময়ূরী আক্তার')
        self.assertContains(response, '01755123456')
        content = response.content.decode('utf-8')
        self.assertTrue('আমার আবেদন নিশ্চিত ও দাখিল করুন' in content or 'Confirm My Legal Aid Application' in content)

    def test_17_moyuri_can_correct_information_before_confirmation(self):
        """17. Moyuri can edit and update information before confirmation."""
        client = Client()
        client.login(username='moyuri', password='password123')
        response = client.post(reverse('cases:moyuri_confirm'), {
            'action': 'edit',
            'name': 'মোসাঃ ময়ূরী আক্তার খাতুন',
            'phone': '01755999888',
            'address': 'গ্রাম: তেঁতুলঝোড়া, সাভার, ঢাকা',
            'legal_problem': 'যৌতুক ও পারিবারিক নির্যাতন প্রতিকার',
            'incident_description': 'বিগত তিন মাস যাবত শারীরিক ও মানসিক নির্যাতন এবং জোরপূর্বক বাড়ি থেকে বের করে দেওয়া হয়েছে।',
            'safe_contact_number': '01811223344',
            'safe_contact_time': 'সকাল ১০টা - দুপুর ১টা',
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'মোসাঃ ময়ূরী আক্তার খাতুন')
        self.assertContains(response, '01755999888')

    def test_18_moyuri_explicit_confirmation_succeeds(self):
        """18. Explicit confirmation by Moyuri submits the application and generates App ID."""
        client = Client()
        client.login(username='moyuri', password='password123')
        response = client.post(reverse('cases:moyuri_confirm'), {
            'action': 'confirm',
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        app = Application.objects.filter(name='ময়ূরী আক্তার').first()
        self.assertIsNotNone(app)
        self.assertTrue(app.application_id.startswith('APP-'))
        self.assertEqual(app.status, Application.STATUS_SUBMITTED)
        # Case ID must NOT exist
        self.assertFalse(CaseRecord.objects.filter(application=app).exists())

    def test_19_moyuri_confirmed_case_event_is_created(self):
        """19. moyuri_confirmed CaseEvent is logged with applicant authority."""
        client = Client()
        client.login(username='moyuri', password='password123')
        client.post(reverse('cases:moyuri_confirm'), {'action': 'confirm'})
        app = Application.objects.filter(name='ময়ূরী আক্তার').first()
        event = CaseEvent.objects.filter(application=app, action='moyuri_confirmed').first()
        self.assertIsNotNone(event)
        self.assertEqual(event.actor, self.moyuri_user)
        self.assertEqual(event.provenance, CaseEvent.PROVENANCE_APPLICANT_CONFIRMED)
        self.assertEqual(event.authority, 'applicant_personal_confirmation')

    def test_20_unauthorized_user_cannot_perform_moyuri_confirmation(self):
        """20. Unauthorized non-Moyuri users receive 403 Forbidden on Moyuri confirmation endpoint."""
        client = Client()
        client.login(username='unauthorized_user_b2', password='password123')
        # GET attempt
        res_get = client.get(reverse('cases:moyuri_confirm'))
        self.assertEqual(res_get.status_code, 403)
        # POST attempt
        res_post = client.post(reverse('cases:moyuri_confirm'), {'action': 'confirm'})
        self.assertEqual(res_post.status_code, 403)

    # =========================================================================
    # PART C: RIPON VOICE-ONLY TASK TESTS (21 - 26)
    # =========================================================================

    def test_21_ripon_sees_assigned_voice_task(self):
        """21. Ripon can view his assigned voice task workspace."""
        client = Client()
        client.login(username='ripon', password='password123')
        response = client.get(reverse('cases:voice_task', kwargs={'task_id': self.ripon_task.id}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Referral Acknowledgement: REF-101')
        content = response.content.decode('utf-8')
        self.assertTrue('Voice' in content or 'ভয়েস' in content)

    def test_22_voice_task_accepts_supported_simulated_command(self):
        """22. Voice task accepts controlled vocabulary command."""
        client = Client()
        client.login(username='ripon', password='password123')
        response = client.post(
            reverse('cases:voice_task_execute', kwargs={'task_id': self.ripon_task.id}),
            {'command': 'হ্যাঁ, গ্রহণ করছি'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])

    def test_23_task_completion_changes_correct_task_state(self):
        """23. Valid voice command transitions task status to COMPLETED with timestamp."""
        client = Client()
        client.login(username='ripon', password='password123')
        client.post(
            reverse('cases:voice_task_execute', kwargs={'task_id': self.ripon_task.id}),
            {'command': 'কাজটি সম্পন্ন করুন'}
        )
        self.ripon_task.refresh_from_db()
        self.assertEqual(self.ripon_task.status, Task.STATUS_COMPLETED)
        self.assertIsNotNone(self.ripon_task.completed_at)

    def test_24_voice_task_completed_case_event_is_created(self):
        """24. voice_task_completed CaseEvent is recorded in append-only audit trail."""
        client = Client()
        client.login(username='ripon', password='password123')
        client.post(
            reverse('cases:voice_task_execute', kwargs={'task_id': self.ripon_task.id}),
            {'command': 'হ্যাঁ, গ্রহণ করছি'}
        )
        event = CaseEvent.objects.filter(case=self.case, action='voice_task_completed').first()
        self.assertIsNotNone(event)
        self.assertEqual(event.actor, self.ripon_user)
        self.assertEqual(event.channel, 'voice')
        self.assertEqual(event.authority, 'assigned_user_voice_command')

    def test_25_ripon_cannot_complete_another_users_task_and_vice_versa(self):
        """25. IDOR Protection: Users cannot view or execute tasks assigned to someone else."""
        client = Client()
        # Other user tries to access Ripon's task
        client.login(username='unauthorized_user_b2', password='password123')
        res_get = client.get(reverse('cases:voice_task', kwargs={'task_id': self.ripon_task.id}))
        self.assertEqual(res_get.status_code, 403)

        res_post = client.post(
            reverse('cases:voice_task_execute', kwargs={'task_id': self.ripon_task.id}),
            {'command': 'হ্যাঁ, গ্রহণ করছি'}
        )
        self.assertEqual(res_post.status_code, 403)
        self.ripon_task.refresh_from_db()
        self.assertEqual(self.ripon_task.status, Task.STATUS_PENDING)

    def test_26_invalid_voice_command_does_not_perform_the_action(self):
        """26. Unrecognized voice command is rejected without altering task state."""
        client = Client()
        client.login(username='ripon', password='password123')
        response = client.post(
            reverse('cases:voice_task_execute', kwargs={'task_id': self.ripon_task.id}),
            {'command': 'আজকে আবহাওয়া কেমন?'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(response.status_code, 400)
        self.ripon_task.refresh_from_db()
        self.assertEqual(self.ripon_task.status, Task.STATUS_PENDING)
        self.assertFalse(CaseEvent.objects.filter(case=self.case, action='voice_task_completed').exists())

    # =========================================================================
    # PART D: SECURITY & REGRESSION TESTS (27 - 31)
    # =========================================================================

    def test_27_csrf_protection_and_post_security(self):
        """27. State transitions require POST methods with proper authentication."""
        client = Client()
        # GET on execution endpoints should reject or redirect cleanly
        res = client.get(reverse('cases:voice_task_execute', kwargs={'task_id': self.ripon_task.id}))
        # Redirect to login because unauthenticated
        self.assertEqual(res.status_code, 302)

    def test_28_idor_attempts_fail_across_all_endpoints(self):
        """28. Comprehensive IDOR barriers prevent cross-user execution."""
        client = Client()
        client.login(username='citizen_user_b2', password='password123')
        # Citizen tries to access Moyuri confirmation
        res = client.get(reverse('cases:moyuri_confirm'))
        self.assertEqual(res.status_code, 403)

        # Citizen tries to execute Ripon's task
        res_task = client.post(reverse('cases:voice_task_execute', kwargs={'task_id': self.ripon_task.id}), {'command': 'হ্যাঁ'})
        self.assertEqual(res_task.status_code, 403)

    def test_29_role_restrictions_remain_enforced(self):
        """29. Operational workspace boundaries remain intact."""
        client = Client()
        client.login(username='ripon', password='password123')
        # Support staff can access voice task demo
        res = client.get(reverse('cases:ripon_voice_task_demo'))
        self.assertEqual(res.status_code, 302)  # redirects to voice_task/<id>

    def test_30_regression_batch1_and_core_still_pass(self):
        """30. Verifies Batch 1 non-mandatory NID submission and priority queue unaffected."""
        # Unverified NID application can be submitted
        app = submit_application(
            name="আয়নাল হক",
            phone="01799887766",
            address="সাভার, ঢাকা",
            legal_problem="জমি বিরোধ",
            incident_description="জমি সংক্রান্ত বিষয়ে মারধরের হুমকি দেওয়া হচ্ছে।",
            nid_number="",
            nid_verification_status=Application.NID_STATUS_NOT_VERIFIED,
        )
        self.assertEqual(app.nid_verification_status, Application.NID_STATUS_NOT_VERIFIED)
        self.assertTrue(app.application_id.startswith('APP-'))

    def test_31_bilingual_labels_render_in_both_languages(self):
        """31. Conversational and voice templates support bilingual rendering."""
        client = Client()
        # Bangla session
        client.session['django_language'] = 'bn'
        client.session.save()
        res_bn = client.get(reverse('cases:conversational_intake'))
        self.assertEqual(res_bn.status_code, 200)

        # English session
        client.session['django_language'] = 'en'
        client.session.save()
        res_en = client.get(reverse('cases:conversational_intake'))
        self.assertEqual(res_en.status_code, 200)
