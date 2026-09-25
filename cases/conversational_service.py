"""
Bangla Conversational Intake AI Service for DLAS
Defined in BATCH 2 Requirements:
- Multi-turn slot filling
- Server-side conversation state
- Deterministic Bangla natural language extraction
- Handling missing information
- Correction handling
- Review before submission
- Explicit applicant confirmation
- Provenance preservation (applicant_confirmed, ai_inferred)
- Strictly ASSISTIVE ONLY: Never makes legal decisions, never creates Case ID.
"""

import re
import uuid
from django.utils import timezone
from cases.models import Application, CaseEvent
from cases.services import submit_application


class BanglaConversationalService:
    """
    Simulated conversational intake service for Bengali natural language intake.
    Maintains server-side slot filling state, deterministic extraction, correction handling,
    and applicant review prior to submission.
    """
    IS_SIMULATED = True
    LABEL_EN = "SIMULATED BANGLA CONVERSATIONAL ASSISTANT"
    LABEL_BN = "সিমুলেটেড বাংলা কথোপকথন সহকারী"
    DISCLAIMER_EN = "ASSISTIVE ONLY — AI cannot make legal decisions. Human confirmation required."
    DISCLAIMER_BN = "শুধুমাত্র সহায়ক — এআই কোনো আইনি সিদ্ধান্ত নিতে পারে না। মানুষের নিশ্চিতকরণ আবশ্যক।"

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

    SLOT_METADATA = {
        'name': {
            'label_en': 'Full Name',
            'label_bn': 'পুরো নাম',
            'prompt_en': 'Welcome to the Digital Legal Aid System (DLAS). What is your full legal name?',
            'prompt_bn': 'ডিজিটাল আইনি সহায়তা ব্যবস্থায় (DLAS) স্বাগতম। আইনি সহায়তার জন্য আপনার পুরো নাম কী?',
        },
        'phone': {
            'label_en': 'Mobile Phone Number',
            'label_bn': 'মোবাইল নম্বর',
            'prompt_en': 'What is your active 11-digit mobile phone number (e.g. 01712345678)?',
            'prompt_bn': 'আপনার যোগাযোগের সচল ১১ ডিজিটের মোবাইল নম্বরটি লিখুন (যেমন: 01712345678)।',
        },
        'address': {
            'label_en': 'Present Address',
            'label_bn': 'বর্তমান ঠিকানা',
            'prompt_en': 'What is your current residence address (Village/Area, Thana/Upazila, District)?',
            'prompt_bn': 'আপনার বর্তমান ঠিকানা কী (গ্রাম/মহল্লা, থানা/উপজেলা, জেলা)?',
        },
        'legal_problem': {
            'label_en': 'Legal Problem Subject',
            'label_bn': 'আইনি সমস্যা',
            'prompt_en': 'What type of legal problem are you facing (e.g. Land dispute, Tenancy eviction, Family & Dowry, Unpaid wages)?',
            'prompt_bn': 'আপনার আইনি সমস্যাটি কী বিষয় সংক্রান্ত (যেমন: জমিজমা বিরোধ, বাড়ি ভাড়া/উচ্ছেদ, দেনমোহর/পারিবারিক, বকেয়া বেতন ইত্যাদি)?',
        },
        'incident_description': {
            'label_en': 'Incident Description',
            'label_bn': 'ঘটনার বিবরণ',
            'prompt_en': 'Please provide the details of what happened (when it occurred, who is the opposing party, what they did).',
            'prompt_bn': 'ঘটনাটির বিস্তারিত বিবরণ দিন (কখন ঘটেছে, বিরোধী পক্ষ কে এবং ঘটনাটি কী ছিল)?',
        },
        'safe_contact_number': {
            'label_en': 'Safe Alternative Phone (Optional)',
            'label_bn': 'নিরাপদ বিকল্প নম্বর (ঐচ্ছিক)',
            'prompt_en': 'Do you have a safe alternative contact number? (Reply "No" if not needed)',
            'prompt_bn': 'আপনার কি কোনো নিরাপদ বিকল্প যোগাযোগ নম্বর আছে? (প্রয়োজন না থাকলে "না" বলুন)',
        },
        'safe_contact_time': {
            'label_en': 'Safe Contact Hours (Optional)',
            'label_bn': 'নিরাপদ যোগাযোগের সময় (ঐচ্ছিক)',
            'prompt_en': 'What is your safe time for communication (e.g. Morning 9am - 12pm)? (Reply "No" if standard hours apply)',
            'prompt_bn': 'যোগাযোগের নিরাপদ সময় কোনটি (যেমন: সকাল ৯টা - ১২টা)? (না থাকলে "না" বলুন)',
        },
    }

    BN_TO_EN_DIGITS = {
        '০': '0', '১': '1', '২': '2', '৩': '3', '৪': '4',
        '৫': '5', '৬': '6', '৭': '7', '৮': '8', '৯': '9',
    }

    @classmethod
    def normalize_digits(cls, text):
        if not text:
            return ""
        out = []
        for ch in str(text):
            out.append(cls.BN_TO_EN_DIGITS.get(ch, ch))
        return "".join(out)

    @classmethod
    def init_session(cls, user=None):
        """
        Initializes a fresh conversation state dict for the session.
        """
        return {
            'session_id': str(uuid.uuid4())[:8],
            'status': 'in_progress',  # in_progress, review, confirmed, cancelled
            'current_slot': 'name',
            'slots': {
                'name': {'value': None, 'provenance': None, 'raw_input': None},
                'phone': {'value': None, 'provenance': None, 'raw_input': None},
                'address': {'value': None, 'provenance': None, 'raw_input': None},
                'legal_problem': {'value': None, 'provenance': None, 'raw_input': None},
                'incident_description': {'value': None, 'provenance': None, 'raw_input': None},
                'safe_contact_number': {'value': None, 'provenance': None, 'raw_input': None},
                'safe_contact_time': {'value': None, 'provenance': None, 'raw_input': None},
                'nid_number': {'value': None, 'provenance': None, 'raw_input': None},
            },
            'history': [
                {
                    'role': 'assistant',
                    'text_en': cls.SLOT_METADATA['name']['prompt_en'],
                    'text_bn': cls.SLOT_METADATA['name']['prompt_bn'],
                    'timestamp': timezone.now().strftime('%H:%M'),
                }
            ],
            'corrections_count': 0,
            'application_id': None,
        }

    @classmethod
    def get_next_missing_slot(cls, slots):
        """
        Returns the first required slot that does not have a value.
        """
        for slot in cls.REQUIRED_SLOTS:
            if not slots.get(slot, {}).get('value'):
                return slot
        return None

    @classmethod
    def extract_information(cls, text, current_slot, existing_slots):
        """
        Deterministic simulated extraction for common Bangla patterns.
        Detects:
        - Explicit corrections ("না, আমার পুরো নাম ...", "মোবাইল হবে ...")
        - Multi-turn slot recognition
        - Multi-slot mentions in a single sentence
        """
        norm_text = cls.normalize_digits(text).strip()
        extracted = {}
        correction_detected = False
        correction_slot = None
        is_uncertain = False

        # ---------------------------------------------------------------------
        # 1. CORRECTION DETECTION
        # ---------------------------------------------------------------------
        has_explicit_negation = bool(re.search(r'(?:^|[,\s])না(?:[,\s]|$)', text))
        has_correction_word = ('ভুল' in text or 'সংশোধন' in text or 'হবে' in text)

        if has_explicit_negation or has_correction_word:
            # Name correction: "না, আমার পুরো নাম মোঃ রহিম উদ্দিন" or "নাম হবে মোঃ রহিম"
            name_corr = re.search(r'(?:(?:না[,\s]+|ভুল[,\s]+|সংশোধন[,\s:]+)?(?:আমার\s+)?(?:পুরো\s+)?নাম\s*(?:হলো|হচ্ছে|হবে|:|ঃ|=)?\s*([^\n।,]+))', text)
            if name_corr and ('নাম' in text):
                val = name_corr.group(1).strip()
                if val and val != text.strip():
                    extracted['name'] = val
                    correction_detected = True
                    correction_slot = 'name'

            # Phone correction: "না, আমার মোবাইল নম্বর 01712345678" or "মোবাইল হবে 017..."
            phone_corr = re.search(r'(?:(?:না[,\s]+|ভুল[,\s]+|সংশোধন[,\s:]+)?(?:আমার\s+)?মোবাইল(?:\s*নম্বর)?\s*(?:হলো|হবে|:|ঃ|=)?\s*(01[3-9]\d{8}))', norm_text)
            if phone_corr and ('মোবাইল' in norm_text or 'ফোন' in norm_text):
                extracted['phone'] = phone_corr.group(1).strip()
                correction_detected = True
                correction_slot = 'phone'

            # Address correction
            address_corr = re.search(r'(?:(?:না[,\s]+|ভুল[,\s]+|সংশোধন[,\s:]+)?(?:আমার\s+)?ঠিকানা\s*(?:হলো|হবে|:|ঃ|=)?\s*([^\n।]+))', text)
            if address_corr and ('ঠিকানা' in text):
                extracted['address'] = address_corr.group(1).strip()
                correction_detected = True
                correction_slot = 'address'

            # Legal problem correction
            prob_corr = re.search(r'(?:(?:না[,\s]+|ভুল[,\s]+|সংশোধন[,\s:]+)?(?:আমার\s+)?সমস্যা\s*(?:হলো|হবে|:|ঃ|=)?\s*([^\n।]+))', text)
            if prob_corr and ('সমস্যা' in text):
                extracted['legal_problem'] = cls.classify_legal_problem(prob_corr.group(1).strip())
                correction_detected = True
                correction_slot = 'legal_problem'

        # If it was an explicit correction, return immediately
        if correction_detected:
            return {
                'extracted': extracted,
                'is_correction': True,
                'correction_slot': correction_slot,
                'is_uncertain': False,
            }

        # ---------------------------------------------------------------------
        # 2. PATTERN EXTRACTION ACROSS SLOTS
        # ---------------------------------------------------------------------
        # Phone pattern
        phone_match = re.search(r'\b(01[3-9]\d{8})\b', norm_text)
        if phone_match:
            extracted['phone'] = phone_match.group(1)

        # Name patterns
        # "আমার নাম মোঃ রহিম" / "আমার নাম রহিম"
        name_match = re.search(r'আমার\s+নাম(?: হলো| হচ্ছে)?\s+([^\n।,]+)', text)
        if name_match:
            extracted['name'] = name_match.group(1).strip()
        elif text.startswith('আমি ') and len(text.split()) <= 4 and not any(w in text for w in ['সমস্যা', 'থাকি', 'থাকেন', 'বিপদে']):
            extracted['name'] = text.replace('আমি ', '').strip()

        # Address patterns
        # "আমার ঠিকানা গ্রাম: শান্তিনগর, মিরপুর" / "আমি সাভার, ঢাকা থাকি"
        addr_match = re.search(r'আমার\s+ঠিকানা(?: হলো)?\s+([^\n।]+)', text)
        if addr_match:
            extracted['address'] = addr_match.group(1).strip()
        else:
            live_match = re.search(r'আমি\s+([^\n।]+?)(?:-\s*এ| এ| তে| থাকি| বসবাস করি)', text)
            if live_match and not any(w in live_match.group(1) for w in ['সমস্যা', 'বিপদে', 'জমি']):
                extracted['address'] = live_match.group(1).strip()

        # Legal problem patterns
        # "আমার জমি নিয়ে সমস্যা" / "আমি জমিজমা সমস্যায় পড়েছি"
        prob_match = re.search(r'আমার\s+([^\n।]+?)\s*নিয়ে সমস্যা', text)
        if prob_match:
            raw_prob = prob_match.group(1).strip()
            extracted['legal_problem'] = cls.classify_legal_problem(raw_prob)
        else:
            prob_match2 = re.search(r'আমি\s+([^\n।]+?)\s*সমস্যায় পড়েছি', text)
            if prob_match2:
                raw_prob = prob_match2.group(1).strip()
                extracted['legal_problem'] = cls.classify_legal_problem(raw_prob)
            elif any(w in text for w in ['জমি', 'উচ্ছেদ', 'দেনমোহর', 'যৌতুক', 'মজুরি', 'বেতন', 'মারধর']):
                # Keyword detected
                extracted['legal_problem'] = cls.classify_legal_problem(text)

        # Incident description pattern
        inc_match = re.search(r'ঘটনাটি হলো\s+([^\n।]+)', text)
        if inc_match:
            extracted['incident_description'] = inc_match.group(1).strip()

        # Safe contact patterns
        safe_phone = re.search(r'নিরাপদ\s*(?:নম্বর)?\s*(01[3-9]\d{8})', norm_text)
        if safe_phone:
            extracted['safe_contact_number'] = safe_phone.group(1)

        safe_time = re.search(r'(?:নিরাপদ\s*)?সময়\s*(সকাল\s*\d+|বিকাল\s*\d+|দুপুর\s*\d+|রাত\s*\d+|[0-9:\sAPMapm-]+)', text)
        if safe_time:
            extracted['safe_contact_time'] = safe_time.group(1).strip()

        # ---------------------------------------------------------------------
        # 3. CONTEXTUAL FALLBACK FOR CURRENT EXPECTED SLOT
        # ---------------------------------------------------------------------
        # If the user answered the directly asked slot without prefix keywords:
        if not extracted and current_slot:
            clean_val = text.strip()
            # Check negative answer for optional slots
            if current_slot in ['safe_contact_number', 'safe_contact_time'] and clean_val.lower() in ['না', 'নেই', 'প্রয়োজন নেই', 'no', 'none', 'n/a']:
                extracted[current_slot] = "None"
            elif current_slot == 'name':
                # Strip salutations if present
                clean_name = re.sub(r'^(?:আমার নাম(?: হলো| হচ্ছে)?|নাম|আমি)\s*[:=]?\s*', '', clean_val).strip()
                if len(clean_name) >= 2 and not any(ch.isdigit() for ch in clean_name):
                    extracted['name'] = clean_name
                else:
                    is_uncertain = True
            elif current_slot == 'phone':
                phone_raw = cls.normalize_digits(clean_val).replace('-', '').replace(' ', '')
                if re.match(r'^(?:\+?88)?01[3-9]\d{8}$', phone_raw):
                    extracted['phone'] = phone_raw[-11:]
                else:
                    is_uncertain = True
            elif current_slot == 'address':
                if len(clean_val) >= 4 and not clean_val.isdigit():
                    extracted['address'] = clean_val
                else:
                    is_uncertain = True
            elif current_slot == 'legal_problem':
                if len(clean_val) >= 3:
                    extracted['legal_problem'] = cls.classify_legal_problem(clean_val)
                else:
                    is_uncertain = True
            elif current_slot == 'incident_description':
                if len(clean_val) >= 6:
                    extracted['incident_description'] = clean_val
                else:
                    is_uncertain = True
            elif current_slot in ['safe_contact_number', 'safe_contact_time']:
                extracted[current_slot] = clean_val
            else:
                is_uncertain = True

        return {
            'extracted': extracted,
            'is_correction': False,
            'correction_slot': None,
            'is_uncertain': is_uncertain if not extracted else False,
        }

    @classmethod
    def classify_legal_problem(cls, text):
        """
        Classifies legal problem text into a clean category while preserving original context.
        """
        t = text.lower()
        if any(w in t for w in ['জমি', 'জায়গা', 'সীমানা', 'খতিয়ান', 'দলিল', 'বেদখল', 'land', 'property']):
            return f"জমিজমা ও সম্পত্তি সংক্রান্ত বিরোধ ({text.strip()})"
        elif any(w in t for w in ['উচ্ছেদ', 'বাড়ি ভাড়া', 'ভাড়াটিয়া', 'দোকান', 'evict', 'tenancy', 'rent']):
            return f"উচ্ছেদ ও ভাড়াটিয়া বিরোধ ({text.strip()})"
        elif any(w in t for w in ['যৌতুক', 'দেনমোহর', 'তালাক', 'খোরপোষ', 'পারিবারিক', 'dowry', 'marriage', 'custody']):
            return f"পারিবারিক ও দেনমোহর বিরোধ ({text.strip()})"
        elif any(w in t for w in ['বেতন', 'মজুরি', 'কারখানা', 'শ্রমিক', 'চাকরি', 'wage', 'salary', 'labour']):
            return f"শ্রম ও বকেয়া বেতন বিরোধ ({text.strip()})"
        elif any(w in t for w in ['মারধর', 'হুমকি', 'সহিংসতা', 'নির্যাতন', 'assault', 'violence']):
            return f"পারিবারিক সহিংসতা ও শারীরিক নিরাপত্তা ({text.strip()})"
        return text.strip()

    @classmethod
    def generate_review_content(cls, slots):
        """
        Generates the bilingual review card and question.
        """
        name = slots.get('name', {}).get('value') or "—"
        phone = slots.get('phone', {}).get('value') or "—"
        address = slots.get('address', {}).get('value') or "—"
        problem = slots.get('legal_problem', {}).get('value') or "—"
        incident = slots.get('incident_description', {}).get('value') or "—"
        safe_contact = slots.get('safe_contact_number', {}).get('value') or "প্রযোজ্য নয় / None"
        safe_time = slots.get('safe_contact_time', {}).get('value') or "প্রযোজ্য নয় / Standard"

        text_bn = (
            "আপনার দেওয়া সকল তথ্য সংগ্রহ করা হয়েছে। অনুগ্রহ করে যাচাই করুন:\n\n"
            f"• নাম: {name}\n"
            f"• মোবাইল নম্বর: {phone}\n"
            f"• ঠিকানা: {address}\n"
            f"• আইনি সমস্যা: {problem}\n"
            f"• ঘটনার বিবরণ: {incident}\n"
            f"• নিরাপদ যোগাযোগ: {safe_contact} ({safe_time})\n\n"
            "এই তথ্য দিয়ে আবেদন জমা দিতে চান? 'নিশ্চিত করুন' অথবা 'সম্পাদনা করুন' নির্বাচন করুন।"
        )
        text_en = (
            "All intake details have been collected. Please review:\n\n"
            f"• Name: {name}\n"
            f"• Phone: {phone}\n"
            f"• Address: {address}\n"
            f"• Legal Problem: {problem}\n"
            f"• Incident: {incident}\n"
            f"• Safe Contact: {safe_contact} ({safe_time})\n\n"
            "Do you want to submit this application with these details? Select 'Confirm' or 'Edit'."
        )
        return text_en, text_bn

    @classmethod
    def process_turn(cls, state, user_text):
        """
        Processes a single turn from the applicant:
        1. Logs user turn in history.
        2. Performs deterministic extraction & correction detection.
        3. Updates slots with proper provenance (applicant_confirmed vs ai_inferred).
        4. Evaluates if all required slots are present.
        5. Formulates next prompt or review card.
        """
        user_text = (user_text or "").strip()
        if not user_text:
            return state, "অনুগ্রহ করে আপনার তথ্য লিখুন।", "Please provide your details."

        current_slot = state.get('current_slot')
        state_status = state.get('status', 'in_progress')

        # Record user turn in history
        state['history'].append({
            'role': 'user',
            'text_en': user_text,
            'text_bn': user_text,
            'timestamp': timezone.now().strftime('%H:%M'),
        })

        # Check for user cancellation
        if user_text.lower() in ['বাতিল', 'বাতিল করুন', 'cancel', 'stop']:
            state['status'] = 'cancelled'
            reply_bn = "আপনার আবেদন প্রক্রিয়া বাতিল করা হয়েছে। পুনরায় শুরু করতে 'রিসেট' করুন।"
            reply_en = "Your application process has been cancelled. Click 'Reset' to start over."
            state['history'].append({
                'role': 'assistant',
                'text_en': reply_en,
                'text_bn': reply_bn,
                'timestamp': timezone.now().strftime('%H:%M'),
            })
            return state, reply_bn, reply_en

        # If in review state and user says edit/correct
        if state_status == 'review':
            if any(w in user_text.lower() for w in ['সম্পাদনা', 'সংশোধন', 'edit', 'change', 'ভুল']):
                state['status'] = 'in_progress'
                state['current_slot'] = 'name'
                reply_bn = "কোন তথ্যটি সংশোধন করতে চান বলুন (যেমন: 'আমার নাম হবে ...', বা 'মোবাইল হবে ...')।"
                reply_en = "Which detail would you like to edit? (e.g. 'My name is ...' or 'Phone should be ...')"
                state['history'].append({
                    'role': 'assistant',
                    'text_en': reply_en,
                    'text_bn': reply_bn,
                    'timestamp': timezone.now().strftime('%H:%M'),
                })
                return state, reply_bn, reply_en

        # Extraction
        res = cls.extract_information(user_text, current_slot, state['slots'])
        extracted = res['extracted']
        is_corr = res['is_correction']
        corr_slot = res['correction_slot']
        is_uncertain = res['is_uncertain']

        if is_uncertain and not extracted:
            # DO NOT GUESS. Ask clarification question
            meta = cls.SLOT_METADATA.get(current_slot, {})
            slot_bn = meta.get('label_bn', 'তথ্য')
            slot_en = meta.get('label_en', 'information')
            reply_bn = f"দুঃখিত, আমি আপনার তথ্যটি স্পষ্টভাবে বুঝতে পারিনি। অনুগ্রহ করে আপনার {slot_bn} স্পষ্টভাবে উল্লেখ করুন।"
            reply_en = f"Sorry, I could not understand clearly. Please state your {slot_en} again."
            state['history'].append({
                'role': 'assistant',
                'text_en': reply_en,
                'text_bn': reply_bn,
                'timestamp': timezone.now().strftime('%H:%M'),
            })
            return state, reply_bn, reply_en

        # Apply extracted slots
        for slot_k, slot_v in extracted.items():
            if slot_k in state['slots']:
                state['slots'][slot_k] = {
                    'value': slot_v,
                    'provenance': CaseEvent.PROVENANCE_APPLICANT_CONFIRMED,
                    'raw_input': user_text,
                }

        if is_corr:
            state['corrections_count'] = state.get('corrections_count', 0) + 1
            meta = cls.SLOT_METADATA.get(corr_slot, {})
            slot_bn = meta.get('label_bn', 'তথ্য')
            slot_en = meta.get('label_en', 'information')
            val = extracted.get(corr_slot, '')
            ack_bn = f"আপনার {slot_bn} সংশোধন করে '{val}' হিসেবে সংরক্ষণ করা হয়েছে।"
            ack_en = f"Your {slot_en} has been updated to '{val}'."
        else:
            ack_bn = ""
            ack_en = ""

        # Determine next missing slot
        next_slot = cls.get_next_missing_slot(state['slots'])
        if next_slot:
            state['status'] = 'in_progress'
            state['current_slot'] = next_slot
            prompt_meta = cls.SLOT_METADATA[next_slot]
            if ack_bn:
                reply_bn = f"{ack_bn}\n\n{prompt_meta['prompt_bn']}"
                reply_en = f"{ack_en}\n\n{prompt_meta['prompt_en']}"
            else:
                reply_bn = prompt_meta['prompt_bn']
                reply_en = prompt_meta['prompt_en']
        else:
            # All required slots filled -> transition to review
            state['status'] = 'review'
            state['current_slot'] = None
            rev_en, rev_bn = cls.generate_review_content(state['slots'])
            if ack_bn:
                reply_bn = f"{ack_bn}\n\n{rev_bn}"
                reply_en = f"{ack_en}\n\n{rev_en}"
            else:
                reply_bn = rev_bn
                reply_en = rev_en

        state['history'].append({
            'role': 'assistant',
            'text_en': reply_en,
            'text_bn': reply_bn,
            'timestamp': timezone.now().strftime('%H:%M'),
        })
        return state, reply_bn, reply_en

    @classmethod
    def confirm_and_submit(cls, state, user=None, channel='web'):
        """
        Explicit applicant confirmation step.
        Submits application through existing submit_application service.
        Generates Application ID, does NOT generate Case ID.
        Preserves provenance as 'applicant_confirmed'.
        """
        slots = state.get('slots', {})
        missing = cls.get_next_missing_slot(slots)
        if missing:
            raise ValueError(f"Cannot submit application: missing required slot '{missing}'.")

        name = slots['name']['value']
        phone = slots['phone']['value']
        address = slots['address']['value']
        legal_problem = slots['legal_problem']['value']
        incident_description = slots['incident_description']['value']
        safe_contact_num = slots.get('safe_contact_number', {}).get('value')
        if safe_contact_num in [None, 'None', 'না', 'নেই', 'none', 'n/a']:
            safe_contact_num = ""
        safe_contact_time = slots.get('safe_contact_time', {}).get('value')
        if safe_contact_time in [None, 'None', 'না', 'নেই', 'none', 'n/a']:
            safe_contact_time = ""
        nid_num = slots.get('nid_number', {}).get('value') or ""

        # Call existing submit_application service
        app = submit_application(
            name=name,
            phone=phone,
            address=address,
            legal_problem=legal_problem,
            incident_description=incident_description,
            applicant_user=user if (user and user.is_authenticated) else None,
            preferred_channel=channel or Application.CHANNEL_WEB,
            safe_contact_number=safe_contact_num or "",
            safe_contact_time=safe_contact_time or "",
            language='bn',
            nid_number=nid_num,
            nid_verification_status=Application.NID_STATUS_NOT_VERIFIED,
            actor=user if (user and user.is_authenticated) else None,
            provenance=CaseEvent.PROVENANCE_APPLICANT_CONFIRMED,
        )

        # Log specific conversational intake confirmed event
        actor_role = getattr(user, 'profile', None).role if (user and hasattr(user, 'profile')) else 'citizen'
        CaseEvent.objects.create(
            application=app,
            case=None,
            actor=user if (user and user.is_authenticated) else None,
            actor_role=actor_role,
            channel=channel or 'web',
            action='conversational_intake_confirmed',
            description=(
                f"Applicant reviewed and confirmed intake details via Bangla Conversational AI. "
                f"Generated Application ID: {app.application_id}."
            ),
            provenance=CaseEvent.PROVENANCE_APPLICANT_CONFIRMED,
            authority='applicant_personal_confirmation',
        )

        state['status'] = 'confirmed'
        state['application_id'] = app.application_id
        return app
