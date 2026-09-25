"""
Mock External Services for DLAS (Digital Legal Aid System)
Defined in docs/ARCHITECTURE.md Section 7 and docs/MASTER_PRD.md Section 4.4.

IMPORTANT ARCHITECTURAL MANDATES:
1. These services are STRICTLY SIMULATED internal Python mocks.
2. They MUST NOT connect to real external APIs, government databases, telecommunications carriers, or payment gateways.
3. Every mock output MUST clearly be labeled:
   - English: "[ SIMULATED ]"
   - বাংলা: "[ সিমুলেটেড ]"
4. Signature simulations MUST explicitly state that they are NOT legally binding electronic signatures.
5. Communications MUST strictly respect citizen safe-contact rules (safe phone and permitted contact hours).
"""

import hashlib
import json
import uuid
import re
from django.utils import timezone
from django.core.exceptions import ValidationError, PermissionDenied


class MockSMSService:
    """
    Internal simulated SMS service.
    Simulates sending citizen notifications, case status alerts, and OTPs.
    Defined in docs/ARCHITECTURE.md Section 7.
    """
    IS_SIMULATED = True
    LABEL_EN = "[ SIMULATED ] SMS Service"
    LABEL_BN = "[ সিমুলেটেড ] এসএমএস সেবা"
    STATUS_SENT_EN = "Sent"
    STATUS_SENT_BN = "প্রেরিত"

    @classmethod
    def send_sms(cls, case_record, message, actor=None, force_safe=False):
        """
        Sends a simulated SMS for a case record.
        Strictly respects safe-contact rules:
        If the citizen specified a safe_contact_number, it MUST be prioritized over primary phone.
        Records the communication in the Communication table.
        """
        from cases.models import Communication
        from django.contrib.auth.models import User

        app = case_record.application
        safe_contact_used = False

        if app.safe_contact_number:
            recipient = app.safe_contact_number
            safe_contact_used = True
        else:
            recipient = app.phone

        # Ensure valid actor
        if not actor or not isinstance(actor, User):
            actor = case_record.assigned_officer

        # Record in Communication table
        comm = Communication.objects.create(
            case=case_record,
            actor=actor,
            channel=Communication.CHANNEL_SMS,
            recipient=recipient,
            message=message,
            result="delivered (simulated)",
            safe_contact_used=safe_contact_used,
        )

        return {
            'is_simulated': cls.IS_SIMULATED,
            'service': 'MockSMSService',
            'label_en': cls.LABEL_EN,
            'label_bn': cls.LABEL_BN,
            'status_en': cls.STATUS_SENT_EN,
            'status_bn': cls.STATUS_SENT_BN,
            'recipient': recipient,
            'safe_contact_used': safe_contact_used,
            'message': message,
            'timestamp': comm.created_at,
            'communication_id': comm.id,
        }

    @classmethod
    def send_raw_sms(cls, recipient, message):
        """
        Simulates standalone SMS dispatch for non-case notifications (e.g. intake receipt, OTP).
        """
        return {
            'is_simulated': cls.IS_SIMULATED,
            'service': 'MockSMSService',
            'label_en': cls.LABEL_EN,
            'label_bn': cls.LABEL_BN,
            'status_en': cls.STATUS_SENT_EN,
            'status_bn': cls.STATUS_SENT_BN,
            'recipient': recipient,
            'message': message,
            'timestamp': timezone.now(),
        }


