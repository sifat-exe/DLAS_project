"""
Universal AI Chat Service for DLAS.

Architecture:
    AIService
    ├── RealOpenAIService  (when OPENAI_API_KEY is configured)
    └── MockUniversalChatService  (deterministic fallback)

SAFETY RULES (per MASTER_PRD Section 4.4):
- AI is ASSISTIVE ONLY.
- AI must NEVER determine legal eligibility, reject applications,
  assign final priority, assign lawyers, merge cases, or close cases.
- API key is read server-side from Django settings. NEVER exposed to clients.
- All AI outputs carry a clear "ai_assisted" provenance label.
- Human confirmation is ALWAYS required before any application submission.
"""

import io
import re
from django.conf import settings
from django.core.exceptions import ValidationError
from cases.forms import CitizenApplicationForm
from cases.services import submit_application
from cases.models import Application, CaseEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

AI_SAFETY_DISCLAIMER_EN = (
    "AI ASSISTED — For human review only. "
    "AI cannot make legal decisions or determine eligibility."
)
AI_SAFETY_DISCLAIMER_BN = (
    "AI সহায়তাপ্রাপ্ত — শুধুমাত্র মানব পর্যালোচনার জন্য। "
    "AI কোনো আইনি সিদ্ধান্ত নিতে পারে না।"
)

BN_DIGIT_MAP = str.maketrans('০১২৩৪৫৬৭৮৯', '0123456789')


def normalize_digits(text):
    if not text:
        return ""
    return str(text).translate(BN_DIGIT_MAP)


def _detect_marma_intent(text):
    text_l = text.lower()
    marma_signals = [
        'মারমা', 'marma', 'মারমা ভাষা', 'মারমা ভাষায়', 'marma language',
        'আমি মারমা', 'indigenous', 'ক্ষুদ্র নৃ', 'পার্বত্য',
    ]
    return any(s in text_l for s in marma_signals)


def _detect_voice_task_intent(text):
    text_l = text.lower()
    accept_signals = [
        'গ্রহণ করছি', 'accept', 'acknowledge', 'গ্রহণ', 'সম্পন্ন করুন', 'complete task',
        'রেফারেল গ্রহণ', 'রেফারেলটি গ্রহণ', 'কাজটি সম্পন্ন',
    ]
    if any(s in text_l for s in accept_signals):
        return {'intent': 'acknowledge_referral'}
    return None


def _classify_legal_topic(text):
    text_l = text.lower()
    if any(w in text_l for w in ['জমি', 'সম্পত্তি', 'land', 'property', 'সীমানা', 'boundary', 'khatian']):
        return 'land_property'
    if any(w in text_l for w in ['যৌতুক', 'দেনমোহর', 'তালাক', 'dowry', 'divorce', 'family', 'পারিবারিক']):
        return 'family_law'
    if any(w in text_l for w in ['বেতন', 'মজুরি', 'wage', 'salary', 'labour', 'শ্রম', 'কারখানা']):
        return 'labour_law'
    if any(w in text_l for w in ['মারধর', 'নির্যাতন', 'violence', 'assault', 'threat', 'হুমকি']):
        return 'protection'
    return 'general'


# ---------------------------------------------------------------------------
# Conversational Application Intake Manager
# ---------------------------------------------------------------------------

