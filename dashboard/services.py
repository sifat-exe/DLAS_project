from django.utils import timezone
from django.db.models import Case, When, Value, IntegerField, Q
from django.urls import reverse
from accounts.models import UserProfile
from accounts.permissions import has_role
from cases.models import Application, CaseRecord, CaseEvent, Task
from lawyers.models import LawyerAssignment
from referrals.models import Referral
from mediation.models import Mediation
from documents.models import Document


def get_case_record_priority_rank():
    """
    Returns Django Case/When expression ordering CaseRecords server-side by priority:
    1. URGENT
    2. HIGH
    3. MEDIUM
    4. LOW
    """
    return Case(
        When(priority=CaseRecord.PRIORITY_URGENT, then=Value(1)),
        When(priority=CaseRecord.PRIORITY_HIGH, then=Value(2)),
        When(priority=CaseRecord.PRIORITY_MEDIUM, then=Value(3)),
        When(priority=CaseRecord.PRIORITY_LOW, then=Value(4)),
        default=Value(5),
        output_field=IntegerField(),
    )


def get_role_workspace_context(user, role_name=None):
    """
    Computes the role-specific workspace action center data for the logged-in user:
    A. What To Do Next (actionable pending items, strictly authorized)
    B. Recently Done (recent audit events from CaseEvent)
    C. Next Step (clear call-to-action for the top priority item)

    Protects against IDOR: users only see items they are authorized to act on.
    Handles unauthenticated and unauthorized requests gracefully without errors.
    """
    context = {
        'role': role_name or '',
        'has_access': False,
        'pending_actions': [],
        'pending_count': 0,
        'urgent_count': 0,
        'recently_done': [],
        'top_next_step': None,
    }

    if not user or not user.is_authenticated:
        return context

    # Determine user's effective role
    user_role = ''
    if hasattr(user, 'profile') and user.profile.role:
        user_role = user.profile.role
    elif user.is_superuser:
        user_role = UserProfile.ROLE_ADMIN

    target_role = role_name or user_role
    now = timezone.now()
    rank_expr = get_case_record_priority_rank()

    # -------------------------------------------------------------------------
    # 1. DLAO OFFICER WORKSPACE
    # -------------------------------------------------------------------------
    if target_role in [UserProfile.ROLE_DLAO_OFFICER, 'dlao_officer', 'officer']:
        if not has_role(user, [UserProfile.ROLE_DLAO_OFFICER, UserProfile.ROLE_ADMIN]):
            return context
        context['has_access'] = True

        pending = []

        # A1. Urgent Cases awaiting attention (FIFO within URGENT)
        urgent_cases = (
            CaseRecord.objects.filter(priority=CaseRecord.PRIORITY_URGENT)
            .exclude(status=CaseRecord.STATUS_CLOSED)
            .order_by('created_at')
        )
        for c in urgent_cases[:5]:
            pending.append({
                'identifier': c.case_id,
                'priority': 'URGENT',
                'priority_label_en': 'URGENT',
                'priority_label_bn': 'জরুরি',
                'title_en': f"Urgent Case: {c.case_id} — {c.application.name}",
                'title_bn': f"জরুরি মামলা: {c.case_id} — {c.application.name}",
                'detail_en': f"Priority matter: {c.application.legal_problem[:60]}...",
                'detail_bn': f"জরুরি সমস্যা: {c.application.legal_problem[:60]}...",
                'action_text_en': "Review Urgent Case →",
                'action_text_bn': "জরুরি মামলা পর্যালোচনা করুন →",
                'url': reverse('cases:officer_case_detail', kwargs={'case_id': c.case_id}),
                'created_at': c.created_at,
            })

        # A2. New Submitted Applications awaiting DLAO review (FIFO older first)
        new_apps = (
            Application.objects.filter(status=Application.STATUS_SUBMITTED)
            .order_by('created_at')
        )
        for app in new_apps[:6]:
            pending.append({
                'identifier': app.application_id,
                'priority': 'HIGH' if app.nid_verification_status == 'verified' else 'NORMAL',
                'priority_label_en': 'Awaiting Review',
                'priority_label_bn': 'পর্যালোচনার অপেক্ষায়',
                'title_en': f"Intake Application: {app.application_id} — {app.name}",
                'title_bn': f"নতুন আবেদন: {app.application_id} — {app.name}",
                'detail_en': f"{app.legal_problem[:60]}... ({app.get_preferred_channel_display()})",
                'detail_bn': f"{app.legal_problem[:60]}... ({app.get_preferred_channel_display()})",
                'action_text_en': "Review & Decide →",
                'action_text_bn': "পর্যালোচনা ও সিদ্ধান্ত →",
                'url': reverse('cases:officer_application_detail', kwargs={'application_id': app.application_id}),
                'created_at': app.created_at,
            })

        # A3. Accepted cases awaiting lawyer assignment
        unassigned = (
            CaseRecord.objects.filter(status=CaseRecord.STATUS_ACCEPTED, assigned_lawyer__isnull=True)
            .annotate(priority_rank=rank_expr)
            .order_by('priority_rank', 'created_at')
        )
        for c in unassigned[:4]:
            pending.append({
                'identifier': c.case_id,
                'priority': c.priority,
                'priority_label_en': f"Assign Lawyer ({c.get_priority_display()})",
                'priority_label_bn': f"আইনজীবী নিয়োগ ({c.get_priority_display()})",
                'title_en': f"Accepted Case Awaiting Lawyer: {c.case_id}",
                'title_bn': f"আইনজীবী বরাদ্দের অপেক্ষায়: {c.case_id}",
                'detail_en': f"Accepted for {c.application.name}. Assign panel lawyer to commence representation.",
                'detail_bn': f"{c.application.name}-এর জন্য মামলা গৃহীত হয়েছে। আইনজীবী নিয়োগ করুন।",
                'action_text_en': "Assign Lawyer →",
                'action_text_bn': "আইনজীবী বরাদ্দ করুন →",
                'url': reverse('cases:officer_case_detail', kwargs={'case_id': c.case_id}),
                'created_at': c.created_at,
            })

        # A4. Reassigned referrals handed off to this officer
        reassigned_to_user = Referral.objects.filter(
            assigned_officer=user,
            status=Referral.STATUS_REASSIGNED
        ).order_by('-reassigned_at')
        for ref in reassigned_to_user[:3]:
            deadline_str = ref.deadline.strftime('%d %b %Y, %I:%M %p') if ref.deadline else 'N/A'
            prev_name = ref.previous_officer.get_full_name() or ref.previous_officer.username if ref.previous_officer else 'previous officer'
            pending.append({
                'identifier': f"REF-{ref.id}",
                'priority': 'URGENT',
                'priority_label_en': 'Reassigned Referral',
                'priority_label_bn': 'পুনঃহস্তান্তরিত রেফারাল',
                'title_en': f"Handed Off Referral #{ref.id}: {ref.destination}",
                'title_bn': f"হস্তান্তরিত রেফারাল #{ref.id}: {ref.destination}",
                'detail_en': (
                    f"Deadline missed by {prev_name}. "
                    f"Referral handed off to you. Requires immediate acknowledgement and follow-up."
                ),
                'detail_bn': (
                    f"পূর্ববর্তী কর্মকর্তা ({prev_name}) সময়সীমা মিস করায় আপনার নিকট হস্তান্তর করা হয়েছে। "
                    f"অবিলম্বে গ্রহণ নিশ্চিতকরণ এবং পদক্ষেপ আবশ্যক।"
                ),
                'action_text_en': "Acknowledge Referral →",
                'action_text_bn': "রেফারাল গ্রহণ করুন →",
                'url': reverse('referrals:referral_detail', kwargs={'referral_id': ref.id}),
                'created_at': ref.reassigned_at or ref.created_at,
            })

        # A5. Overdue referrals
        overdue_refs = Referral.objects.filter(
            Q(deadline__lt=now, status__in=[Referral.STATUS_PENDING, Referral.STATUS_REASSIGNED]) |
            Q(status=Referral.STATUS_ESCALATED)
        ).exclude(assigned_officer=user, status=Referral.STATUS_REASSIGNED).order_by('deadline')
        for ref in overdue_refs[:3]:
            ref_id_str = f"REF-{ref.id}"
            deadline_str = ref.deadline.strftime('%d %b %Y') if ref.deadline else 'N/A'
            pending.append({
                'identifier': ref_id_str,
                'priority': 'URGENT',
                'priority_label_en': 'Overdue Referral',
                'priority_label_bn': 'সময়োত্তীর্ণ রেফারেল',
                'title_en': f"Overdue Referral: #{ref.id} ({ref.destination})",
                'title_bn': f"সময়োত্তীর্ণ রেফারেল: #{ref.id} ({ref.destination})",
                'detail_en': f"Case {ref.case.case_id} — Deadline passed {deadline_str}.",
                'detail_bn': f"মামলা {ref.case.case_id} — সময়সীমা অতিক্রম: {deadline_str}।",
                'action_text_en': "Follow Up Referral →",
                'action_text_bn': "রেফারেল অনুসন্ধান →",
                'url': reverse('referrals:referral_detail', kwargs={'referral_id': ref.id}),
                'created_at': ref.created_at,
            })

        context['pending_actions'] = pending
        context['pending_count'] = len(pending)
        context['urgent_count'] = sum(1 for p in pending if p['priority'] == 'URGENT')

        # B. Recently Done: recent audit events from CaseEvent
        events = CaseEvent.objects.filter(
            Q(actor=user) | Q(actor_role__in=['dlao_officer', 'dlao_support_staff', 'admin'])
        ).select_related('case', 'application', 'actor').order_by('-created_at')[:7]
        context['recently_done'] = list(events)

        # C. Next Step: Top actionable priority
        if reassigned_to_user.exists():
            top = reassigned_to_user.first()
            prev_name = top.previous_officer.get_full_name() or top.previous_officer.username if top.previous_officer else 'previous officer'
            context['top_next_step'] = {
                'headline_en': "Acknowledge Reassigned Referral",
                'headline_bn': "পুনঃহস্তান্তরিত রেফারাল গ্রহণ নিশ্চিত করুন",
                'instruction_en': (
                    f"Referral #{top.id} ({top.destination}) was handed off to you after {prev_name} "
                    f"missed the acknowledgement deadline. Review package and take custody."
                ),
                'instruction_bn': (
                    f"রেফারাল #{top.id} ({top.destination})-এর সময়সীমা পূর্ববর্তী কর্মকর্তা মিস করায় আপনার নিকট হস্তান্তর করা হয়েছে। "
                    f"প্যাকেজটি পর্যালোচনা করে অবিলম্বে দায়িত্ব গ্রহণ নিশ্চিত করুন।"
                ),
                'target_id': f"REF-{top.id}",
                'url': reverse('referrals:referral_detail', kwargs={'referral_id': top.id}),
                'action_btn_en': "Acknowledge Referral",
                'action_btn_bn': "রেফারাল গ্রহণ করুন",
                'is_urgent': True,
            }
        elif urgent_cases.exists():
            top = urgent_cases.first()
            context['top_next_step'] = {
                'headline_en': "Review This Urgent Case",
                'headline_bn': "এই জরুরি মামলাটি পর্যালোচনা করুন",
                'instruction_en': f"Case {top.case_id} ({top.application.name}) is marked URGENT. Review incident details and assign legal representation immediately.",
                'instruction_bn': f"মামলা {top.case_id} ({top.application.name}) জরুরি হিসেবে চিহ্নিত। দ্রুত বিবরণ পরীক্ষা করুন এবং আইনি পদক্ষেপ নিন।",
                'target_id': top.case_id,
                'url': reverse('cases:officer_case_detail', kwargs={'case_id': top.case_id}),
                'action_btn_en': "Open Urgent Case Workspace",
                'action_btn_bn': "জরুরি মামলা পরিচালনা করুন",
                'is_urgent': True,
            }
        elif new_apps.exists():
            top = new_apps.first()
            context['top_next_step'] = {
                'headline_en': "Review Oldest Pending Application",
                'headline_bn': "প্রাচীনতম অপেক্ষমাণ আবেদন পর্যালোচনা করুন",
                'instruction_en': f"Application {top.application_id} submitted by {top.name} is awaiting eligibility determination. Accept or request clarification.",
                'instruction_bn': f"আবেদন {top.application_id} ({top.name}) পর্যালোচনার অপেক্ষায়। যোগ্যতা যাচাই করে গ্রহণ বা কারণসহ নিষ্পত্তি করুন।",
                'target_id': top.application_id,
                'url': reverse('cases:officer_application_detail', kwargs={'application_id': top.application_id}),
                'action_btn_en': "Review Application",
                'action_btn_bn': "আবেদন পর্যালোচনা করুন",
                'is_urgent': False,
            }
        elif unassigned.exists():
            top = unassigned.first()
            context['top_next_step'] = {
                'headline_en': "Assign Lawyer to Accepted Case",
                'headline_bn': "গৃহীত মামলার জন্য আইনজীবী নিয়োগ করুন",
                'instruction_en': f"Case {top.case_id} has been accepted but does not have an assigned panel lawyer yet.",
                'instruction_bn': f"মামলা {top.case_id} গৃহীত হয়েছে কিন্তু এখনো কোনো প্যানেল আইনজীবী বরাদ্দ করা হয়নি।",
                'target_id': top.case_id,
                'url': reverse('cases:officer_case_detail', kwargs={'case_id': top.case_id}),
                'action_btn_en': "Assign Lawyer Now",
                'action_btn_bn': "আইনজীবী নিয়োগ করুন",
                'is_urgent': False,
            }
        elif overdue_refs.exists():
            top = overdue_refs.first()
            context['top_next_step'] = {
                'headline_en': "Follow Up Overdue Referral",
                'headline_bn': "সময়োত্তীর্ণ রেফারেলের অগ্রগতি যাচাই করুন",
                'instruction_en': f"Referral #{top.id} to {top.destination} has passed its acknowledgement deadline.",
                'instruction_bn': f"রেফারেল #{top.id} ({top.destination}) সময়সীমা অতিক্রম করেছে। সংস্থাটির সাথে যোগাযোগ করুন।",
                'target_id': f"REF-{top.id}",
                'url': reverse('referrals:referral_detail', kwargs={'referral_id': top.id}),
                'action_btn_en': "Inspect Referral",
                'action_btn_bn': "রেফারেল দেখুন",
                'is_urgent': True,
            }

    # -------------------------------------------------------------------------
    # 2. PANEL LAWYER WORKSPACE
    # -------------------------------------------------------------------------
    elif target_role in [UserProfile.ROLE_PANEL_LAWYER, 'panel_lawyer', 'lawyer']:
        if not has_role(user, [UserProfile.ROLE_PANEL_LAWYER, UserProfile.ROLE_ADMIN]):
            return context
        context['has_access'] = True

        pending = []

        # A1. Pending Lawyer Assignments (strictly lawyer=user)
        pending_asgns = LawyerAssignment.objects.filter(
            lawyer=user,
            status=LawyerAssignment.STATUS_PENDING
        ).select_related('case', 'case__application').order_by('assigned_at')

        for asgn in pending_asgns:
            pending.append({
                'identifier': asgn.case.case_id,
                'priority': 'URGENT' if asgn.case.priority == 'URGENT' else 'HIGH',
                'priority_label_en': 'Assignment Decision Required',
                'priority_label_bn': 'বরাদ্দ সিদ্ধান্ত আবশ্যক',
                'title_en': f"Case Assignment: {asgn.case.case_id} — {asgn.case.application.name}",
                'title_bn': f"মামলা বরাদ্দ: {asgn.case.case_id} — {asgn.case.application.name}",
                'detail_en': f"Assigned by DLAO. Matter: {asgn.case.application.legal_problem[:60]}...",
                'detail_bn': f"ডিএলএও কর্তৃক বরাদ্দ। বিষয়: {asgn.case.application.legal_problem[:60]}...",
                'action_text_en': "Accept or Decline →",
                'action_text_bn': "গ্রহণ বা প্রত্যাখ্যান →",
                'url': reverse('lawyers:case_detail', kwargs={'case_id': asgn.case.case_id}),
                'created_at': asgn.assigned_at,
            })

        # A2. Active cases needing hearing or proceeding update
        active_cases = CaseRecord.objects.filter(
            assigned_lawyer=user
        ).exclude(status=CaseRecord.STATUS_CLOSED).order_by('updated_at')

        for c in active_cases[:4]:
            pending.append({
                'identifier': c.case_id,
                'priority': c.priority,
                'priority_label_en': f"Active Case ({c.get_status_display()})",
                'priority_label_bn': f"চলমান মামলা ({c.get_status_display()})",
                'title_en': f"Active Proceeding: {c.case_id} ({c.application.name})",
                'title_bn': f"চলমান আইনি প্রক্রিয়া: {c.case_id} ({c.application.name})",
                'detail_en': f"Keep court updates, next hearing date, and steps documented.",
                'detail_bn': f"আদালতের শুনানি, পরবর্তী তারিখ এবং অগ্রগতি লিপিবদ্ধ রাখুন।",
                'action_text_en': "Record Proceeding Update →",
                'action_text_bn': "অগ্রগতি আপডেট দাখিল করুন →",
                'url': reverse('lawyers:case_detail', kwargs={'case_id': c.case_id}),
                'created_at': c.updated_at,
            })

        context['pending_actions'] = pending
        context['pending_count'] = len(pending)
        context['urgent_count'] = sum(1 for p in pending if p['priority'] == 'URGENT')

        # B. Recently Done for this lawyer
        events = CaseEvent.objects.filter(
            Q(actor=user) | Q(case__assigned_lawyer=user)
        ).select_related('case', 'application', 'actor').order_by('-created_at')[:7]
        context['recently_done'] = list(events)

        # C. Next Step
        if pending_asgns.exists():
            top = pending_asgns.first()
            context['top_next_step'] = {
                'headline_en': "Respond to Pending Case Assignment",
                'headline_bn': "অপেক্ষমাণ মামলা বরাদ্দের সিদ্ধান্ত নিন",
                'instruction_en': f"You have been assigned Case {top.case.case_id} ({top.case.application.name}). Review conflict of interest and accept or decline representation.",
                'instruction_bn': f"আপনাকে {top.case.case_id} ({top.case.application.name}) মামলাটি বরাদ্দ করা হয়েছে। স্বার্থের সংঘাত না থাকলে মামলাটি গ্রহণ করুন।",
                'target_id': top.case.case_id,
                'url': reverse('lawyers:case_detail', kwargs={'case_id': top.case.case_id}),
                'action_btn_en': "Decide on Assignment",
                'action_btn_bn': "সিদ্ধান্ত গ্রহণ করুন",
                'is_urgent': True,
            }
        elif active_cases.exists():
            top = active_cases.first()
            context['top_next_step'] = {
                'headline_en': "Submit Case Proceeding Update",
                'headline_bn': "মামলার শুনানির অগ্রগতি দাখিল করুন",
                'instruction_en': f"Record the latest court appearance and outcome for Case {top.case_id}.",
                'instruction_bn': f"মামলা {top.case_id}-এর সর্বশেষ আদালতের শুনানি এবং পরবর্তী পদক্ষেপ লিপিবদ্ধ করুন।",
                'target_id': top.case_id,
                'url': reverse('lawyers:case_detail', kwargs={'case_id': top.case_id}),
                'action_btn_en': "Open Lawyer Case Workspace",
                'action_btn_bn': "মামলা ড্যাশবোর্ড খুলুন",
                'is_urgent': False,
            }

    # -------------------------------------------------------------------------
    # 3. MEDIATOR WORKSPACE
    # -------------------------------------------------------------------------
    elif target_role in [UserProfile.ROLE_MEDIATOR, 'mediator']:
        if not has_role(user, [UserProfile.ROLE_MEDIATOR, UserProfile.ROLE_ADMIN]):
            return context
        context['has_access'] = True

        pending = []
        user_mediations = Mediation.objects.filter(mediator=user).select_related('case', 'case__application')

        # A1. Mediations needing scheduling
        unscheduled = user_mediations.filter(status='pending').order_by('created_at')
        for m in unscheduled[:4]:
            pending.append({
                'identifier': m.case.case_id,
                'priority': 'HIGH',
                'priority_label_en': 'Schedule Session',
                'priority_label_bn': 'সেশন নির্ধারণ',
                'title_en': f"Mediation: {m.case.case_id} — {m.case.application.name}",
                'title_bn': f"মধ্যস্থতা: {m.case.case_id} — {m.case.application.name}",
                'detail_en': "ADR matter assigned. Schedule conciliation session and issue party notices.",
                'detail_bn': "বিকল্প বিরোধ নিষ্পত্তি মামলা বরাদ্দ হয়েছে। অধিবেশনের তারিখ নির্ধারণ করুন।",
                'action_text_en': "Schedule Session →",
                'action_text_bn': "সেশন নির্ধারণ করুন →",
                'url': reverse('dashboard:mediator'),
                'created_at': m.created_at,
            })

        # A2. In-progress sessions needing attendance or settlement outcome
        scheduled = user_mediations.filter(status__in=['scheduled', 'in_progress']).order_by('scheduled_at')
        for m in scheduled[:4]:
            pending.append({
                'identifier': m.case.case_id,
                'priority': 'NORMAL',
                'priority_label_en': 'Record Outcome',
                'priority_label_bn': 'ফলাফল লিপিবদ্ধকরণ',
                'title_en': f"Session Conducted: {m.case.case_id}",
                'title_bn': f"পরিচালিত অধিবেশন: {m.case.case_id}",
                'detail_en': f"Record party attendance and settlement accord / ADR outcome.",
                'detail_bn': f"অধিবেশনে পক্ষসমূহের উপস্থিতি এবং মীমাংসার ফলাফল সংরক্ষণ করুন।",
                'action_text_en': "Record Outcome →",
                'action_text_bn': "ফলাফল সংরক্ষণ করুন →",
                'url': reverse('dashboard:mediator'),
                'created_at': m.scheduled_at or m.created_at,
            })

        context['pending_actions'] = pending
        context['pending_count'] = len(pending)
        context['urgent_count'] = sum(1 for p in pending if p['priority'] == 'URGENT')

        events = CaseEvent.objects.filter(
            Q(actor=user) | Q(case__in=user_mediations.values_list('case_id', flat=True))
        ).select_related('case', 'application', 'actor').order_by('-created_at')[:7]
        context['recently_done'] = list(events)

        if unscheduled.exists():
            top = unscheduled.first()
            context['top_next_step'] = {
                'headline_en': "Schedule Conciliation Session",
                'headline_bn': "আপস-মীমাংসা অধিবেশন নির্ধারণ করুন",
                'instruction_en': f"Fix hearing date for Case {top.case.case_id} ({top.case.application.name}) and notify dispute parties.",
                'instruction_bn': f"মামলা {top.case.case_id} ({top.case.application.name})-এর জন্য মধ্যস্থতা বৈঠকের সময় নির্ধারণ করুন।",
                'target_id': top.case.case_id,
                'url': reverse('dashboard:mediator'),
                'action_btn_en': "Schedule Hearing Date",
                'action_btn_bn': "তারিখ নির্ধারণ করুন",
                'is_urgent': False,
            }
        elif scheduled.exists():
            top = scheduled.first()
            context['top_next_step'] = {
                'headline_en': "Record Mediation Outcome",
                'headline_bn': "মধ্যস্থতার ফলাফল লিপিবদ্ধ করুন",
                'instruction_en': f"Submit attendance and final conciliation accord for Case {top.case.case_id}.",
                'instruction_bn': f"মামলা {top.case.case_id}-এর উপস্থিতি ও আপসনামা বা সমাপ্তি প্রতিবেদন দাখিল করুন।",
                'target_id': top.case.case_id,
                'url': reverse('dashboard:mediator'),
                'action_btn_en': "Record ADR Result",
                'action_btn_bn': "ফলাফল সংরক্ষণ করুন",
                'is_urgent': False,
            }

    # -------------------------------------------------------------------------
    # 4. SUPPORT STAFF WORKSPACE
    # -------------------------------------------------------------------------
    elif target_role in [UserProfile.ROLE_DLAO_SUPPORT_STAFF, 'dlao_support_staff', 'support_staff']:
        if not has_role(user, [UserProfile.ROLE_DLAO_SUPPORT_STAFF, UserProfile.ROLE_DLAO_OFFICER, UserProfile.ROLE_ADMIN]):
            return context
        context['has_access'] = True

        pending = []

        # A1. Pending document verification
        unverified_docs = Document.objects.filter(
            status=Document.STATUS_UPLOADED
        ).select_related('case', 'uploaded_by').order_by('created_at')

        for doc in unverified_docs[:5]:
            pending.append({
                'identifier': doc.case.case_id,
                'priority': 'HIGH',
                'priority_label_en': 'Verify Document',
                'priority_label_bn': 'নথি যাচাই',
                'title_en': f"Document: '{doc.title}' ({doc.case.case_id})",
                'title_bn': f"সংযুক্ত নথি: '{doc.title}' ({doc.case.case_id})",
                'detail_en': f"Uploaded for {doc.case.application.name}. Verify authenticity against originals.",
                'detail_bn': f"{doc.case.application.name}-এর জন্য আপলোডকৃত। মূল নথির সাথে মিলিয়ে যাচাই করুন।",
                'action_text_en': "Examine & Verify →",
                'action_text_bn': "পরীক্ষা ও যাচাই করুন →",
                'url': reverse('cases:officer_case_detail', kwargs={'case_id': doc.case.case_id}),
                'created_at': doc.created_at,
            })

        # A2. Operational tasks assigned to this user
        user_tasks = Task.objects.filter(
            assigned_to=user,
            status__in=['pending', 'in_progress']
        ).select_related('case').order_by('due_at')

        for t in user_tasks[:4]:
            pending.append({
                'identifier': t.case.case_id,
                'priority': 'URGENT' if t.due_at < now else 'NORMAL',
                'priority_label_en': 'Assigned Task Due',
                'priority_label_bn': 'অর্পিত দায়িত্ব',
                'title_en': f"Task: {t.title} ({t.case.case_id})",
                'title_bn': f"দায়িত্ব: {t.title} ({t.case.case_id})",
                'detail_en': f"Due by {t.due_at.strftime('%d %b %Y, %I:%M %p')}.",
                'detail_bn': f"সময়সীমা: {t.due_at.strftime('%d %b %Y, %I:%M %p')}।",
                'action_text_en': "Complete Task →",
                'action_text_bn': "সম্পন্ন করুন →",
                'url': reverse('cases:officer_case_detail', kwargs={'case_id': t.case.case_id}),
                'created_at': t.due_at,
            })

        context['pending_actions'] = pending
        context['pending_count'] = len(pending)
        context['urgent_count'] = sum(1 for p in pending if p['priority'] == 'URGENT')

        events = CaseEvent.objects.filter(
            Q(actor=user) | Q(actor_role='dlao_support_staff')
        ).select_related('case', 'application', 'actor').order_by('-created_at')[:7]
        context['recently_done'] = list(events)

        if unverified_docs.exists():
            top = unverified_docs.first()
            context['top_next_step'] = {
                'headline_en': "Verify Uploaded Case Document",
                'headline_bn': "সংযুক্ত মামলার নথি পরীক্ষা ও যাচাই করুন",
                'instruction_en': f"Document '{top.title}' for Case {top.case.case_id} is awaiting verification.",
                'instruction_bn': f"মামলা {top.case.case_id}-এর '{top.title}' নথিটি যাচাইয়ের অপেক্ষায় রয়েছে।",
                'target_id': top.case.case_id,
                'url': reverse('cases:officer_case_detail', kwargs={'case_id': top.case.case_id}),
                'action_btn_en': "Verify Document Now",
                'action_btn_bn': "এখনই নথি যাচাই করুন",
                'is_urgent': False,
            }
        elif user_tasks.exists():
            top = user_tasks.first()
            context['top_next_step'] = {
                'headline_en': "Complete Assigned Administrative Task",
                'headline_bn': "অর্পিত প্রশাসনিক কাজটি সম্পন্ন করুন",
                'instruction_en': f"Task '{top.title}' for Case {top.case.case_id} is due for completion.",
                'instruction_bn': f"মামলা {top.case.case_id}-এর '{top.title}' কাজটি সম্পন্ন করুন।",
                'target_id': top.case.case_id,
                'url': reverse('cases:officer_case_detail', kwargs={'case_id': top.case.case_id}),
                'action_btn_en': "View Case & Task",
                'action_btn_bn': "মামলা ও কাজ দেখুন",
                'is_urgent': (top.due_at < now),
            }

    # -------------------------------------------------------------------------
    # 5. HELPLINE AGENT WORKSPACE
    # -------------------------------------------------------------------------
    elif target_role in [UserProfile.ROLE_HELPLINE_AGENT, 'helpline_agent', 'helpline']:
        if not has_role(user, [UserProfile.ROLE_HELPLINE_AGENT, UserProfile.ROLE_ADMIN]):
            return context
        context['has_access'] = True

        pending = []
        helpline_apps = Application.objects.filter(
            preferred_channel=Application.CHANNEL_HELPLINE,
            status=Application.STATUS_SUBMITTED
        ).order_by('created_at')

        for app in helpline_apps[:5]:
            pending.append({
                'identifier': app.application_id,
                'priority': 'HIGH' if app.safe_contact_number else 'NORMAL',
                'priority_label_en': 'Helpline Intake',
                'priority_label_bn': 'হেল্পলাইন আবেদন',
                'title_en': f"16699 Intake: {app.application_id} — {app.name}",
                'title_bn': f"১৬৬৯৯ আবেদন: {app.application_id} — {app.name}",
                'detail_en': f"Phone: {app.phone}. Safe window: {app.safe_contact_time or 'Standard'}.",
                'detail_bn': f"ফোন: {app.phone}। নিরাপদ সময়: {app.safe_contact_time or 'সাধারণ'}।",
                'action_text_en': "Inspect Intake →",
                'action_text_bn': "আবেদন দেখুন →",
                'url': reverse('dashboard:helpline_agent'),
                'created_at': app.created_at,
            })

        context['pending_actions'] = pending
        context['pending_count'] = len(pending)
        context['urgent_count'] = sum(1 for p in pending if p['priority'] == 'URGENT')

        events = CaseEvent.objects.filter(
            Q(actor=user) | Q(channel='helpline')
        ).select_related('case', 'application', 'actor').order_by('-created_at')[:7]
        context['recently_done'] = list(events)

        if helpline_apps.exists():
            top = helpline_apps.first()
            context['top_next_step'] = {
                'headline_en': "Verify Safe Callback Information",
                'headline_bn': "নিরাপদ যোগাযোগের সময়সূচী নিশ্চিত করুন",
                'instruction_en': f"Caller {top.name} ({top.phone}) requested telephonic legal aid assistance.",
                'instruction_bn': f"কলার {top.name} ({top.phone}) টেলিফোনে আইনি সহায়তার অনুরোধ করেছেন।",
                'target_id': top.application_id,
                'url': reverse('dashboard:helpline_agent'),
                'action_btn_en': "Inspect Helpline Record",
                'action_btn_bn': "হেল্পলাইন রেকর্ড দেখুন",
                'is_urgent': False,
            }

    return context