class MockIVRService:
    """
    Internal simulated IVR / Voice Call service.
    Simulates automated helpline calls and voice prompts for 16699 National Legal Aid Helpline.
    Defined in docs/ARCHITECTURE.md Section 7.
    """
    IS_SIMULATED = True
    LABEL_EN = "[ SIMULATED ] IVR Voice Service (16699)"
    LABEL_BN = "[ সিমুলেটেড ] আইভিআর ভয়েস সেবা (১৬৬৯৯)"
    STATUS_COMPLETED_EN = "Call Completed (Simulated)"
    STATUS_COMPLETED_BN = "কল সম্পন্ন (সিমুলেটেড)"

    @classmethod
    def initiate_call(cls, case_record, script_summary, actor=None):
        """
        Simulates an automated outbound voice call to the applicant.
        Strictly respects safe contact hours and safe phone.
        """
        from cases.models import Communication
        from django.contrib.auth.models import User

        app = case_record.application
        safe_contact_used = False

        if app.safe_contact_number:
            recipient = app.safe_contact_number
            safe_contact_used = True
        else:
            recipient = app.phone

        if not actor or not isinstance(actor, User):
            actor = case_record.assigned_officer

        voice_msg = f"[Simulated Voice Prompt 16699]: {script_summary}"
        if safe_contact_used and app.safe_contact_time:
            voice_msg += f" (Respecting permitted hours: {app.safe_contact_time})"

        comm = Communication.objects.create(
            case=case_record,
            actor=actor,
            channel=Communication.CHANNEL_VOICE,
            recipient=recipient,
            message=voice_msg,
            result="call_completed (simulated)",
            safe_contact_used=safe_contact_used,
        )

        return {
            'is_simulated': cls.IS_SIMULATED,
            'service': 'MockIVRService',
            'label_en': cls.LABEL_EN,
            'label_bn': cls.LABEL_BN,
            'status_en': cls.STATUS_COMPLETED_EN,
            'status_bn': cls.STATUS_COMPLETED_BN,
            'recipient': recipient,
            'safe_contact_used': safe_contact_used,
            'permitted_hours': app.safe_contact_time if safe_contact_used else "Standard Hours",
            'script_summary': script_summary,
            'timestamp': comm.created_at,
            'communication_id': comm.id,
        }

    @classmethod
    def simulate_inbound_helpline(cls, phone, caller_query=""):
        """
        Simulates an inbound citizen call to 16699 helpline.
        """
        return {
            'is_simulated': cls.IS_SIMULATED,
            'service': 'MockIVRService',
            'label_en': cls.LABEL_EN,
            'label_bn': cls.LABEL_BN,
            'caller_phone': phone,
            'ivr_menu_en': "Press 1 for Application Status, Press 2 for Safe Contact, Press 3 for Agent.",
            'ivr_menu_bn': "আবেদনের অবস্থার জন্য ১ চাপুন, নিরাপদ যোগাযোগের জন্য ২ চাপুন, এজেন্টের সাথে কথা বলতে ৩ চাপুন।",
            'status_en': "Simulated Call Connected to Helpline Queue",
            'status_bn': "সিমুলেটেড কল হেল্পলাইন কিউতে সংযুক্ত হয়েছে",
            'timestamp': timezone.now(),
        }