class ConversationalIntakeManager:
    """
    Manages conversational legal aid application intake:
    - Step-by-step required field collection matching CitizenApplicationForm
    - Rejects invalid inputs and prompts for corrections
    - Displays clear summary review card before submission
    - Enforces explicit applicant confirmation
    - Submits via existing submit_application workflow
    - Strictly assistive: never makes legal eligibility or officer decisions
    """
    REQUIRED_SLOTS = [
        'name',
        'phone',
        'address',
        'legal_problem',
        'incident_description',
    ]
    OPTIONAL_SLOTS = [
        'safe_contact_number',
        'safe_contact_time',
        'nid_number',
    ]

    SLOT_PROMPTS = {
        'name': {
            'bn': "ডিজিটাল আইনি সহায়তা ব্যবস্থায় (DLAS) স্বাগতম। আপনার আবেদন শুরু করতে প্রথমে আপনার পুরো নাম কী লিখুন।",
            'en': "Welcome to the Digital Legal Aid System (DLAS). To begin your application, what is your full legal name?",
        },
        'phone': {
            'bn': "আপনার সচল যোগাযোগ ফোন নম্বরটি লিখুন (যেমন: 01XXXXXXXXX - কমপক্ষে ৬ ডিজিট)।",
            'en': "What is your active contact phone number (e.g., 01XXXXXXXXX - at least 6 digits)?",
        },
        'address': {
            'bn': "আপনার বর্তমান ঠিকানা কী (গ্রাম/মহল্লা, উপজেলা/থানা, জেলা)?",
            'en': "What is your current address (Village/Area, Upazila/Thana, District)?",
        },
        'legal_problem': {
            'bn': "আপনার আইনি সমস্যাটি কী বিষয় সংক্রান্ত? (যেমন: জমিজমা বিরোধ, দেনমোহর/পারিবারিক, বকেয়া বেতন ইত্যাদি - কমপক্ষে ৫ অক্ষর)",
            'en': "What type of legal problem are you facing? (e.g., Land dispute, Family & Dowry, Unpaid wages - at least 5 characters)",
        },
        'incident_description': {
            'bn': "ঘটনাটির বিস্তারিত বিবরণ দিন (কখন ঘটেছে, বিরোধী পক্ষ কে এবং তারা কী করেছে - কমপক্ষে ১০ অক্ষর)।",
            'en': "Please describe the incident in detail (what happened, when it occurred, who is opposing party - at least 10 characters).",
        },
    }

    SLOT_LABELS = {
        'name': {'bn': 'পূর্ণ নাম', 'en': 'Full Legal Name'},
        'phone': {'bn': 'ফোন নম্বর', 'en': 'Contact Phone'},
        'address': {'bn': 'ঠিকানা', 'en': 'Current Address'},
        'legal_problem': {'bn': 'আইনি সমস্যা', 'en': 'Legal Problem'},
        'incident_description': {'bn': 'ঘটনার বিবরণ', 'en': 'Incident Description'},
        'safe_contact_number': {'bn': 'বিকল্প নিরাপদ ফোন', 'en': 'Safe Contact Phone'},
        'safe_contact_time': {'bn': 'যোগাযোগের নিরাপদ সময়', 'en': 'Safe Contact Time'},
        'nid_number': {'bn': 'জাতীয় পরিচয়পত্র নম্বর', 'en': 'NID Number'},
    }

    @classmethod
    def detect_start_intent(cls, text):
        t = (text or '').lower()
        patterns = [
            'apply', 'application', 'file application', 'start application',
            'legal aid application', 'help me apply', 'intake',
            'আবেদন', 'আবেদন করতে চাই', 'আইনি সহায়তা চাই', 'আইনি সহায়তা চাই',
            'আবেদন শুরু', 'দরখাস্ত', 'আবেদন করব', 'সহায়তা চাই', 'সহায়তা চাই',
        ]
        return any(p in t for p in patterns)

    @classmethod
    def detect_cancel_intent(cls, text):
        t = (text or '').lower().strip()
        patterns = ['cancel', 'stop', 'বাতিল', 'বাতিল করুন', 'বন্ধ করুন', 'দরকার নেই']
        return any(p == t or t.startswith(p + ' ') or t.endswith(' ' + p) for p in patterns)

    @classmethod
    def detect_manual_form_intent(cls, text):
        t = (text or '').lower().strip()
        patterns = [
            'manual', 'manual form', 'switch to manual',
            'ম্যানুয়াল', 'ম্যানুয়াল', 'ফরম', 'ফর্ম', 'ম্যানুয়াল ফর্ম', 'ম্যানুয়াল ফর্ম',
        ]
        return any(p in t for p in patterns)

    @classmethod
    def detect_confirm_intent(cls, text):
        t = (text or '').lower().strip()
        patterns = [
            'confirm', 'yes', 'submit', 'i confirm', 'submit application',
            'হ্যাঁ', 'হ্যা', 'নিশ্চিত', 'নিশ্চিত করুন', 'জমা দিন', 'আবেদন জমা দিন',
        ]
        return any(p == t or t.startswith(p + ' ') or t.endswith(' ' + p) for p in patterns)

    @classmethod
    def init_state(cls, lang='bn'):
        return {
            'active': True,
            'status': 'in_progress',  # in_progress, review, confirmed, cancelled, manual_form
            'current_slot': 'name',
            'slots': {
                'name': None,
                'phone': None,
                'address': None,
                'legal_problem': None,
                'incident_description': None,
                'safe_contact_number': None,
                'safe_contact_time': None,
                'nid_number': None,
            },
            'validation_error': None,
            'application_id': None,
            'language': lang,
        }

    @classmethod
    def get_next_missing_slot(cls, slots):
        for slot in cls.REQUIRED_SLOTS:
            val = slots.get(slot)
            if not val or not str(val).strip():
                return slot
        return None

    @classmethod
    def validate_slot_value(cls, slot, value):
        val = (value or '').strip()
        if not val:
            return None, "Field cannot be empty / ঘরটি খালি রাখা যাবে না।"

        if slot == 'name':
            if len(val) < 2:
                return None, "Name must be at least 2 characters long / নাম কমপক্ষে ২ অক্ষরের হতে হবে।"
            if val.isdigit():
                return None, "Name cannot be only numbers / নাম শুধুমাত্র সংখ্যা হতে পারে না।"
            return val, None

        elif slot == 'phone':
            norm = normalize_digits(val)
            digits = re.sub(r'[^\d+]', '', norm)
            if len(digits) < 6:
                return None, "Please provide a valid contact phone number (at least 6 digits) / অনুগ্রহ করে একটি সঠিক ফোন নম্বর লিখুন (কমপক্ষে ৬ ডিজিট)।"
            return norm, None

        elif slot == 'address':
            if len(val) < 3 or val.isdigit():
                return None, "Please provide a valid address / অনুগ্রহ করে সঠিক ঠিকানা লিখুন।"
            return val, None

        elif slot == 'legal_problem':
            if len(val) < 5:
                return None, "Please describe your legal problem in more detail (at least 5 characters) / আইনি সমস্যাটি অনুগ্রহ করে একটু বিস্তারিত লিখুন (কমপক্ষে ৫ অক্ষর)।"
            return val, None

        elif slot == 'incident_description':
            if len(val) < 10:
                return None, "Please provide a description of the incident of at least 10 characters / ঘটনার বিবরণ কমপক্ষে ১০ অক্ষরের হতে হবে।"
            return val, None

        elif slot == 'nid_number':
            norm = normalize_digits(val)
            return norm, None

        return val, None

    @classmethod
    def extract_and_validate(cls, text, state):
        current_slot = state.get('current_slot')
        slots = state.get('slots', {})
        norm_text = normalize_digits(text).strip()
        extracted = {}

        # 1. Explicit correction checks
        phone_corr = re.search(r'(?:ফোন|মোবাইল|phone|mobile)\s*(?:হবে|হলো|is|should be|:)?\s*([0-9+]{6,16})', norm_text, re.I)
        if phone_corr:
            val, err = cls.validate_slot_value('phone', phone_corr.group(1))
            if err:
                return {}, err
            extracted['phone'] = val

        name_corr = re.search(r'(?:নাম|name)\s*(?:হবে|হলো|is|should be|:)?\s*([^\n,।,]+)', text, re.I)
        if name_corr and ('নাম' in text or 'name' in text.lower()):
            cand = re.sub(r'^(?:হবে|হলো|is|should be|:|=)\s*', '', name_corr.group(1), flags=re.I).strip()
            if cand:
                val, err = cls.validate_slot_value('name', cand)
                if not err:
                    extracted['name'] = val

        addr_corr = re.search(r'(?:ঠিকানা|address)\s*(?:হবে|হলো|is|should be|:)?\s*([^\n।]+)', text, re.I)
        if addr_corr and ('ঠিকানা' in text or 'address' in text.lower()):
            cand = re.sub(r'^(?:হবে|হলো|is|should be|:|=)\s*', '', addr_corr.group(1), flags=re.I).strip()
            if cand:
                val, err = cls.validate_slot_value('address', cand)
                if not err:
                    extracted['address'] = val

        if extracted:
            return extracted, None

        # 2. Pattern extractions (multi-slot mentions)
        phone_match = re.search(r'\b(01[3-9]\d{8})\b', norm_text)
        if phone_match and not slots.get('phone'):
            val, err = cls.validate_slot_value('phone', phone_match.group(1))
            if not err:
                extracted['phone'] = val

        name_match = re.search(r'(?:আমার\s+নাম(?: হলো| হচ্ছে)?|my\s+name\s+is)\s+([^\n।,]+)', text, re.I)
        if name_match and not slots.get('name'):
            cand = name_match.group(1).strip()
            val, err = cls.validate_slot_value('name', cand)
            if not err:
                extracted['name'] = val

        addr_match = re.search(r'(?:আমার\s+ঠিকানা(?: হলো| হচ্ছে)?|my\s+address\s+is)\s+([^\n।,]+)', text, re.I)
        if addr_match and not slots.get('address'):
            cand = addr_match.group(1).strip()
            val, err = cls.validate_slot_value('address', cand)
            if not err:
                extracted['address'] = val

        prob_match = re.search(r'(?:আমার\s+([^\n।,]+?)\s*নিয়ে সমস্যা|legal problem is\s+([^\n,.]+))', text, re.I)
        if prob_match and not slots.get('legal_problem'):
            cand = (prob_match.group(1) or prob_match.group(2) or '').strip()
            val, err = cls.validate_slot_value('legal_problem', cand)
            if not err:
                extracted['legal_problem'] = val

        inc_match = re.search(r'(?:ঘটনাটি হলো|ঘটনার বিবরণ(?: হলো| হচ্ছে|:)?|incident is|incident description is)\s+([^\n।]+)', text, re.I)
        if inc_match and not slots.get('incident_description'):
            cand = inc_match.group(1).strip()
            val, err = cls.validate_slot_value('incident_description', cand)
            if not err:
                extracted['incident_description'] = val

        if extracted:
            return extracted, None

        # 3. Contextual fallback for current expected slot
        if current_slot:
            clean_val = text.strip()
            # If user message is initiating the application intake, do not treat the start phrase as a slot value
            if cls.detect_start_intent(clean_val):
                return {}, None

            if current_slot == 'name':
                clean_val = re.sub(r'^(?:আমার নাম(?: হলো| হচ্ছে)?|my name is|name is|আমি|নাম)\s*[:=]?\s*', '', clean_val, flags=re.I).strip()
            elif current_slot == 'address':
                clean_val = re.sub(r'^(?:আমার ঠিকানা(?: হলো| হচ্ছে)?|my address is|address is|ঠিকানা)\s*[:=]?\s*', '', clean_val, flags=re.I).strip()
            elif current_slot == 'legal_problem':
                clean_val = re.sub(r'^(?:আমার সমস্যা(?: হলো| হচ্ছে)?|my problem is|problem is|সমস্যা)\s*[:=]?\s*', '', clean_val, flags=re.I).strip()
            elif current_slot == 'incident_description':
                clean_val = re.sub(r'^(?:ঘটনাটি হলো|ঘটনা(?: হলো| হচ্ছে)?|incident is|description is)\s*[:=]?\s*', '', clean_val, flags=re.I).strip()

            val, err = cls.validate_slot_value(current_slot, clean_val)
            if err:
                return {}, err
            extracted[current_slot] = val
            return extracted, None

        return {}, None

    @classmethod
    def generate_review_summary(cls, slots, lang='bn'):
        name = slots.get('name') or "—"
        phone = slots.get('phone') or "—"
        address = slots.get('address') or "—"
        problem = slots.get('legal_problem') or "—"
        incident = slots.get('incident_description') or "—"
        safe_contact = slots.get('safe_contact_number') or ("প্রযোজ্য নয়" if lang == 'bn' else "N/A")
        safe_time = slots.get('safe_contact_time') or ("প্রযোজ্য নয়" if lang == 'bn' else "Standard")
        nid = slots.get('nid_number') or ("প্রযোজ্য নয়" if lang == 'bn' else "N/A")

        summary_bn = (
            "📋 আপনার আবেদন তথ্যের সারসংক্ষেপ:\n\n"
            f"• পূর্ণ নাম: {name}\n"
            f"• যোগাযোগ ফোন: {phone}\n"
            f"• বর্তমান ঠিকানা: {address}\n"
            f"• আইনি সমস্যা: {problem}\n"
            f"• ঘটনার বিবরণ: {incident}\n"
            f"• নিরাপদ বিকল্প যোগাযোগ: {safe_contact} ({safe_time})\n"
            f"• জাতীয় পরিচয়পত্র (ঐচ্ছিক): {nid}\n\n"
            "⚠️ আপনি কি এই তথ্য দিয়ে আবেদন জমা দিতে চান?\n"
            "জমা দিতে 'নিশ্চিত' বা 'হ্যাঁ' বলুন অথবা নিচের 'আবেদন জমা দিন' বাটনে চাপুন।\n"
            "কোনো তথ্য সংশোধন করতে চাইলে তা লিখুন, অথবা সরাসরি ম্যানুয়াল ফর্মে যেতে চাইলে 'ম্যানুয়াল ফর্ম' বলুন।"
        )

        summary_en = (
            "📋 Application Information Summary:\n\n"
            f"• Full Legal Name: {name}\n"
            f"• Contact Phone: {phone}\n"
            f"• Current Address: {address}\n"
            f"• Legal Problem: {problem}\n"
            f"• Incident Description: {incident}\n"
            f"• Safe Contact Phone: {safe_contact} ({safe_time})\n"
            f"• NID Number (Optional): {nid}\n\n"
            "⚠️ Would you like to submit your application with this information?\n"
            "To submit, reply 'CONFIRM' or 'YES' or click the 'Submit Application' button below.\n"
            "To edit, state the correction, or say 'manual form' to fill out the form manually."
        )

        return summary_en, summary_bn

    @classmethod
    def submit_intake_application(cls, state, user=None, channel='web', lang='bn'):
        slots = state.get('slots', {})
        missing = cls.get_next_missing_slot(slots)
        if missing:
            raise ValidationError(f"Cannot submit application: missing required field '{missing}'.")

        form_data = {
            'name': slots.get('name') or '',
            'phone': slots.get('phone') or '',
            'address': slots.get('address') or '',
            'legal_problem': slots.get('legal_problem') or '',
            'incident_description': slots.get('incident_description') or '',
            'preferred_channel': channel or Application.CHANNEL_WEB,
            'safe_contact_number': slots.get('safe_contact_number') or '',
            'safe_contact_time': slots.get('safe_contact_time') or '',
            'nid_number': slots.get('nid_number') or '',
            'language': lang or 'bn',
        }

        form = CitizenApplicationForm(form_data)
        if not form.is_valid():
            raise ValidationError(form.errors)

        cleaned = form.cleaned_data

        app = submit_application(
            name=cleaned['name'],
            phone=cleaned['phone'],
            address=cleaned['address'],
            legal_problem=cleaned['legal_problem'],
            incident_description=cleaned['incident_description'],
            applicant_user=user if (user and user.is_authenticated) else None,
            preferred_channel=cleaned.get('preferred_channel') or Application.CHANNEL_WEB,
            safe_contact_number=cleaned.get('safe_contact_number') or '',
            safe_contact_time=cleaned.get('safe_contact_time') or '',
            language=cleaned.get('language') or 'bn',
            nid_number=cleaned.get('nid_number') or '',
            actor=user if (user and user.is_authenticated) else None,
            provenance=CaseEvent.PROVENANCE_APPLICANT_CONFIRMED,
        )

        actor_role = getattr(user, 'profile', None).role if (user and hasattr(user, 'profile')) else 'citizen'
        CaseEvent.objects.create(
            application=app,
            case=None,
            actor=user if (user and user.is_authenticated) else None,
            actor_role=actor_role,
            channel=channel or 'web',
            action='conversational_intake_confirmed',
            description=(
                f"Applicant reviewed and confirmed intake details via Conversational AI. "
                f"Generated Application ID: {app.application_id}."
            ),
            provenance=CaseEvent.PROVENANCE_APPLICANT_CONFIRMED,
            authority='applicant_personal_confirmation',
        )

        state['status'] = 'confirmed'
        state['application_id'] = app.application_id
        return app

    @classmethod
    def process_turn(cls, message, state, user=None, lang='bn'):
        msg = (message or '').strip()
        current_status = state.get('status', 'in_progress')

        # 1. Cancellation check
        if cls.detect_cancel_intent(msg):
            state['status'] = 'cancelled'
            state['active'] = False
            reply_bn = "আপনার কথোপকথনভিত্তিক আবেদন প্রক্রিয়াটি বাতিল করা হয়েছে। আপনি পরবর্তীতে যেকোনো সময় পুনরায় শুরু করতে পারেন।"
            reply_en = "Your conversational application process has been cancelled. You can start again anytime."
            return {
                'reply_bn': reply_bn,
                'reply_en': reply_en,
                'intake_active': False,
                'intake_status': 'cancelled',
                'suggested_action': None,
            }

        # 2. Switch to manual form check
        if cls.detect_manual_form_intent(msg):
            state['status'] = 'manual_form'
            state['active'] = False
            reply_bn = "আপনি সরাসরি ম্যানুয়াল ফর্মে আবেদন করতে পারেন: <a href='/cases/apply/'>ম্যানুয়াল আবেদন ফর্ম পূরণ করুন</a>।"
            reply_en = "You can fill out the application manually anytime: <a href='/cases/apply/'>Manual Application Form</a>."
            return {
                'reply_bn': reply_bn,
                'reply_en': reply_en,
                'intake_active': False,
                'intake_status': 'manual_form',
                'manual_form_url': '/cases/apply/',
                'suggested_action': 'manual_form',
            }

        # 3. Confirmation check (when in review state)
        if current_status == 'review':
            if cls.detect_confirm_intent(msg):
                try:
                    app = cls.submit_intake_application(state, user=user, lang=lang)
                    state['active'] = False
                    reply_bn = (
                        f"🎉 আপনার আবেদনটি সফলভাবে জমা দেওয়া হয়েছে!\n\n"
                        f"• আবেদন নম্বর: {app.application_id}\n"
                        f"• স্থিতি: জমা দেওয়া হয়েছে (Submitted)\n\n"
                        f"জেলা আইনি সহায়তা কর্মকর্তা (DLAO) এটি পর্যালোচনা করবেন। আপনি ড্যাশবোর্ড থেকে আবেদনের অগ্রগতি পর্যবেক্ষণ করতে পারবেন।"
                    )
                    reply_en = (
                        f"🎉 Your application has been successfully submitted!\n\n"
                        f"• Application ID: {app.application_id}\n"
                        f"• Status: Submitted\n\n"
                        f"A District Legal Aid Officer (DLAO) will review your application. You can track its progress on your dashboard."
                    )
                    return {
                        'reply_bn': reply_bn,
                        'reply_en': reply_en,
                        'intake_active': False,
                        'intake_status': 'confirmed',
                        'application_id': app.application_id,
                        'suggested_action': 'view_application',
                    }
                except ValidationError as ve:
                    err_msg = str(ve)
                    reply_bn = f"আবেদন তথ্যে ত্রুটি রয়েছে: {err_msg}। অনুগ্রহ করে সংশোধন করুন।"
                    reply_en = f"There is an error with the application details: {err_msg}. Please correct it."
                    return {
                        'reply_bn': reply_bn,
                        'reply_en': reply_en,
                        'intake_active': True,
                        'intake_status': 'review',
                        'suggested_action': 'confirm_submission',
                    }

        # 4. Extract and validate input
        extracted, validation_err = cls.extract_and_validate(msg, state)

        if validation_err:
            reply_bn = f"⚠️ {validation_err}\n\nঅনুগ্রহ করে সঠিক তথ্যটি প্রদান করুন।"
            reply_en = f"⚠️ {validation_err}\n\nPlease provide the correct information."
            return {
                'reply_bn': reply_bn,
                'reply_en': reply_en,
                'intake_active': True,
                'intake_status': state['status'],
                'current_slot': state.get('current_slot'),
                'suggested_action': 'switch_to_manual',
            }

        for k, v in extracted.items():
            if k in state['slots']:
                state['slots'][k] = v

        # 5. Check next missing required slot
        missing = cls.get_next_missing_slot(state['slots'])
        if missing:
            state['status'] = 'in_progress'
            state['current_slot'] = missing
            prompt = cls.SLOT_PROMPTS[missing]
            ack_bn = ""
            ack_en = ""
            if extracted:
                recent_k = list(extracted.keys())[-1]
                label_bn = cls.SLOT_LABELS.get(recent_k, {}).get('bn', 'তথ্য')
                label_en = cls.SLOT_LABELS.get(recent_k, {}).get('en', 'Detail')
                ack_bn = f"আপনার {label_bn} গ্রহণ করা হয়েছে। "
                ack_en = f"Your {label_en} has been recorded. "

            reply_bn = f"{ack_bn}{prompt['bn']}"
            reply_en = f"{ack_en}{prompt['en']}"
            return {
                'reply_bn': reply_bn,
                'reply_en': reply_en,
                'intake_active': True,
                'intake_status': 'in_progress',
                'current_slot': missing,
                'suggested_action': 'switch_to_manual',
            }
        else:
            # All required slots are filled -> review summary screen
            state['status'] = 'review'
            state['current_slot'] = None
            rev_en, rev_bn = cls.generate_review_summary(state['slots'], lang=lang)
            return {
                'reply_bn': rev_bn,
                'reply_en': rev_en,
                'intake_active': True,
                'intake_status': 'review',
                'suggested_action': 'confirm_submission',
            }


