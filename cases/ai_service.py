"""
Mock AI Service for DLAS (Digital Legal Aid System)
Defined in docs/ARCHITECTURE.md Section 4 and docs/MASTER_PRD.md Section 4.4.

IMPORTANT ARCHITECTURAL MANDATES:
1. This service is strictly SIMULATED and assistive.
2. AI is strictly PROHIBITED from:
   - Determining eligibility
   - Rejecting applications
   - Assigning final priority
   - Assigning the final lawyer
   - Declaring fraud
   - Merging cases
   - Closing cases
   - Determining mediation outcomes
   - Making final legal decisions
3. All AI outputs must be clearly labeled: "SIMULATED AI ASSISTANCE" / "সিমুলেটেড এআই সহায়তা"
4. Provenance for all AI suggestions is 'ai_inferred'. Original human inputs must be preserved.
"""

import re
from django.core.exceptions import PermissionDenied


class MockAIService:
    """
    Internal simulated assistive AI service.
    Returns deterministic, safe suggestions and extractions for human staff review.
    """
    IS_SIMULATED = True
    LABEL_EN = "SIMULATED AI ASSISTANCE"
    LABEL_BN = "সিমুলেটেড এআই সহায়তা"
    DISCLAIMER_EN = "SIMULATED AI ASSISTANCE — FOR HUMAN REVIEW ONLY. AI cannot make legal decisions."
    DISCLAIMER_BN = "সিমুলেটেড এআই সহায়তা — শুধুমাত্র মানব পর্যালোচনার জন্য। এআই কোনো আইনি সিদ্ধান্ত নিতে পারে না।"

    @classmethod
    def assert_ai_cannot_decide(cls, action_name):
        """
        Safety barrier enforcing that AI code can never execute consequential actions.
        """
        forbidden_actions = [
            'reject_application',
            'merge_cases',
            'close_case',
            'determine_eligibility',
            'assign_lawyer',
            'assign_priority',
            'declare_fraud',
            'determine_mediation_outcome',
        ]
        if action_name in forbidden_actions:
            raise PermissionDenied(
                f"AI Safety Violation: AI is strictly prohibited from executing '{action_name}'. "
                "All consequential legal decisions require human authority per PRD Section 4.4."
            )

    @classmethod
    def extract_information(cls, text):
        """
        Assistive entity and information extraction.
        Extracts dates, phone numbers, and structural entities from narrative text.
        """
        if not text:
            return {
                'is_simulated': cls.IS_SIMULATED,
                'label_en': cls.LABEL_EN,
                'label_bn': cls.LABEL_BN,
                'dates': [],
                'phones': [],
                'keywords': [],
                'summary': "No narrative provided.",
                'confidence': 0.0,
            }

        # Simulated deterministic regex extraction
        dates = re.findall(r'\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})\b', text)
        phones = re.findall(r'\b(?:01[3-9]\d{8})\b', text)

        # Keyword tags
        legal_keywords = []
        kw_map = {
            'land': 'Land / Property',
            'eviction': 'Tenancy / Eviction',
            'rent': 'Tenancy Dispute',
            'wages': 'Labour / Unpaid Wages',
            'dowry': 'Dowry / Family Offence',
            'violence': 'Domestic Protection',
            'assault': 'Personal Safety / Tort',
            'boundary': 'Boundary / Survey Dispute',
            'notice': 'Notice Issued',
        }
        text_lower = text.lower()
        for kw, tag in kw_map.items():
            if kw in text_lower:
                legal_keywords.append(tag)

        # Truncated simulated summary
        words = text.split()
        summary = " ".join(words[:25]) + ("..." if len(words) > 25 else "")

        return {
            'is_simulated': cls.IS_SIMULATED,
            'label_en': cls.LABEL_EN,
            'label_bn': cls.LABEL_BN,
            'disclaimer': cls.DISCLAIMER_EN,
            'dates': dates,
            'phones': phones,
            'keywords': legal_keywords,
            'summary': summary,
            'confidence': 0.86,
        }

    @classmethod
    def categorize_case(cls, incident_description):
        """
        Simulated assistive case categorization suggestion.
        Does NOT assign final category; generates advice for officer.
        """
        desc = (incident_description or "").lower()

        if any(w in desc for w in ['land', 'plot', 'boundary', 'deed', 'khatian', 'evict', 'property', 'lease']):
            category = "Civil / Land & Property Rights"
            confidence = 0.91
            reasoning = "Keywords indicate immovable property possession or tenancy dispute."
        elif any(w in desc for w in ['wage', 'salary', 'factory', 'worker', 'terminate', 'overtime', 'mill']):
            category = "Labour Law & Industrial Employment"
            confidence = 0.89
            reasoning = "Employment-related compensation or wrongful termination markers detected."
        elif any(w in desc for w in ['dowry', 'marriage', 'divorce', 'custody', 'child', 'maintenance', 'kabinnama']):
            category = "Family & Guardianship Law"
            confidence = 0.94
            reasoning = "Domestic relations or maintenance claims identified."
        elif any(w in desc for w in ['beat', 'threat', 'assault', 'violence', 'harass', 'injure', 'police']):
            category = "Criminal Protection / Prevention of Oppression"
            confidence = 0.92
            reasoning = "Markers of physical endangerment or penal offences detected."
        else:
            category = "General Civil Legal Aid"
            confidence = 0.70
            reasoning = "Broad civil dispute requiring detailed human interview."

        return {
            'is_simulated': cls.IS_SIMULATED,
            'label_en': cls.LABEL_EN,
            'label_bn': cls.LABEL_BN,
            'suggested_category': category,
            'confidence': confidence,
            'reasoning': reasoning,
            'disclaimer': cls.DISCLAIMER_EN,
        }

    @classmethod
    def detect_missing_information(cls, application):
        """
        Detects missing essential documentation or narrative gaps in an application.
        """
        missing_items = []
        desc = application.incident_description or ""

        # Check date occurrence
        if not re.search(r'\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}|\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\b)', desc, re.I):
            missing_items.append("Exact date or timeframe of incident not specified in narrative")

        # Check opposing party identification
        if not any(w in desc.lower() for w in ['against', 'by the', 'opponent', 'landlord', 'employer', 'husband', 'wife', 'brother', 'in-laws', 'respondent', 'named']):
            missing_items.append("Opposing party / respondent full identity and relationship not clearly specified")

        # Check document proofs
        if not any(w in desc.lower() for w in ['deed', 'contract', 'notice', 'slip', 'paper', 'document', 'receipt', 'letter']):
            missing_items.append("Supporting documentation (e.g. written notice, rent slip, title deed) not referenced")

        # Check safe contact time
        if not application.safe_contact_time:
            missing_items.append("Safe contact hours not provided (recommended for vulnerable citizens)")

        completeness_score = max(20, 100 - (len(missing_items) * 20))

        return {
            'is_simulated': cls.IS_SIMULATED,
            'label_en': cls.LABEL_EN,
            'label_bn': cls.LABEL_BN,
            'completeness_score': completeness_score,
            'missing_items': missing_items,
            'is_complete': len(missing_items) == 0,
            'disclaimer': cls.DISCLAIMER_EN,
        }

    @classmethod
    def summarize_document(cls, title, filename=""):
        """
        Generates simulated assistive summary for an uploaded document.
        """
        lower_name = f"{title} {filename}".lower()
        if any(w in lower_name for w in ['notice', 'letter', 'demand']):
            summary = "Simulated Analysis: Formal notice or demand letter specifying grievance and stipulated response deadline."
            confidence = 0.88
        elif any(w in lower_name for w in ['deed', 'khatian', 'porcha', 'mutation', 'land']):
            summary = "Simulated Analysis: Title/mutation record pertaining to land schedule and recorded ownership."
            confidence = 0.92
        elif any(w in lower_name for w in ['nid', 'identity', 'birth', 'passport']):
            summary = "Simulated Analysis: Official national identification or civic verification record."
            confidence = 0.95
        elif any(w in lower_name for w in ['contract', 'agreement', 'mou', 'lease']):
            summary = "Simulated Analysis: Executed legal agreement specifying covenants, terms, and counterparty obligations."
            confidence = 0.90
        elif any(w in lower_name for w in ['medical', 'injury', 'hospital']):
            summary = "Simulated Analysis: Medical evaluation certificate detailing injuries or clinical findings."
            confidence = 0.87
        else:
            summary = f"Simulated Analysis: Case supporting documentation for '{title}'. Recommended for staff review."
            confidence = 0.75

        return {
            'is_simulated': cls.IS_SIMULATED,
            'label_en': cls.LABEL_EN,
            'label_bn': cls.LABEL_BN,
            'ai_summary': summary,
            'confidence': confidence,
            'disclaimer': cls.DISCLAIMER_EN,
        }

    @classmethod
    def generate_draft(cls, case_record, draft_type='case_summary'):
        """
        Assists lawyers or officers by preparing a template draft brief.
        Human must review, edit, and formalize.
        """
        app = case_record.application
        if draft_type == 'referral_letter':
            content = (
                f"[SIMULATED DRAFT REFERRAL LETTER - SUBJECT TO HUMAN OFFICER REVISION]\n\n"
                f"To: Designated Authority\n"
                f"Subject: Inter-Agency Legal Aid Referral for Case {case_record.case_id}\n\n"
                f"Applicant: {app.name}\n"
                f"Phone: {app.phone}\n"
                f"Legal Matter: {app.legal_problem}\n\n"
                f"Background Summary:\n{app.incident_description}\n\n"
                f"Requested Action: Please review jurisdictional standing and provide necessary assistance under DLAS protocols.\n\n"
                f"DLAO Officer Signature: _______________________\n"
                f"Date: _______________________"
            )
        elif draft_type == 'mediation_brief':
            content = (
                f"[SIMULATED DRAFT ADR CONCILIATION BRIEF - SUBJECT TO MEDIATOR REVISION]\n\n"
                f"Matter: Conciliation Hearing for {case_record.case_id}\n"
                f"Applicant: {app.name}\n"
                f"Primary Issue: {app.legal_problem}\n\n"
                f"Key Facts for Conciliation:\n{app.incident_description}\n\n"
                f"Suggested Discussion Points:\n"
                f"1. Verification of mutual claims\n"
                f"2. Assessment of voluntary compromise terms\n"
                f"3. Draft settlement terms under Legal Aid Act"
            )
        else:
            content = (
                f"[SIMULATED DRAFT CASE SUMMARY - FOR LEGAL EVALUATION]\n\n"
                f"Case ID: {case_record.case_id} | Priority: {case_record.get_priority_display()}\n"
                f"Applicant: {app.name} ({app.phone})\n"
                f"Legal Category: {app.legal_problem}\n\n"
                f"Factual Allegations:\n{app.incident_description}\n\n"
                f"Recommended Next Steps: File verified statement, compile documentary exhibits, and inspect jurisdictional timeline."
            )

        return {
            'is_simulated': cls.IS_SIMULATED,
            'label_en': cls.LABEL_EN,
            'label_bn': cls.LABEL_BN,
            'draft_type': draft_type,
            'content': content,
            'disclaimer': cls.DISCLAIMER_EN,
        }

    @classmethod
    def flag_inconsistencies(cls, application):
        """
        Detects anomalies or logical inconsistencies for human staff attention.
        """
        flags = []
        phone = application.phone or ""
        if len(phone) != 11 or not phone.startswith("01"):
            flags.append("Contact phone number does not conform to standard 11-digit Bangladesh mobile format (01XXXXXXXXX).")

        desc = application.incident_description or ""
        if len(desc.strip()) < 30:
            flags.append("Incident description is unusually brief (under 30 characters); additional factual inquiry advised.")

        return {
            'is_simulated': cls.IS_SIMULATED,
            'label_en': cls.LABEL_EN,
            'label_bn': cls.LABEL_BN,
            'flags': flags,
            'has_flags': len(flags) > 0,
            'disclaimer': cls.DISCLAIMER_EN,
        }

    @classmethod
    def suggest_duplicates(cls, case_record):
        """
        Deterministic matching against existing CaseRecords to detect potential duplicates.
        Scans:
        - Exact and normalized Name matching
        - Exact Phone matching
        - Address overlap
        - Legal problem category overlap

        Returns a list of tuples: (other_case_record, match_score, match_reason)
        """
        from cases.models import CaseRecord

        target_app = case_record.application
        target_name = (target_app.name or "").strip().lower()
        target_phone = (target_app.phone or "").strip()
        target_problem = (target_app.legal_problem or "").strip().lower()
        target_address = (target_app.address or "").strip().lower()

        candidates = []
        other_cases = CaseRecord.objects.exclude(id=case_record.id).select_related('application')

        for other in other_cases:
            other_app = other.application
            score = 0.0
            reasons = []

            # Phone check (strong signal: 0.40)
            other_phone = (other_app.phone or "").strip()
            if target_phone and other_phone and target_phone == other_phone:
                score += 0.40
                reasons.append(f"Matching contact phone number ({target_phone})")

            # Name check (exact match: 0.40, partial: 0.20)
            other_name = (other_app.name or "").strip().lower()
            if target_name and other_name:
                if target_name == other_name:
                    score += 0.40
                    reasons.append(f"Identical applicant name ('{other_app.name}')")
                elif target_name in other_name or other_name in target_name:
                    score += 0.20
                    reasons.append(f"Similar applicant name match ('{other_app.name}')")

            # Address match (0.10)
            other_address = (other_app.address or "").strip().lower()
            if target_address and other_address:
                if target_address == other_address or any(part in other_address for part in target_address.split() if len(part) > 4):
                    score += 0.10
                    reasons.append("Geographic locality / address overlap")

            # Legal problem match (0.10)
            other_problem = (other_app.legal_problem or "").strip().lower()
            if target_problem and other_problem:
                if target_problem == other_problem or any(part in other_problem for part in target_problem.split() if len(part) > 4):
                    score += 0.10
                    reasons.append("Comparable legal problem domain")

            score = min(score, 1.0)
            if score >= 0.40:
                match_reason = "; ".join(reasons)
                candidates.append((other, score, match_reason))

        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates

    @classmethod
    def run_duplicate_scan(cls, case_record):
        """
        Executes duplicate matching and records DuplicateCandidate instances with STATUS_PENDING.
        NEVER automatically rejects, merges, or closes cases!
        """
        from cases.models import DuplicateCandidate

        suggestions = cls.suggest_duplicates(case_record)
        created_candidates = []

        for other_case, score, reason in suggestions:
            candidate, created = DuplicateCandidate.objects.get_or_create(
                case=case_record,
                possible_case=other_case,
                defaults={
                    'match_score': score,
                    'match_reason': reason,
                    'review_status': DuplicateCandidate.STATUS_PENDING,
                }
            )
            created_candidates.append(candidate)

        return created_candidates