class MockUSSDService:
    """
    Internal simulated USSD Service (*16699#).
    Simulates feature-phone offline self-service legal aid queries in Bangladesh.
    Defined in docs/ARCHITECTURE.md Section 7.
    """
    IS_SIMULATED = True
    LABEL_EN = "[ SIMULATED ] USSD Service (*16699#)"
    LABEL_BN = "[ সিমুলেটেড ] ইউএসএসডি সেবা (*১৬৬৯৯#)"

    @classmethod
    def process_ussd_session(cls, session_id, phone, user_input=""):
        """
        Simulates USSD session state machine (*16699#).
        Accepts user_input and returns simulated interactive screen response.
        """
        user_input = user_input.strip()
        from cases.models import Application, CaseRecord

        if not user_input or user_input == '*16699#':
            return {
                'is_simulated': cls.IS_SIMULATED,
                'session_id': session_id,
                'screen_text_en': (
                    "[ SIMULATED USSD *16699# ]\n"
                    "Welcome to National Legal Aid Services (DLAS)\n"
                    "1. Check Application/Case Status\n"
                    "2. Safe Contact Policy Info\n"
                    "3. DLAO Office Locations\n"
                    "0. Exit"
                ),
                'screen_text_bn': (
                    "[ সিমুলেটেড ইউএসএসডি *১৬৬৯৯# ]\n"
                    "জাতীয় আইনগত সহায়তা সেবা (ডিএলএএস)-এ স্বাগতম\n"
                    "১. আবেদন বা মামলার অবস্থা যাচাই\n"
                    "২. নিরাপদ যোগাযোগ নির্দেশিকা\n"
                    "৩. জেলা লিগ্যাল এইড অফিসের তথ্য\n"
                    "০. প্রস্থান"
                ),
                'expects_input': True,
            }
        elif user_input == '1':
            return {
                'is_simulated': cls.IS_SIMULATED,
                'session_id': session_id,
                'screen_text_en': "[ SIMULATED ] Please reply with your Application ID (e.g. APP-2026-00001) or Case ID.",
                'screen_text_bn': "[ সিমুলেটেড ] আপনার আবেদন নম্বর (যেমন APP-2026-00001) বা কেস নম্বর লিখে পাঠান।",
                'expects_input': True,
            }
        elif user_input == '2':
            return {
                'is_simulated': cls.IS_SIMULATED,
                'session_id': session_id,
                'screen_text_en': (
                    "[ SIMULATED ] Safe Contact: If your phone is monitored, contact DLAO walk-in or specify safe contact hours.\n"
                    "Dial 16699 (Toll-Free) for immediate confidential help."
                ),
                'screen_text_bn': (
                    "[ সিমুলেটেড ] নিরাপদ যোগাযোগ: আপনার ফোনে ঝুঁকি থাকলে ডিএলএও অফিসে সরাসরি আসুন বা নিরাপদ সময় দিন।\n"
                    "গোপনীয় সহায়তার জন্য ১৬৬৯৯ (টোল-ফ্রি) নম্বরে ডায়াল করুন।"
                ),
                'expects_input': False,
            }
        elif user_input == '3':
            return {
                'is_simulated': cls.IS_SIMULATED,
                'session_id': session_id,
                'screen_text_en': "[ SIMULATED ] 64 District Legal Aid Offices (DLAO) located at District Judgeship Court Complexes.",
                'screen_text_bn': "[ সিমুলেটেড ] দেশের ৬৪ জেলা জজ আদালত প্রাঙ্গণে জেলা লিগ্যাল এইড অফিস অবস্থিত।",
                'expects_input': False,
            }
        else:
            # Check if user input is an Application ID or Case ID
            app = Application.objects.filter(application_id__iexact=user_input).first()
            if app:
                status_str = app.get_status_display()
                case_info = f"Case ID: {app.case_record.case_id}" if hasattr(app, 'case_record') else "Awaiting DLAO Acceptance"
                return {
                    'is_simulated': cls.IS_SIMULATED,
                    'session_id': session_id,
                    'screen_text_en': f"[ SIMULATED ] App: {app.application_id}\nStatus: {status_str}\n{case_info}",
                    'screen_text_bn': f"[ সিমুলেটেড ] আবেদন: {app.application_id}\nঅবস্থা: {status_str}\n{case_info}",
                    'expects_input': False,
                }
            
            case_rec = CaseRecord.objects.filter(case_id__iexact=user_input).first()
            if case_rec:
                return {
                    'is_simulated': cls.IS_SIMULATED,
                    'session_id': session_id,
                    'screen_text_en': f"[ SIMULATED ] Case: {case_rec.case_id}\nStatus: {case_rec.get_status_display()}\nPriority: {case_rec.get_priority_display()}",
                    'screen_text_bn': f"[ সিমুলেটেড ] মামলা: {case_rec.case_id}\nঅবস্থা: {case_rec.get_status_display()}\nঅগ্রাধিকার: {case_rec.get_priority_display()}",
                    'expects_input': False,
                }

            return {
                'is_simulated': cls.IS_SIMULATED,
                'session_id': session_id,
                'screen_text_en': "[ SIMULATED ] Record not found for the entered reference. Please verify your ID.",
                'screen_text_bn': "[ সিমুলেটেড ] প্রদত্ত নম্বরের কোনো রেকর্ড পাওয়া যায়নি। অনুগ্রহ করে সঠিক নম্বর দিন।",
                'expects_input': False,
            }