# ---------------------------------------------------------------------------
# Mock Universal Chat Service
# ---------------------------------------------------------------------------

class MockUniversalChatService:
    IS_SIMULATED = True
    LABEL_EN = "SIMULATED AI VOICE CHAT"
    LABEL_BN = "সিমুলেটেড AI ভয়েস চ্যাট"

    _RESPONSES = {
        'greeting': {
            'en': (
                "Hello! I'm the DLAS AI Voice Chat assistant. I can help you with legal aid intake, "
                "questions about your application, or connect you with the right service. "
                "How can I help you today?"
            ),
            'bn': (
                "নমস্কার! আমি ডিএলএএস AI ভয়েস চ্যাট সহকারী। আমি আইনি সহায়তা আবেদন, "
                "আপনার আবেদনের অগ্রগতি বা সঠিক সেবায় সংযুক্ত করতে সাহায্য করতে পারি। "
                "আজ আমি আপনার জন্য কী করতে পারি?"
            ),
        },
        'land_property': {
            'en': (
                "I understand you have a land or property-related legal issue. "
                "DLAS can assist with land disputes, boundary conflicts, eviction threats, and title matters. "
                "Would you like to start a legal aid application? I can guide you step by step."
            ),
            'bn': (
                "আমি বুঝতে পারছি আপনার জমি বা সম্পত্তি সংক্রান্ত আইনি সমস্যা রয়েছে। "
                "ডিএলএএস জমি বিরোধ, সীমানা বিরোধ, উচ্ছেদের হুমকি ও দলিল সংক্রান্ত সমস্যায় সহায়তা করে। "
                "আপনি কি একটি আইনি সহায়তার আবেদন শুরু করতে চান?"
            ),
        },
        'family_law': {
            'en': (
                "Family and domestic law issues — dowry, divorce, child custody, or maintenance — "
                "are covered under DLAS support. Your privacy and safety are our priority. "
                "Would you like to start an application?"
            ),
            'bn': (
                "পারিবারিক আইন সংক্রান্ত সমস্যা — যৌতুক, তালাক, সন্তানের অভিভাবকত্ব বা ভরণপোষণ — "
                "ডিএলএএস এর অন্তর্ভুক্ত। আপনার গোপনীয়তা ও নিরাপত্তা আমাদের অগ্রাধিকার। "
                "আপনি কি আবেদন শুরু করতে চান?"
            ),
        },
        'labour_law': {
            'en': (
                "Labour and wage disputes — unpaid salary, wrongful termination, or workplace harassment — "
                "can be addressed through DLAS. Shall I help you start an application?"
            ),
            'bn': (
                "শ্রম ও বেতন সংক্রান্ত বিরোধ — বকেয়া বেতন, অন্যায়ভাবে চাকরিচ্যুতি বা নির্যাতন — "
                "ডিএলএএস এর আইনি সহায়তার আওতায় পড়ে। আমি কি আপনার আবেদন শুরু করতে সাহায্য করব?"
            ),
        },
        'protection': {
            'en': (
                "Personal safety matters are treated with highest urgency. "
                "If you face threats, assault, or domestic violence, DLAS can connect you with immediate legal protection. "
                "Are you safe right now? Would you like to begin an urgent application?"
            ),
            'bn': (
                "ব্যক্তিগত নিরাপত্তার বিষয়গুলো সর্বোচ্চ অগ্রাধিকারে পরিচালিত হয়। "
                "হুমকি, মারধর বা গৃহ সহিংসতার ক্ষেত্রে ডিএলএএস তাৎক্ষণিক আইনি সুরক্ষায় সহায়তা করে। "
                "আপনি কি এখন নিরাপদ? আপনি কি একটি জরুরি আবেদন শুরু করতে চান?"
            ),
        },
        'marma': {
            'en': (
                "I can support intake with Marma language provenance tracking. "
                "The Marma-assisted workflow preserves: "
                "Original Statement → Transcription → AI-assisted Translation → Typed Data → Human Confirmation. "
                "Please use the 'Start Marma Intake' link below to begin."
            ),
            'bn': (
                "আমি মারমা ভাষায় সম্পূর্ণ প্রমাণপথ-সহ আবেদন গ্রহণ করতে পারি। "
                "মারমা-সহায়তা প্রক্রিয়া: মূল বিবৃতি → প্রতিলিপি → AI-সহায়তায় অনুবাদ → টাইপ করা তথ্য → মানবিক নিশ্চিতকরণ। "
                "অনুগ্রহ করে 'মারমা আবেদন শুরু করুন' লিংকটি ব্যবহার করুন।"
            ),
        },
        'voice_task_intent': {
            'en': (
                "It sounds like you want to acknowledge or complete a task. "
                "For security, task completion requires server-side authorization — "
                "please navigate to your assigned task page to confirm."
            ),
            'bn': (
                "মনে হচ্ছে আপনি একটি কাজ গ্রহণ বা সম্পন্ন করতে চান। "
                "নিরাপত্তার কারণে কাজ সম্পন্ন করার জন্য সার্ভার-সাইড অনুমোদন প্রয়োজন — "
                "অনুগ্রহ করে আপনার নির্ধারিত কাজের পাতায় যান।"
            ),
        },
        'general': {
            'en': (
                "I'm here to help with your legal aid needs. "
                "Describe your situation in Bangla or English and I'll help identify the right service. "
                "What's your situation?"
            ),
            'bn': (
                "আমি আপনার আইনি সহায়তার জন্য এখানে আছি। "
                "বাংলা বা ইংরেজিতে আপনার পরিস্থিতি বর্ণনা করুন। "
                "আপনার পরিস্থিতি কী?"
            ),
        },
    }

    @classmethod
    def chat(cls, message, history, lang='bn', intake_state=None, user=None, **kwargs):
        text = (message or '').strip()

        # Conversational application intake handling
        is_intake_active = bool(intake_state and intake_state.get('active'))
        if is_intake_active or ConversationalIntakeManager.detect_start_intent(text):
            if not is_intake_active:
                intake_state = ConversationalIntakeManager.init_state(lang=lang)

            intake_res = ConversationalIntakeManager.process_turn(
                message=text,
                state=intake_state,
                user=user,
                lang=lang,
            )
            res = cls._build_response(
                key='general',
                extra=intake_res,
                reply_en=intake_res.get('reply_en'),
                reply_bn=intake_res.get('reply_bn'),
            )
            res['intake_state'] = intake_state
            return res

        if not text:
            return cls._build_response('greeting', None)

        if _detect_marma_intent(text):
            result = cls._build_response('marma', None)
            result['marma_workflow'] = True
            result['suggested_action'] = 'marma_intake'
            return result

        task_intent = _detect_voice_task_intent(text)
        if task_intent:
            result = cls._build_response('voice_task_intent', None)
            result['voice_task_intent'] = task_intent
            return result

        greetings = ['hello', 'hi', 'সালাম', 'নমস্কার', 'হ্যালো', 'আসসালামু', 'shalom']
        if not history and any(g in text.lower() for g in greetings):
            return cls._build_response('greeting', None)

        topic = _classify_legal_topic(text)
        return cls._build_response(topic, None)

    @classmethod
    def transcribe_audio(cls, audio_bytes, filename='audio.webm'):
        return {
            'transcript': 'আমার জমি নিয়ে সমস্যা হয়েছে (simulated transcription)',
            'is_simulated': True,
            'language': 'bn',
        }

    @classmethod
    def _build_response(cls, key, extra, reply_en=None, reply_bn=None):
        if reply_en is not None and reply_bn is not None:
            bank = {'en': reply_en, 'bn': reply_bn}
        else:
            bank = cls._RESPONSES.get(key, cls._RESPONSES['general'])
        result = {
            'reply_en': bank['en'],
            'reply_bn': bank['bn'],
            'is_simulated': True,
            'marma_workflow': False,
            'voice_task_intent': None,
            'suggested_action': None,
            'disclaimer_en': AI_SAFETY_DISCLAIMER_EN,
            'disclaimer_bn': AI_SAFETY_DISCLAIMER_BN,
            'label_en': cls.LABEL_EN,
            'label_bn': cls.LABEL_BN,
        }
        if extra:
            result.update(extra)
        return result


