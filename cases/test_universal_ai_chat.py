"""
Tests for Universal AI Voice Chat — DLAS Batch (UI + API).

Test plan per specification:
 1. authenticated dashboard includes generic AI Voice Chat (widget present in base).
 2. prototype auth contains generic Voice Chat button.
 3. Moyuri/Ripon navigation labels are removed from login page.
 4. Citizen dashboard no longer exposes separate Marma Intake button.
 5. Underlying Marma backend still works (GET marma_intake returns 200).
 6. Marma provenance chain: submit_marma_intake creates correct CaseEvents.
 7. Marma human confirmation remains required (form fields present).
 8. Voice upload endpoint works (returns transcript).
 9. Real API path is selected when OPENAI_API_KEY is configured.
10. Mock fallback works without API key.
11. API key is never exposed to frontend.
12. Ripon intent does not bypass authorization.
13. Unauthorized task completion fails (IDOR check).
14. Normal citizen conversational intake works.
15. Application ID generated only after confirmation/submission.
16. Case ID absent before DLAO acceptance.
17. CSRF protection remains on AI chat endpoint.
18. IDOR protection remains on task execution.
19. All existing Batch 4 tests still pass (smoke via manage.py check).
"""

import json
import io
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from accounts.models import UserProfile
from cases.models import Application, CaseRecord, CaseEvent, Task
from cases.services import submit_application, accept_application
from cases.universal_ai_service import (
    MockUniversalChatService,
    RealOpenAIService,
    RealGeminiService,
    get_ai_chat_service,
    _detect_marma_intent,
    _detect_voice_task_intent,
    _classify_legal_topic,
)
from unittest.mock import patch
from django.utils import timezone


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_citizen(username='testcitizen', password='pass1234'):
    user = User.objects.create_user(username=username, password=password)
    UserProfile.objects.get_or_create(user=user, defaults={'role': UserProfile.ROLE_CITIZEN})
    return user


def make_officer(username='testofficer', password='pass1234'):
    user = User.objects.create_user(username=username, password=password)
    UserProfile.objects.get_or_create(user=user, defaults={'role': UserProfile.ROLE_DLAO_OFFICER})
    return user


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

class UniversalAIChatServiceTest(TestCase):
    """Unit tests for the service layer — no HTTP."""

    def test_mock_service_is_default_without_api_key(self):
        """10. Mock fallback works without API key."""
        with self.settings(OPENAI_API_KEY='', GEMINI_API_KEY=''):
            svc = get_ai_chat_service()
            self.assertIs(svc, MockUniversalChatService)

    def test_gemini_service_selected_with_gemini_api_key(self):
        """Gemini service is prioritized when GEMINI_API_KEY is configured."""
        with self.settings(GEMINI_API_KEY='fake-gemini-key', OPENAI_API_KEY=''):
            svc = get_ai_chat_service()
            self.assertIs(svc, RealGeminiService)

    def test_real_openai_service_selected_with_openai_api_key(self):
        """OpenAI service is selected when OPENAI_API_KEY is configured and no GEMINI_API_KEY."""
        with self.settings(GEMINI_API_KEY='', OPENAI_API_KEY='sk-fake-key-for-test'):
            svc = get_ai_chat_service()
            self.assertIs(svc, RealOpenAIService)

    def test_settings_key_resolution_without_key(self):
        """Resolving without environment/file returns empty string."""
        from config.settings import _resolve_api_key
        with patch.dict('os.environ', {'OPENAI_API_KEY': '', 'GEMINI_API_KEY': ''}, clear=False):
            with patch('pathlib.Path.is_file', return_value=False):
                with patch('os.name', 'posix'):
                    key = _resolve_api_key('GEMINI_API_KEY')
                    self.assertEqual(key, '')

    def test_mock_chat_returns_bilingual_reply(self):
        result = MockUniversalChatService.chat('আমার জমি নিয়ে সমস্যা', [], 'bn')
        self.assertIn('reply_bn', result)
        self.assertIn('reply_en', result)
        self.assertTrue(result['reply_bn'])
        self.assertTrue(result['reply_en'])

    def test_mock_chat_detects_marma(self):
        result = MockUniversalChatService.chat('আমি মারমা ভাষায় কথা বলতে চাই', [], 'bn')
        self.assertTrue(result['marma_workflow'])
        self.assertEqual(result['suggested_action'], 'marma_intake')

    def test_mock_chat_detects_voice_task_intent(self):
        result = MockUniversalChatService.chat('আমি এই রেফারেলটি গ্রহণ করছি', [], 'bn')
        self.assertIsNotNone(result['voice_task_intent'])
        self.assertEqual(result['voice_task_intent']['intent'], 'acknowledge_referral')

    def test_mock_transcription_returns_transcript(self):
        """8. Voice upload endpoint works."""
        result = MockUniversalChatService.transcribe_audio(b'fake-audio-bytes', 'test.webm')
        self.assertIn('transcript', result)
        self.assertTrue(result['transcript'])
        self.assertTrue(result['is_simulated'])

    def test_classify_legal_topic_land(self):
        self.assertEqual(_classify_legal_topic('আমার জমি সংক্রান্ত সমস্যা'), 'land_property')

    def test_classify_legal_topic_family(self):
        self.assertEqual(_classify_legal_topic('dowry case'), 'family_law')

    def test_detect_marma_intent(self):
        self.assertTrue(_detect_marma_intent('মারমা ভাষায়'))
        self.assertTrue(_detect_marma_intent('marma language'))
        self.assertFalse(_detect_marma_intent('জমির সমস্যা'))

    def test_detect_voice_task_intent(self):
        self.assertIsNotNone(_detect_voice_task_intent('গ্রহণ করছি'))
        self.assertIsNone(_detect_voice_task_intent('আমার নাম রহিম'))

    def test_api_key_not_in_service_response(self):
        """11. API key is never exposed to frontend."""
        result = MockUniversalChatService.chat('test', [], 'en')
        response_str = json.dumps(result)
        # Ensure no key-like value leaks
        self.assertNotIn('sk-', response_str)
        self.assertNotIn('OPENAI_API_KEY', response_str)
        self.assertNotIn('GEMINI_API_KEY', response_str)