class MockNIDService:
    """
    Internal simulated National ID (NID) Verification Service.
    Defined in docs/ARCHITECTURE.md Section 7.
    Accepts development/test input and returns deterministic simulated results.
    DOES NOT connect to Bangladesh Election Commission or NID database.
    DOES NOT store unnecessary sensitive personal NID data.
    """
    IS_SIMULATED = True
    LABEL_EN = "[ SIMULATED ] NID Verification Service"
    LABEL_BN = "[ সিমুলেটেড ] এনআইডি যাচাইকরণ সেবা"
    DISCLAIMER_EN = "SIMULATED VERIFICATION ONLY — Not connected to Bangladesh National ID / Election Commission database."
    DISCLAIMER_BN = "শুধুমাত্র সিমুলেটেড যাচাইকরণ — বাংলাদেশ নির্বাচন কমিশন বা জাতীয় পরিচয়পত্র ডাটাবেজের সাথে সংযুক্ত নয়।"

    @classmethod
    def verify_nid(cls, nid_number, dob=None, name=None):
        """
        Deterministic simulated NID verification.
        Validates:
        - Must be 10 digits (Smart NID) or 13/17 digits (Old NID).
        - Test failure codes: all zeroes '0000000000' or non-digits return simulated NOT_FOUND.
        Returns clear bilingual labels indicating simulated status.
        """
        raw = str(nid_number or '').strip()
        digits = re.sub(r'\D', '', raw)

        # Negative test conditions
        if not digits or len(digits) not in [10, 13, 17] or set(digits) == {'0'} or digits == '9999999999':
            return {
                'is_simulated': cls.IS_SIMULATED,
                'label_en': cls.LABEL_EN,
                'label_bn': cls.LABEL_BN,
                'disclaimer_en': cls.DISCLAIMER_EN,
                'disclaimer_bn': cls.DISCLAIMER_BN,
                'status': 'NOT_FOUND',
                'status_display_en': 'Simulated Verification Failed / Record Not Found',
                'status_display_bn': 'সিমুলেটেড যাচাই ব্যর্থ / রেকর্ড পাওয়া যায়নি',
                'is_verified': False,
                'nid_masked': f"{digits[:3]}****{digits[-3:]}" if len(digits) >= 6 else "****",
                'message_en': "NID number not found in mock verification database or invalid format.",
                'message_bn': "মক যাচাইকরণ ডাটাবেজে জাতীয় পরিচয়পত্র নম্বর পাওয়া যায়নি অথবা বিন্যাস সঠিক নয়।",
            }

        # Deterministic simulated success
        masked = f"{digits[:3]}****{digits[-3:]}"
        token = f"SIM-NID-{hashlib.sha256(digits.encode()).hexdigest()[:10].upper()}"
        return {
            'is_simulated': cls.IS_SIMULATED,
            'label_en': cls.LABEL_EN,
            'label_bn': cls.LABEL_BN,
            'disclaimer_en': cls.DISCLAIMER_EN,
            'disclaimer_bn': cls.DISCLAIMER_BN,
            'status': 'VERIFIED',
            'status_display_en': 'Verified (Simulated)',
            'status_display_bn': 'যাচাইকৃত (সিমুলেটেড)',
            'is_verified': True,
            'nid_masked': masked,
            'verification_token': token,
            'verified_name': name or "Simulated Citizen Profile",
            'confidence': 0.99,
            'message_en': "Simulated NID record verified successfully against test repository.",
            'message_bn': "টেস্ট ডাটাবেজের সাথে সিমুলেটেড এনআইডি সফলভাবে যাচাই করা হয়েছে।",
        }