# ---------------------------------------------------------------------------
# Shared System Prompt
# ---------------------------------------------------------------------------

DLAS_AI_SYSTEM_PROMPT = (
    "You are the DLAS (Digital Legal Aid System) AI assistant for Bangladesh. "
    "You help citizens, including vulnerable communities and minority language speakers, "
    "access legal aid services. "
    "You can conversationally assist citizens with completing their legal aid application step-by-step. "
    "When assisting with applications: "
    "- Collect the required details step-by-step: full legal name, contact phone, address, legal problem, and incident description. "
    "- Ask for missing information one step at a time instead of asking for everything at once. "
    "- Understand both Bangla and English input and respond appropriately. "
    "- Do NOT invent information on behalf of the citizen. "
    "- When all details are collected, ask the citizen to review and explicitly confirm before submission. "
    "- If the citizen wants to switch to manual form filling, allow them to do so at /cases/apply/. "
    "You are ASSISTIVE ONLY — you cannot determine legal eligibility, reject applications, "
    "approve applications, assign lawyers, or make any final legal or officer decisions. "
    "Respond bilingually (English and Bangla) unless the user clearly prefers one language. "
    "Keep responses concise and compassionate. "
    "If the user mentions Marma language or a Marma-speaking user, "
    "let them know about the Marma-assisted intake workflow. "
    "If the user seems to want to complete a task or acknowledge a referral, "
    "remind them that actual task completion requires authentication on their task page — "
    "you cannot perform it on their behalf. "
    "Do NOT invent legal facts. If unsure, say so and recommend speaking to a DLAO officer."
)