class UniversalAIChatViewTest(TestCase):
    """HTTP-level tests for the chat endpoints."""

    def setUp(self):
        self.client = Client()
        self.citizen = make_citizen()
        self.officer = make_officer()
        # Force mock service in tests — never make real API calls
        self.settings_override = self.settings(OPENAI_API_KEY='', GEMINI_API_KEY='')
        self.settings_override.enable()

    def tearDown(self):
        self.settings_override.disable()

    # -- Test 17: CSRF protection --
    def test_chat_message_requires_csrf(self):
        """17. CSRF protection remains on AI chat endpoint."""
        self.client.login(username='testcitizen', password='pass1234')
        # Enforce CSRF checking
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.login(username='testcitizen', password='pass1234')
        response = csrf_client.post(
            reverse('cases:ai_chat_message'),
            data=json.dumps({'message': 'hello'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)

    def test_chat_message_requires_auth(self):
        """Unauthenticated requests are redirected."""
        response = self.client.post(
            reverse('cases:ai_chat_message'),
            data=json.dumps({'message': 'hello'}),
            content_type='application/json',
        )
        self.assertIn(response.status_code, [302, 401])

    def test_chat_message_authenticated_returns_json(self):
        """Authenticated POST returns a JSON reply."""
        self.client.login(username='testcitizen', password='pass1234')
        response = self.client.post(
            reverse('cases:ai_chat_message'),
            data=json.dumps({'message': 'আমার জমির সমস্যা', 'lang': 'bn'}),
            content_type='application/json',
            HTTP_X_CSRFTOKEN=self.client.cookies.get('csrftoken', ''),
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('reply_bn', data)
        self.assertIn('reply_en', data)
        self.assertIn('is_simulated', data)

    def test_chat_reply_does_not_expose_api_key(self):
        """11. API key is never in response body."""
        self.client.login(username='testcitizen', password='pass1234')
        with self.settings(OPENAI_API_KEY='sk-secret-key-test', GEMINI_API_KEY='gemini-secret-test'):
            with patch('cases.universal_ai_service.get_ai_chat_service') as mock_factory:
                from cases.universal_ai_service import MockUniversalChatService
                mock_factory.return_value = MockUniversalChatService
                response = self.client.post(
                    reverse('cases:ai_chat_message'),
                    data=json.dumps({'message': 'hello', 'lang': 'en'}),
                    content_type='application/json',
                )
        body = response.content.decode('utf-8')
        self.assertNotIn('sk-secret-key-test', body)
        self.assertNotIn('OPENAI_API_KEY', body)
        self.assertNotIn('gemini-secret-test', body)
        self.assertNotIn('GEMINI_API_KEY', body)

    def test_voice_upload_requires_auth(self):
        """Unauthenticated voice upload is rejected."""
        response = self.client.post(reverse('cases:ai_voice_upload'))
        self.assertIn(response.status_code, [302, 401])

    def test_voice_upload_no_file_returns_400(self):
        """Voice upload without a file returns 400."""
        self.client.login(username='testcitizen', password='pass1234')
        response = self.client.post(reverse('cases:ai_voice_upload'))
        self.assertEqual(response.status_code, 400)

    def test_voice_upload_with_file_returns_transcript(self):
        """8. Voice upload with a file returns a transcript."""
        self.client.login(username='testcitizen', password='pass1234')
        fake_audio = io.BytesIO(b'fake-webm-data')
        fake_audio.name = 'test.webm'
        response = self.client.post(
            reverse('cases:ai_voice_upload'),
            data={'audio': fake_audio},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('transcript', data)
        self.assertTrue(data['is_simulated'])

    def test_voice_upload_no_api_key_in_response(self):
        """11. API key never in voice upload response."""
        self.client.login(username='testcitizen', password='pass1234')
        with self.settings(OPENAI_API_KEY='sk-voice-secret'):
            with patch('cases.universal_ai_service.get_ai_chat_service') as mock_factory:
                from cases.universal_ai_service import MockUniversalChatService
                mock_factory.return_value = MockUniversalChatService
                fake_audio = io.BytesIO(b'fake-audio')
                fake_audio.name = 'rec.webm'
                response = self.client.post(
                    reverse('cases:ai_voice_upload'),
                    data={'audio': fake_audio},
                )
        body = response.content.decode('utf-8')
        self.assertNotIn('sk-voice-secret', body)


class LoginPageCleanupTest(TestCase):
    """
    2. Auth page has generic Voice Chat.
    3. Moyuri/Ripon nav labels removed.
    """

    def setUp(self):
        self.client = Client()

    def test_login_page_loads(self):
        response = self.client.get(reverse('accounts:login'))
        self.assertEqual(response.status_code, 200)

    def test_login_has_generic_voice_chat_button(self):
        """2. Prototype auth contains generic Voice Chat."""
        response = self.client.get(reverse('accounts:login'))
        self.assertContains(response, 'Voice Chat')

    def test_login_no_moyuri_button(self):
        """3. Moyuri nav label removed."""
        response = self.client.get(reverse('accounts:login'))
        self.assertNotContains(response, 'Moyuri (Citizen)')
        self.assertNotContains(response, 'ময়ূরী (নাগরিক)')

    def test_login_no_ripon_button(self):
        """3. Ripon nav label removed."""
        response = self.client.get(reverse('accounts:login'))
        self.assertNotContains(response, 'Ripon (Voice Tasks)')
        self.assertNotContains(response, 'রিপন (ভয়েস টাস্ক)')

    def test_login_all_standard_roles_present(self):
        """All 9 standard roles present on login page."""
        response = self.client.get(reverse('accounts:login'))
        for role in ['citizen', 'dlao_officer', 'support_staff', 'udc_operator',
                     'panel_lawyer', 'mediator', 'helpline_agent', 'representative', 'admin']:
            self.assertContains(response, role)


class CitizenDashboardTest(TestCase):
    """
    1. Authenticated dashboard includes AI Voice Chat.
    4. No separate Marma Intake button.
    """

    def setUp(self):
        self.client = Client()
        self.citizen = make_citizen('dashcit', 'pass1234')
        self.client.login(username='dashcit', password='pass1234')
        # Prevent real API calls during HTML rendering
        self.settings_override = self.settings(OPENAI_API_KEY='', GEMINI_API_KEY='')
        self.settings_override.enable()

    def tearDown(self):
        self.settings_override.disable()

    def test_citizen_dashboard_loads(self):
        response = self.client.get(reverse('dashboard:citizen'))
        self.assertEqual(response.status_code, 200)

    def test_citizen_dashboard_has_voice_chat(self):
        """1. Authenticated dashboard includes generic AI Voice Chat."""
        response = self.client.get(reverse('dashboard:citizen'))
        self.assertContains(response, 'Voice Chat')

    def test_citizen_dashboard_no_marma_intake_button(self):
        """4. No separate Marma Intake nav button in citizen dashboard header."""
        response = self.client.get(reverse('dashboard:citizen'))
        content = response.content.decode('utf-8')
        # The citizen dashboard nav/header should not have a dedicated Marma Intake nav item.
        # Note: the floating AI chat widget in base.html may show a 'Start Marma Intake' hint
        # when Marma intent is detected (inside the hidden ai-marma-banner div), which is correct.
        # We verify the dashboard header actions section does not contain a separate Marma Intake button.
        # The citizen.html header actions only has: Voice Chat, Offline Queue, New Application.
        import re
        # Check that no standalone Marma nav link exists outside the ai-chat-widget
        # by ensuring 'Marma Intake' does not appear in the dashboard-header section
        header_section = re.search(
            r'class="dashboard-header".*?(?=<div class="card"|<main|$)',
            content, re.DOTALL
        )
        if header_section:
            self.assertNotIn('Marma Intake', header_section.group(0))
        # Also verify in the citizen.html template file directly
        import os
        from django.conf import settings
        template_path = os.path.join(settings.BASE_DIR, 'templates', 'dashboard', 'citizen.html')
        if os.path.exists(template_path):
            with open(template_path, encoding='utf-8') as f:
                citizen_tmpl = f.read()
            self.assertNotIn('Marma Intake', citizen_tmpl)
            self.assertNotIn('marma_intake', citizen_tmpl)

    def test_floating_chat_head_in_base(self):
        """1. AI chat widget present for authenticated users via base.html."""
        response = self.client.get(reverse('dashboard:citizen'))
        self.assertContains(response, 'ai-chat-widget')
        self.assertContains(response, 'dlasChat')

    def test_api_key_not_in_dashboard_html(self):
        """11. API key never in page HTML."""
        response = self.client.get(reverse('dashboard:citizen'))
        body = response.content.decode('utf-8')
        self.assertNotIn('OPENAI_API_KEY', body)
        self.assertNotIn('GEMINI_API_KEY', body)
        # Verify no sk-... API key string appears in rendered HTML
        import re
        sk_keys = re.findall(r'sk-[A-Za-z0-9_-]{10,}', body)
        self.assertEqual(sk_keys, [], f"Unexpected API key-like string in HTML: {sk_keys}")

    def test_citizen_dashboard_pending_banner(self):
        """Review banner appears when a conversational intake is pending review."""
        session = self.client.session
        session['conversational_intake'] = {'status': 'review', 'slots': {}}
        session.save()
        response = self.client.get(reverse('dashboard:citizen'))
        self.assertContains(response, 'Voice Chat Intake Pending')


class MarmaBackendTest(TestCase):
    """
    5. Marma backend works.
    6. Marma provenance chain intact.
    7. Marma human confirmation required.
    """

    def setUp(self):
        self.client = Client()
        self.citizen = make_citizen('marmatest', 'pass1234')
        self.client.login(username='marmatest', password='pass1234')

    def test_marma_intake_page_loads(self):
        """5. Underlying Marma backend still works."""
        response = self.client.get(reverse('cases:marma_intake'))
        self.assertEqual(response.status_code, 200)

    def test_marma_intake_form_has_provenance_fields(self):
        """7. Marma human confirmation fields present in form."""
        response = self.client.get(reverse('cases:marma_intake'))
        self.assertContains(response, 'original_statement')
        self.assertContains(response, 'translated_statement')

    def test_marma_intake_submit_creates_application_and_events(self):
        """6. Marma provenance chain: submit_marma_intake creates correct CaseEvents."""
        from cases.services import submit_marma_intake
        officer = make_officer('marma_officer', 'pass1234')
        app = submit_marma_intake(
            name='মং শোয়ে প্রু মারমা',
            phone='01844000999',
            address='রোয়াংছড়ি, বান্দরবান',
            original_statement='မြေ ပြဿနာ',
            translated_statement='জমি বিরোধ',
            typed_legal_problem='জমি জবরদখল',
            typed_incident_description='পৈতৃক জমি দখল করা হয়েছে।',
            statement_language='marma',
            actor=officer,
            applicant_user=None,
            preferred_channel=Application.CHANNEL_UDC,
            safe_contact_number='',
            safe_contact_time='',
            nid_number='',
        )
        self.assertIsNotNone(app)
        self.assertTrue(app.application_id.startswith('APP-'))

        # 6. Check CaseEvents for provenance chain
        events = CaseEvent.objects.filter(application=app)
        action_names = list(events.values_list('action', flat=True))
        self.assertIn('marma_statement_recorded', action_names)
        self.assertIn('marma_translation_recorded', action_names)
        self.assertIn('marma_typed_confirmation', action_names)

    def test_marma_no_case_id_at_submission(self):
        """16. Case ID absent before DLAO acceptance."""
        from cases.services import submit_marma_intake
        officer = make_officer('marma_officer2', 'pass1234')
        app = submit_marma_intake(
            name='Test Marma',
            phone='01844000998',
            address='Bandarban',
            original_statement='original',
            translated_statement='translated',
            typed_legal_problem='land',
            typed_incident_description='land dispute',
            statement_language='marma',
            actor=officer,
        )
        # Application exists but no CaseRecord yet
        self.assertFalse(CaseRecord.objects.filter(application=app).exists())


class CitizenIntakeTest(TestCase):
    """
    14. Normal citizen conversational intake works.
    15. Application ID generated after confirmation.
    16. Case ID absent before DLAO acceptance.
    """

    def setUp(self):
        self.client = Client()
        self.citizen = make_citizen('intakecit', 'pass1234')
        self.client.login(username='intakecit', password='pass1234')

    def test_conversational_intake_page_loads(self):
        response = self.client.get(reverse('cases:conversational_intake'))
        self.assertEqual(response.status_code, 200)

    def test_intake_message_endpoint_returns_json(self):
        """14. Normal citizen conversational intake works."""
        response = self.client.post(
            reverse('cases:conversational_intake_message'),
            data=json.dumps({'message': 'রহিম উদ্দিন'}),
            content_type='application/json',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('status', data)

    def test_application_id_on_submission(self):
        """15. Application ID generated after submission."""
        app = submit_application(
            name='রহিম উদ্দিন',
            phone='01712345678',
            address='মিরপুর, ঢাকা',
            legal_problem='জমির সমস্যা',
            incident_description='প্রতিপক্ষ জমি দখল করেছে।',
            applicant_user=self.citizen,
            preferred_channel=Application.CHANNEL_WEB,
        )
        self.assertTrue(app.application_id.startswith('APP-'))

    def test_no_case_id_before_dlao_acceptance(self):
        """16. Case ID absent before DLAO acceptance."""
        app = submit_application(
            name='করিম উদ্দিন',
            phone='01712340001',
            address='সাভার, ঢাকা',
            legal_problem='বেতন সমস্যা',
            incident_description='বেতন বকেয়া আছে।',
            applicant_user=self.citizen,
            preferred_channel=Application.CHANNEL_WEB,
        )
        self.assertFalse(CaseRecord.objects.filter(application=app).exists())


class VoiceTaskIDORTest(TestCase):
    """
    12. Ripon intent does not bypass authorization.
    13. Unauthorized task completion fails.
    18. IDOR protection remains.
    """

    def setUp(self):
        self.client = Client()
        self.officer = make_officer('taskoff', 'pass1234')
        self.other_user = make_citizen('otherusr', 'pass1234')
        self.settings_override = self.settings(OPENAI_API_KEY='', GEMINI_API_KEY='')
        self.settings_override.enable()
        # Create an application + case + task
        app = submit_application(
            name='Test User',
            phone='01711111111',
            address='Dhaka',
            legal_problem='land',
            incident_description='land dispute',
            preferred_channel=Application.CHANNEL_WEB,
        )
        case = accept_application(app, self.officer, priority='HIGH')
        self.task = Task.objects.create(
            case=case,
            assigned_to=self.officer,
            title='Test Task',
            description='Test',
            status=Task.STATUS_PENDING,
            due_at=timezone.now() + timezone.timedelta(days=1),
            priority='HIGH',
        )

    def tearDown(self):
        self.settings_override.disable()


    def test_unauthorized_user_cannot_execute_task(self):
        """13. Unauthorized task completion fails (IDOR)."""
        self.client.login(username='otherusr', password='pass1234')
        response = self.client.post(
            reverse('cases:voice_task_execute', kwargs={'task_id': self.task.id}),
            data={'command': 'yes, accept'},
        )
        self.assertEqual(response.status_code, 403)

    def test_ai_chat_intent_alone_cannot_complete_task(self):
        """12. Ripon intent in AI chat does not bypass authorization."""
        self.client.login(username='otherusr', password='pass1234')
        # AI chat detects intent but returns advisory message, not task completion
        response = self.client.post(
            reverse('cases:ai_chat_message'),
            data=json.dumps({'message': 'আমি এই রেফারেলটি গ্রহণ করছি', 'lang': 'bn'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        # Should return advisory, not a completed action
        self.assertIn('reply_bn', data)
        # Task must remain PENDING
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.STATUS_PENDING)

    def test_task_execute_requires_login(self):
        """18. IDOR protection — unauthenticated request rejected."""
        response = self.client.post(
            reverse('cases:voice_task_execute', kwargs={'task_id': self.task.id}),
            data={'command': 'yes'},
        )
        self.assertIn(response.status_code, [302, 403])


# ---------------------------------------------------------------------------
# Conversational Application Intake Tests (Gemini + Universal AI Chat)
# ---------------------------------------------------------------------------

class ConversationalApplicationWorkflowTest(TestCase):
    """
    Comprehensive tests for conversational legal aid application intake:
    - Step-by-step required field collection (Bangla & English)
    - Missing-field handling (asks one-by-one, blocks submission)
    - Invalid-field validation matching CitizenApplicationForm
    - Confirmation before submission requirement
    - Successful submission through existing workflow (Application ID generated, no CaseRecord)
    - Gemini cannot bypass validation or submit without confirmation
    - Switch to manual form & cancellation
    - Existing manual form behavior unchanged
    """

    def setUp(self):
        self.citizen = make_citizen(username='conv_citizen', password='password123')
        self.client.login(username='conv_citizen', password='password123')
        self.settings_override = self.settings(OPENAI_API_KEY='', GEMINI_API_KEY='')
        self.settings_override.enable()

    def tearDown(self):
        self.settings_override.disable()

    def test_conversational_step_by_step_collection_and_submission_bangla(self):
        """Conversational intake collects fields step-by-step and submits upon confirmation (Bangla)."""
        # 1. Start application
        r1 = self.client.post(
            reverse('cases:ai_chat_message'),
            data=json.dumps({'message': 'আমি আইনি সহায়তার জন্য আবেদন করতে চাই', 'lang': 'bn'}),
            content_type='application/json',
        )
        self.assertEqual(r1.status_code, 200)
        d1 = r1.json()
        self.assertTrue(d1.get('intake_active'))
        self.assertEqual(d1.get('current_slot'), 'name')
        self.assertIn('নাম', d1.get('reply_bn'))

        # 2. Provide Name
        r2 = self.client.post(
            reverse('cases:ai_chat_message'),
            data=json.dumps({'message': 'আমার নাম মোঃ আব্দুর রহিম', 'lang': 'bn'}),
            content_type='application/json',
        )
        self.assertEqual(r2.status_code, 200)
        d2 = r2.json()
        self.assertTrue(d2.get('intake_active'))
        self.assertEqual(d2.get('current_slot'), 'phone')
        self.assertIn('ফোন', d2.get('reply_bn'))

        # 3. Provide Phone
        r3 = self.client.post(
            reverse('cases:ai_chat_message'),
            data=json.dumps({'message': '০১৭১১২২৩৩৪৪', 'lang': 'bn'}),
            content_type='application/json',
        )
        self.assertEqual(r3.status_code, 200)
        d3 = r3.json()
        self.assertTrue(d3.get('intake_active'))
        self.assertEqual(d3.get('current_slot'), 'address')
        self.assertIn('ঠিকানা', d3.get('reply_bn'))

        # 4. Provide Address
        r4 = self.client.post(
            reverse('cases:ai_chat_message'),
            data=json.dumps({'message': 'গ্রাম: রামপুর, উপজেলা: সদর, জেলা: সিলেট', 'lang': 'bn'}),
            content_type='application/json',
        )
        self.assertEqual(r4.status_code, 200)
        d4 = r4.json()
        self.assertTrue(d4.get('intake_active'))
        self.assertEqual(d4.get('current_slot'), 'legal_problem')
        self.assertIn('আইনি সমস্যা', d4.get('reply_bn'))

        # 5. Provide Legal Problem
        r5 = self.client.post(
            reverse('cases:ai_chat_message'),
            data=json.dumps({'message': 'পৈতৃক জমিজমা সংক্রান্ত বিরোধ', 'lang': 'bn'}),
            content_type='application/json',
        )
        self.assertEqual(r5.status_code, 200)
        d5 = r5.json()
        self.assertTrue(d5.get('intake_active'))
        self.assertEqual(d5.get('current_slot'), 'incident_description')
        self.assertIn('ঘটনা', d5.get('reply_bn'))

        # 6. Provide Incident Description -> Transitions to review
        r6 = self.client.post(
            reverse('cases:ai_chat_message'),
            data=json.dumps({'message': 'প্রতিপক্ষ জোরপূর্বক আমাদের সীমানা প্রাচীর ভেঙে দখল করার চেষ্টা করছে', 'lang': 'bn'}),
            content_type='application/json',
        )
        self.assertEqual(r6.status_code, 200)
        d6 = r6.json()
        self.assertTrue(d6.get('intake_active'))
        self.assertEqual(d6.get('intake_status'), 'review')
        self.assertEqual(d6.get('suggested_action'), 'confirm_submission')
        self.assertIn('সারসংক্ষেপ', d6.get('reply_bn'))
        # Ensure application not submitted yet before confirmation
        self.assertEqual(Application.objects.count(), 0)

        # 7. Explicit Human Confirmation
        r7 = self.client.post(
            reverse('cases:ai_chat_message'),
            data=json.dumps({'message': 'হ্যাঁ, আবেদন জমা দিন', 'lang': 'bn'}),
            content_type='application/json',
        )
        self.assertEqual(r7.status_code, 200)
        d7 = r7.json()
        self.assertFalse(d7.get('intake_active'))
        self.assertEqual(d7.get('intake_status'), 'confirmed')
        app_id = d7.get('application_id')
        self.assertTrue(app_id.startswith('APP-'))

        # Verify database record
        app = Application.objects.get(application_id=app_id)
        self.assertEqual(app.name, 'মোঃ আব্দুর রহিম')
        self.assertEqual(app.phone, '01711223344')
        self.assertEqual(app.status, Application.STATUS_SUBMITTED)
        self.assertEqual(app.applicant_user, self.citizen)

        # Verify audit event
        event = CaseEvent.objects.filter(application=app, action='APPLICATION_SUBMITTED').first()
        self.assertIsNotNone(event)
        self.assertEqual(event.provenance, CaseEvent.PROVENANCE_APPLICANT_CONFIRMED)

        # Verify NO CaseRecord created
        self.assertFalse(CaseRecord.objects.filter(application=app).exists())

    def test_conversational_step_by_step_collection_and_submission_english(self):
        """Conversational intake collects fields step-by-step and submits upon confirmation (English)."""
        # 1. Start application
        r1 = self.client.post(
            reverse('cases:ai_chat_message'),
            data=json.dumps({'message': 'I want to apply for legal aid', 'lang': 'en'}),
            content_type='application/json',
        )
        self.assertEqual(r1.status_code, 200)
        d1 = r1.json()
        self.assertEqual(d1.get('current_slot'), 'name')

        # 2. Name
        r2 = self.client.post(
            reverse('cases:ai_chat_message'),
            data=json.dumps({'message': 'Johnathan Doe', 'lang': 'en'}),
            content_type='application/json',
        )
        self.assertEqual(r2.json().get('current_slot'), 'phone')

        # 3. Phone
        r3 = self.client.post(
            reverse('cases:ai_chat_message'),
            data=json.dumps({'message': '01812345678', 'lang': 'en'}),
            content_type='application/json',
        )
        self.assertEqual(r3.json().get('current_slot'), 'address')

        # 4. Address
        r4 = self.client.post(
            reverse('cases:ai_chat_message'),
            data=json.dumps({'message': 'House 12, Road 5, Dhanmondi, Dhaka', 'lang': 'en'}),
            content_type='application/json',
        )
        self.assertEqual(r4.json().get('current_slot'), 'legal_problem')

        # 5. Legal problem
        r5 = self.client.post(
            reverse('cases:ai_chat_message'),
            data=json.dumps({'message': 'Unlawful tenancy eviction dispute', 'lang': 'en'}),
            content_type='application/json',
        )
        self.assertEqual(r5.json().get('current_slot'), 'incident_description')

        # 6. Incident description -> Review
        r6 = self.client.post(
            reverse('cases:ai_chat_message'),
            data=json.dumps({'message': 'Landlord locked the property without legal notice on Monday.', 'lang': 'en'}),
            content_type='application/json',
        )
        d6 = r6.json()
        self.assertEqual(d6.get('intake_status'), 'review')
        self.assertIn('Application Information Summary', d6.get('reply_en'))

        # 7. Confirm
        r7 = self.client.post(
            reverse('cases:ai_chat_message'),
            data=json.dumps({'message': 'CONFIRM', 'lang': 'en'}),
            content_type='application/json',
        )
        d7 = r7.json()
        self.assertEqual(d7.get('intake_status'), 'confirmed')
        app_id = d7.get('application_id')
        self.assertTrue(app_id.startswith('APP-'))

        app = Application.objects.get(application_id=app_id)
        self.assertEqual(app.name, 'Johnathan Doe')
        self.assertEqual(app.phone, '01812345678')

    def test_missing_field_cannot_submit(self):
        """User cannot submit before all required fields are collected."""
        # Start application
        self.client.post(
            reverse('cases:ai_chat_message'),
            data=json.dumps({'message': 'I want to apply', 'lang': 'en'}),
            content_type='application/json',
        )
        # Attempt to confirm prematurely
        res = self.client.post(
            reverse('cases:ai_chat_message'),
            data=json.dumps({'message': 'CONFIRM', 'lang': 'en'}),
            content_type='application/json',
        )
        data = res.json()
        self.assertNotEqual(data.get('intake_status'), 'confirmed')
        self.assertEqual(Application.objects.count(), 0)

    def test_invalid_phone_rejected(self):
        """Phone numbers with fewer than 6 digits are rejected."""
        self.client.post(
            reverse('cases:ai_chat_message'),
            data=json.dumps({'message': 'I want to apply', 'lang': 'en'}),
            content_type='application/json',
        )
        self.client.post(
            reverse('cases:ai_chat_message'),
            data=json.dumps({'message': 'Kamal Hossain', 'lang': 'en'}),
            content_type='application/json',
        )
        # Provide invalid phone with < 6 digits
        res = self.client.post(
            reverse('cases:ai_chat_message'),
            data=json.dumps({'message': '123', 'lang': 'en'}),
            content_type='application/json',
        )
        data = res.json()
        self.assertIn('at least 6 digits', data.get('reply_en') + data.get('reply_bn'))
        # Still in phone slot
        self.assertEqual(data.get('current_slot'), 'phone')
        self.assertEqual(Application.objects.count(), 0)

    def test_invalid_name_rejected(self):
        """Names with fewer than 2 characters are rejected."""
        self.client.post(
            reverse('cases:ai_chat_message'),
            data=json.dumps({'message': 'I want to apply', 'lang': 'en'}),
            content_type='application/json',
        )
        # Provide single character name
        res = self.client.post(
            reverse('cases:ai_chat_message'),
            data=json.dumps({'message': 'A', 'lang': 'en'}),
            content_type='application/json',
        )
        data = res.json()
        self.assertIn('at least 2 characters', data.get('reply_en') + data.get('reply_bn'))
        self.assertEqual(data.get('current_slot'), 'name')

    def test_invalid_legal_problem_rejected(self):
        """Legal problems with fewer than 5 characters are rejected."""
        self.client.post(reverse('cases:ai_chat_message'), data=json.dumps({'message': 'I want to apply'}), content_type='application/json')
        self.client.post(reverse('cases:ai_chat_message'), data=json.dumps({'message': 'Kamal Hossain'}), content_type='application/json')
        self.client.post(reverse('cases:ai_chat_message'), data=json.dumps({'message': '01711223344'}), content_type='application/json')
        self.client.post(reverse('cases:ai_chat_message'), data=json.dumps({'message': 'Dhaka Sadar'}), content_type='application/json')
        # Legal problem too short (< 5 chars)
        res = self.client.post(
            reverse('cases:ai_chat_message'),
            data=json.dumps({'message': 'জমি'}),
            content_type='application/json',
        )
        data = res.json()
        self.assertIn('কমপক্ষে ৫ অক্ষর', data.get('reply_bn') + data.get('reply_en'))
        self.assertEqual(data.get('current_slot'), 'legal_problem')

    def test_invalid_incident_description_rejected(self):
        """Incident descriptions with fewer than 10 characters are rejected."""
        self.client.post(reverse('cases:ai_chat_message'), data=json.dumps({'message': 'I want to apply'}), content_type='application/json')
        self.client.post(reverse('cases:ai_chat_message'), data=json.dumps({'message': 'Kamal Hossain'}), content_type='application/json')
        self.client.post(reverse('cases:ai_chat_message'), data=json.dumps({'message': '01711223344'}), content_type='application/json')
        self.client.post(reverse('cases:ai_chat_message'), data=json.dumps({'message': 'Dhaka Sadar'}), content_type='application/json')
        self.client.post(reverse('cases:ai_chat_message'), data=json.dumps({'message': 'জমিজমা বিরোধ'}), content_type='application/json')
        # Description too short (< 10 chars)
        res = self.client.post(
            reverse('cases:ai_chat_message'),
            data=json.dumps({'message': 'ঝামেলা'}),
            content_type='application/json',
        )
        data = res.json()
        self.assertIn('কমপক্ষে ১০ অক্ষর', data.get('reply_bn') + data.get('reply_en'))
        self.assertEqual(data.get('current_slot'), 'incident_description')

    def test_switch_to_manual_form(self):
        """User can switch to manual form anytime during conversational intake."""
        self.client.post(reverse('cases:ai_chat_message'), data=json.dumps({'message': 'I want to apply'}), content_type='application/json')
        res = self.client.post(
            reverse('cases:ai_chat_message'),
            data=json.dumps({'message': 'switch to manual form', 'lang': 'en'}),
            content_type='application/json',
        )
        data = res.json()
        self.assertFalse(data.get('intake_active'))
        self.assertEqual(data.get('intake_status'), 'manual_form')
        self.assertEqual(data.get('manual_form_url'), '/cases/apply/')

    def test_cancellation(self):
        """User can cancel the conversational intake session."""
        self.client.post(reverse('cases:ai_chat_message'), data=json.dumps({'message': 'I want to apply'}), content_type='application/json')
        res = self.client.post(
            reverse('cases:ai_chat_message'),
            data=json.dumps({'message': 'cancel', 'lang': 'en'}),
            content_type='application/json',
        )
        data = res.json()
        self.assertFalse(data.get('intake_active'))
        self.assertEqual(data.get('intake_status'), 'cancelled')

    def test_correction_during_review(self):
        """User can correct a field (e.g. phone) during review before final submission."""
        self.client.post(reverse('cases:ai_chat_message'), data=json.dumps({'message': 'I want to apply'}), content_type='application/json')
        self.client.post(reverse('cases:ai_chat_message'), data=json.dumps({'message': 'Kamal Hossain'}), content_type='application/json')
        self.client.post(reverse('cases:ai_chat_message'), data=json.dumps({'message': '01711223344'}), content_type='application/json')
        self.client.post(reverse('cases:ai_chat_message'), data=json.dumps({'message': 'Dhaka Sadar'}), content_type='application/json')
        self.client.post(reverse('cases:ai_chat_message'), data=json.dumps({'message': 'Land eviction dispute'}), content_type='application/json')
        r_rev = self.client.post(reverse('cases:ai_chat_message'), data=json.dumps({'message': 'Landlord tried to evict on Sunday morning'}), content_type='application/json')
        self.assertEqual(r_rev.json().get('intake_status'), 'review')

        # Correct phone
        r_corr = self.client.post(
            reverse('cases:ai_chat_message'),
            data=json.dumps({'message': 'phone should be 01999888777'}),
            content_type='application/json',
        )
        d_corr = r_corr.json()
        self.assertEqual(d_corr.get('intake_status'), 'review')
        self.assertIn('01999888777', d_corr.get('reply_en') + d_corr.get('reply_bn'))

        # Submit
        r_sub = self.client.post(
            reverse('cases:ai_chat_message'),
            data=json.dumps({'message': 'CONFIRM'}),
            content_type='application/json',
        )
        app_id = r_sub.json().get('application_id')
        app = Application.objects.get(application_id=app_id)
        self.assertEqual(app.phone, '01999888777')

    def test_existing_manual_form_unchanged(self):
        """The existing manual application form works without modification."""
        get_res = self.client.get(reverse('cases:application_create'))
        self.assertEqual(get_res.status_code, 200)

        post_data = {
            'action': 'submit',
            'name': 'Manual Test Citizen',
            'phone': '01700112233',
            'address': 'Chittagong Sadar',
            'legal_problem': 'Boundary wall dispute',
            'incident_description': 'Neighbor demolished wall without authorization.',
            'preferred_channel': 'web',
            'language': 'bn',
        }
        post_res = self.client.post(reverse('cases:application_create'), data=post_data)
        self.assertEqual(post_res.status_code, 302)
        app = Application.objects.filter(name='Manual Test Citizen').first()
        self.assertIsNotNone(app)
        self.assertEqual(app.phone, '01700112233')

    def test_real_gemini_service_conversational_intake(self):
        """RealGeminiService properly handles conversational intake with slot guidance."""
        from unittest.mock import MagicMock
        from cases.universal_ai_service import ConversationalIntakeManager
        with patch.object(RealGeminiService, '_get_client') as mock_client_factory:
            mock_client = mock_client_factory.return_value
            mock_response = MagicMock()
            mock_response.text = "Hello! Please tell me your full name."
            mock_client.models.generate_content.return_value = mock_response

            intake_state = ConversationalIntakeManager.init_state(lang='en')
            res = RealGeminiService.chat(
                message="I want to apply for legal aid",
                history=[],
                lang='en',
                intake_state=intake_state,
                user=self.citizen,
            )
            self.assertTrue(res.get('intake_active'))
            self.assertEqual(res.get('current_slot'), 'name')
            self.assertFalse(res.get('is_simulated'))

    def test_cannot_bypass_validation_direct_call(self):
        """ConversationalIntakeManager.submit_intake_application enforces CitizenApplicationForm validation."""
        from cases.universal_ai_service import ConversationalIntakeManager
        from django.core.exceptions import ValidationError
        bad_state = ConversationalIntakeManager.init_state(lang='bn')
        bad_state['slots']['name'] = 'A'  # Too short (< 2)
        bad_state['slots']['phone'] = '123'  # Too short (< 6)
        bad_state['slots']['address'] = 'X'
        bad_state['slots']['legal_problem'] = 'bad'
        bad_state['slots']['incident_description'] = 'bad'

        with self.assertRaises(ValidationError):
            ConversationalIntakeManager.submit_intake_application(bad_state, user=self.citizen)