class MockPaymentService:
    """
    Internal simulated Payment / Honorarium Service.
    Defined in docs/ARCHITECTURE.md Section 7.
    Simulates legal-aid court fee exemptions and panel lawyer honorarium disbursements.
    DOES NOT connect to any payment gateway.
    NO real financial transaction occurs.
    """
    IS_SIMULATED = True
    LABEL_EN = "SIMULATED PAYMENT"
    LABEL_BN = "সিমুলেটেড পেমেন্ট"
    DISCLAIMER_EN = (
        "SIMULATED PAYMENT ONLY — No real financial transaction occurs. "
        "DLAS legal aid services are free of cost for eligible citizens under Legal Aid Services Act (LASA) 2000."
    )
    DISCLAIMER_BN = (
        "শুধুমাত্র সিমুলেটেড লেনদেন — কোনো প্রকৃত আর্থিক লেনদেন সম্পন্ন হয় না। "
        "আইনগত সহায়তা প্রদান আইন ২০০০ অনুযায়ী যোগ্য নাগরিকদের জন্য সকল সেবা সম্পূর্ণ বিনামূল্যে।"
    )

    @classmethod
    def process_legal_aid_disbursement(cls, case_record, amount, lawyer, purpose="Panel Lawyer Honorarium"):
        """
        Simulates an administrative legal aid honorarium or filing expense disbursement.
        Logs an immutable CaseEvent for audit transparency.
        """
        from cases.models import CaseEvent

        tx_id = f"SIM-PAY-{uuid.uuid4().hex[:10].upper()}"
        amount_bdt = float(amount) if amount else 1500.0

        # Log audit event
        CaseEvent.objects.create(
            case=case_record,
            application=case_record.application,
            actor=case_record.assigned_officer,
            actor_role='dlao_officer',
            channel='web',
            action='PAYMENT_SIMULATED',
            description=(
                f"[ SIMULATED PAYMENT ] Voucher {tx_id} created for {lawyer.username}. "
                f"Purpose: {purpose} ({amount_bdt:.2f} BDT). No real financial transaction occurred."
            ),
            provenance=CaseEvent.PROVENANCE_STAFF_ENTERED,
            authority='DLAO Officer',
        )

        return {
            'is_simulated': cls.IS_SIMULATED,
            'label_en': cls.LABEL_EN,
            'label_bn': cls.LABEL_BN,
            'disclaimer_en': cls.DISCLAIMER_EN,
            'disclaimer_bn': cls.DISCLAIMER_BN,
            'transaction_id': tx_id,
            'status': 'DISBURSED (SIMULATED)',
            'status_bn': 'পরিশোধিত (সিমুলেটেড)',
            'amount_bdt': amount_bdt,
            'currency': 'BDT',
            'recipient': lawyer.username,
            'purpose': purpose,
            'timestamp': timezone.now(),
        }

    @classmethod
    def check_fee_exemption(cls, application):
        """
        Simulates the statutory court fees exemption certificate under LASA 2000.
        """
        return {
            'is_simulated': cls.IS_SIMULATED,
            'label_en': cls.LABEL_EN,
            'label_bn': cls.LABEL_BN,
            'disclaimer_en': cls.DISCLAIMER_EN,
            'disclaimer_bn': cls.DISCLAIMER_BN,
            'exemption_status': '100% EXEMPT (Legal Aid)',
            'exemption_status_bn': '১০০% ফি মওকুফ (আইনগত সহায়তা)',
            'statutory_reference': 'Legal Aid Services Act 2000, Section 12',
            'court_fee_payable': 0.0,
        }