# ---------------------------------------------------------------------------
# Real Gemini Service (google-genai SDK)
# ---------------------------------------------------------------------------

class RealGeminiService:
    IS_SIMULATED = False
    LABEL_EN = "AI VOICE CHAT (Gemini)"
    LABEL_BN = "AI ভয়েস চ্যাট (Gemini)"
    SYSTEM_PROMPT = DLAS_AI_SYSTEM_PROMPT
    MODELS = ['gemini-flash-lite-latest', 'gemini-3.5-flash-lite', 'gemini-3.8-flash']

    @classmethod
    def _get_client(cls):
        from google import genai
        key = getattr(settings, 'GEMINI_API_KEY', '').strip()
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not configured.")
        return genai.Client(api_key=key)

    @classmethod
    def _build_response_dict(cls, reply_en, reply_bn, extra=None):
        result = {
            'reply_en': reply_en,
            'reply_bn': reply_bn,
            'is_simulated': False,
            'marma_workflow': False,
            'voice_task_intent': None,
            'suggested_action': None,
            'disclaimer_en': AI_SAFETY_DISCLAIMER_EN,
            'disclaimer_bn': AI_SAFETY_DISCLAIMER_BN,
            'label_en': cls.LABEL_EN,
            'label_bn': cls.LABEL_BN,
        }
        if extra:
            result.update(extra)
        return result

    @classmethod
    def chat(cls, message, history, lang='bn', intake_state=None, user=None, **kwargs):
        text = (message or '').strip()

        # Conversational application intake handling
        is_intake_active = bool(intake_state and intake_state.get('active'))
        if is_intake_active or ConversationalIntakeManager.detect_start_intent(text):
            if not is_intake_active:
                intake_state = ConversationalIntakeManager.init_state(lang=lang)

            intake_res = ConversationalIntakeManager.process_turn(
                message=text,
                state=intake_state,
                user=user,
                lang=lang,
            )

            status = intake_res.get('intake_status')
            # Authoritative transitions: confirmed, cancelled, manual form, review card, or validation error
            if status in ('confirmed', 'cancelled', 'manual_form', 'review') or '⚠️' in intake_res.get('reply_bn', ''):
                res = cls._build_response_dict(intake_res['reply_en'], intake_res['reply_bn'], extra=intake_res)
                res['intake_state'] = intake_state
                return res

            # For in_progress step prompts, attempt to get warm Gemini wording with slot guidance
            try:
                from google import genai
                from google.genai import types
                client = cls._get_client()

                system_prompt = (
                    f"{cls.SYSTEM_PROMPT}\n\n"
                    f"CURRENT APPLICATION INTAKE ASSISTANCE:\n"
                    f"You are helping a citizen fill out a legal aid application step-by-step.\n"
                    f"Required question to ask next: {intake_res['reply_bn']} (in Bangla) or {intake_res['reply_en']} (in English).\n"
                    f"Acknowledge the citizen politely, then ask this exact required question.\n"
                    f"Do NOT ask for multiple pieces of information at once. Ask ONLY for this next detail."
                )

                contents = []
                for turn in (history or [])[-4:]:
                    role = 'user' if turn.get('role') == 'user' else 'model'
                    t_val = turn.get('text_en') or turn.get('text_bn') or ''
                    if t_val:
                        contents.append(types.Content(role=role, parts=[types.Part.from_text(text=t_val)]))
                contents.append(types.Content(role='user', parts=[types.Part.from_text(text=text)]))

                config = types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    max_output_tokens=300,
                    temperature=0.4,
                )

                reply = None
                for model_name in cls.MODELS:
                    try:
                        response = client.models.generate_content(
                            model=model_name,
                            contents=contents,
                            config=config,
                        )
                        reply = (response.text or '').strip()
                        if reply:
                            break
                    except Exception:
                        continue

                if reply:
                    intake_res['reply_en'] = reply
                    intake_res['reply_bn'] = reply
            except Exception:
                pass  # Fall back cleanly to deterministic prompt

            res = cls._build_response_dict(intake_res['reply_en'], intake_res['reply_bn'], extra=intake_res)
            res['intake_state'] = intake_state
            return res

        # Standard conversation flow
        try:
            from google import genai
            from google.genai import types
            client = cls._get_client()

            contents = []
            for turn in (history or [])[-6:]:
                role = 'user' if turn.get('role') == 'user' else 'model'
                t_val = turn.get('text_en') or turn.get('text_bn') or ''
                if t_val:
                    contents.append(types.Content(role=role, parts=[types.Part.from_text(text=t_val)]))
            contents.append(types.Content(role='user', parts=[types.Part.from_text(text=text)]))

            config = types.GenerateContentConfig(
                system_instruction=cls.SYSTEM_PROMPT,
                max_output_tokens=400,
                temperature=0.5,
            )

            reply = None
            last_err = None
            for model_name in cls.MODELS:
                try:
                    response = client.models.generate_content(
                        model=model_name,
                        contents=contents,
                        config=config,
                    )
                    reply = (response.text or '').strip()
                    if reply:
                        break
                except Exception as e:
                    last_err = e
                    continue

            if not reply:
                raise RuntimeError(f"Gemini generation error: {last_err}")

            marma = _detect_marma_intent(text)
            task_intent = _detect_voice_task_intent(text)

            return cls._build_response_dict(
                reply_en=reply,
                reply_bn=reply,
                extra={
                    'marma_workflow': marma,
                    'voice_task_intent': task_intent,
                    'suggested_action': 'marma_intake' if marma else None,
                }
            )
        except ImportError:
            raise RuntimeError("google-genai package not installed. Run: pip install google-genai")
        except Exception as e:
            raise RuntimeError(f"Gemini chat error: {e}")

    @classmethod
    def transcribe_audio(cls, audio_bytes, filename='audio.webm'):
        try:
            from google import genai
            from google.genai import types
            client = cls._get_client()

            ext = (filename or '').rsplit('.', 1)[-1].lower() if '.' in (filename or '') else 'webm'
            mime_map = {
                'webm': 'audio/webm',
                'wav': 'audio/wav',
                'mp3': 'audio/mp3',
                'ogg': 'audio/ogg',
                'm4a': 'audio/m4a',
            }
            mime_type = mime_map.get(ext, 'audio/webm')

            part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)
            prompt = (
                "Please accurately transcribe the spoken words in this audio into text. "
                "Preserve the language spoken (Bangla or English). "
                "Return ONLY the plain transcription text, with no preamble, markdown, or commentary."
            )

            response = None
            last_err = None
            for model_name in cls.MODELS:
                try:
                    response = client.models.generate_content(
                        model=model_name,
                        contents=[part, prompt],
                    )
                    if response and response.text:
                        break
                except Exception as e:
                    last_err = e
                    continue

            if not response or not response.text:
                raise RuntimeError(f"Gemini transcription error: {last_err}")

            transcript = response.text.strip()
            return {
                'transcript': transcript,
                'is_simulated': False,
                'language': 'auto',
            }
        except ImportError:
            raise RuntimeError("google-genai package not installed.")
        except Exception as e:
            raise RuntimeError(f"Gemini transcription error: {e}")