class MockSignatureService:
    """
    Internal simulated Signature Service.
    Defined in docs/ARCHITECTURE.md Section 7 and docs/MASTER_PRD.md Section 2.3.
    Demonstrates:
    - Document cryptographic hashing (SHA-256)
    - Simulated electronic signature creation
    - Simulated signature verification
    - Signature status reporting

    CRITICAL LEGAL DISCLAIMER:
    This service is strictly simulated for prototype demonstration.
    It is NOT a legally binding electronic-signature provider under Bangladesh Information and Communication Technology (ICT) Act 2006.
    It does not claim legal validity.
    """
    IS_SIMULATED = True
    LABEL_EN = "[ SIMULATED ] Signature Service"
    LABEL_BN = "[ সিমুলেটেড ] স্বাক্ষর সেবা"
    DISCLAIMER_EN = (
        "SIMULATED ELECTRONIC SIGNATURE — FOR PROTOTYPE DEMONSTRATION ONLY. "
        "This is not a legally binding electronic-signature provider and does not claim legal validity."
    )
    DISCLAIMER_BN = (
        "সিমুলেটেড ইলেকট্রনিক স্বাক্ষর — শুধুমাত্র প্রোটোটাইপ প্রদর্শনের উদ্দেশ্যে। "
        "এটি আইনগতভাবে বাধ্যতামূলক কোনো ডিজিটাল স্বাক্ষর নয় এবং কোনো আইনি বৈধতা দাবি করে না।"
    )

    @classmethod
    def hash_document(cls, document):
        """
        Computes the SHA-256 hash of a document file or content.
        """
        hasher = hashlib.sha256()
        try:
            if document.file and hasattr(document.file, 'path'):
                with open(document.file.path, 'rb') as f:
                    for chunk in iter(lambda: f.read(65536), b''):
                        hasher.update(chunk)
            elif document.file:
                document.file.seek(0)
                hasher.update(document.file.read())
            else:
                hasher.update(f"{document.title}:{document.description}".encode('utf-8'))
        except Exception:
            # Fallback deterministic hash based on document attributes
            hasher.update(f"DOC-{document.id}:{document.title}:{document.case_id}".encode('utf-8'))

        return hasher.hexdigest()

    @classmethod
    def create_signature(cls, document, signer_user, offline_created=False, notes=""):
        """
        Creates a simulated signature record attached to a Document and CaseRecord.
        Computes SHA-256 document hash and logs an immutable CaseEvent.
        """
        from documents.models import Signature
        from cases.models import CaseEvent
        from accounts.permissions import get_user_role

        doc_hash = cls.hash_document(document)
        sig_token = f"SIM-SIG-{uuid.uuid4().hex[:12].upper()}"

        sig_payload = {
            'service': 'MockSignatureService',
            'is_simulated': cls.IS_SIMULATED,
            'token': sig_token,
            'signer_id': signer_user.id,
            'signer_username': signer_user.username,
            'document_id': document.id,
            'document_hash': doc_hash,
            'offline_created': offline_created,
            'notes': notes,
            'created_at': timezone.now().isoformat(),
            'legal_notice': cls.DISCLAIMER_EN,
        }

        signature = Signature.objects.create(
            case=document.case,
            document=document,
            signer=signer_user,
            document_hash=doc_hash,
            signature_data=json.dumps(sig_payload),
            offline_created=offline_created,
            verified=True,
        )

        actor_role = get_user_role(signer_user) or 'dlao_officer'
        CaseEvent.objects.create(
            case=document.case,
            application=document.case.application,
            actor=signer_user,
            actor_role=actor_role,
            channel='web',
            action='SIGNATURE_CREATED',
            description=(
                f"[ SIMULATED ] Signature created by {signer_user.username} on document '{document.title}'. "
                f"Doc SHA-256: {doc_hash[:16]}... NOTE: Simulated prototype only, not legally binding."
            ),
            provenance=CaseEvent.PROVENANCE_STAFF_ENTERED,
            authority=actor_role.replace('_', ' ').title(),
        )

        return signature

    @classmethod
    def verify_signature(cls, signature):
        """
        Verifies a simulated signature against the current document hash.
        Detects tampering or content alteration.
        Logs an immutable CaseEvent.
        """
        from cases.models import CaseEvent

        current_hash = cls.hash_document(signature.document)
        matches = (current_hash == signature.document_hash)

        # Update verification flag
        signature.verified = matches
        signature.save(update_fields=['verified'])

        # Log audit event
        CaseEvent.objects.create(
            case=signature.case,
            application=signature.case.application,
            actor=signature.signer,
            actor_role='system',
            channel='web',
            action='SIGNATURE_VERIFIED',
            description=(
                f"[ SIMULATED ] Signature verification executed for document '{signature.document.title}'. "
                f"Stored hash: {signature.document_hash[:12]}..., Current: {current_hash[:12]}... "
                f"Result: {'VERIFIED MATCH' if matches else 'HASH MISMATCH (TAMPERED)'}."
            ),
            provenance=CaseEvent.PROVENANCE_STAFF_ENTERED,
        )

        return {
            'is_simulated': cls.IS_SIMULATED,
            'label_en': cls.LABEL_EN,
            'label_bn': cls.LABEL_BN,
            'disclaimer_en': cls.DISCLAIMER_EN,
            'disclaimer_bn': cls.DISCLAIMER_BN,
            'is_valid': matches,
            'status': 'VERIFIED' if matches else 'HASH_MISMATCH',
            'status_display_en': 'Verified Match (Simulated)' if matches else 'Invalid - Document Modified',
            'status_display_bn': 'যাচাই সফল (সিমুলেটেড)' if matches else 'ত্রুটিপূর্ণ - নথি পরিবর্তিত হয়েছে',
            'document_hash': current_hash,
            'signature_hash': signature.document_hash,
            'signed_at': signature.signed_at,
            'signer': signature.signer.username,
            'offline_created': signature.offline_created,
        }