# ---------------------------------------------------------------------------
# Real OpenAI Service
# ---------------------------------------------------------------------------

class RealOpenAIService:
    IS_SIMULATED = False
    LABEL_EN = "AI VOICE CHAT (OpenAI)"
    LABEL_BN = "AI ভয়েস চ্যাট (OpenAI)"
    SYSTEM_PROMPT = DLAS_AI_SYSTEM_PROMPT

    @classmethod
    def _build_response_dict(cls, reply_en, reply_bn, extra=None):
        result = {
            'reply_en': reply_en,
            'reply_bn': reply_bn,
            'is_simulated': False,
            'marma_workflow': False,
            'voice_task_intent': None,
            'suggested_action': None,
            'disclaimer_en': AI_SAFETY_DISCLAIMER_EN,
            'disclaimer_bn': AI_SAFETY_DISCLAIMER_BN,
            'label_en': cls.LABEL_EN,
            'label_bn': cls.LABEL_BN,
        }
        if extra:
            result.update(extra)
        return result

    @classmethod
    def chat(cls, message, history, lang='bn', intake_state=None, user=None, **kwargs):
        text = (message or '').strip()

        # Conversational application intake handling
        is_intake_active = bool(intake_state and intake_state.get('active'))
        if is_intake_active or ConversationalIntakeManager.detect_start_intent(text):
            if not is_intake_active:
                intake_state = ConversationalIntakeManager.init_state(lang=lang)

            intake_res = ConversationalIntakeManager.process_turn(
                message=text,
                state=intake_state,
                user=user,
                lang=lang,
            )
            res = cls._build_response_dict(intake_res['reply_en'], intake_res['reply_bn'], extra=intake_res)
            res['intake_state'] = intake_state
            return res

        try:
            import openai
            client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)

            messages = [{"role": "system", "content": cls.SYSTEM_PROMPT}]
            for turn in (history or [])[-6:]:
                role = turn.get('role', 'user')
                t_val = turn.get('text_en') or turn.get('text_bn') or ''
                if role in ('user', 'assistant') and t_val:
                    messages.append({"role": role, "content": t_val})
            messages.append({"role": "user", "content": text})

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=400,
                temperature=0.5,
            )
            reply = response.choices[0].message.content.strip()
            marma = _detect_marma_intent(text)
            task_intent = _detect_voice_task_intent(text)

            return cls._build_response_dict(
                reply_en=reply,
                reply_bn=reply,
                extra={
                    'marma_workflow': marma,
                    'voice_task_intent': task_intent,
                    'suggested_action': 'marma_intake' if marma else None,
                }
            )
        except ImportError:
            raise RuntimeError("openai package not installed. Run: pip install openai")
        except Exception as e:
            raise RuntimeError(f"OpenAI chat error: {e}")

    @classmethod
    def transcribe_audio(cls, audio_bytes, filename='audio.webm'):
        try:
            import openai
            client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
            audio_file = io.BytesIO(audio_bytes)
            audio_file.name = filename
            transcription = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language=None,
            )
            return {
                'transcript': transcription.text,
                'is_simulated': False,
                'language': 'auto',
            }
        except ImportError:
            raise RuntimeError("openai package not installed.")
        except Exception as e:
            raise RuntimeError(f"OpenAI transcription error: {e}")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_ai_chat_service():
    """
    Returns RealGeminiService if GEMINI_API_KEY is configured,
    or RealOpenAIService if OPENAI_API_KEY is configured (and no Gemini),
    otherwise MockUniversalChatService.
    """
    if getattr(settings, 'GEMINI_API_KEY', ''):
        return RealGeminiService
    if getattr(settings, 'OPENAI_API_KEY', ''):
        return RealOpenAIService
    return MockUniversalChatService

