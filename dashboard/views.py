from django.shortcuts import render
from django.utils import timezone
from accounts.models import UserProfile
from cases.models import Application, CaseRecord, Task
from lawyers.models import LawyerAssignment
from referrals.models import Referral
from mediation.models import Mediation

def index(request):
    """
    Dashboard Hub listing all role dashboards for testing and navigation.
    """
    roles = [
        {'id': 'citizen', 'name_en': 'Citizen', 'name_bn': 'নাগরিক', 'url': 'dashboard:citizen', 'desc_en': 'Submit intake, track application & case progress.', 'desc_bn': 'আবেদন জমা দিন, মামলার অগ্রগতি ও অবস্থা ট্র্যাক করুন।'},
        {'id': 'dlao_officer', 'name_en': 'DLAO Officer', 'name_bn': 'ডিএলএও কর্মকর্তা', 'url': 'dashboard:officer', 'desc_en': 'Review queue, accept/reject, assign lawyers, refer cases.', 'desc_bn': 'মামলার কিউ পর্যালোচনা, গ্রহণ/প্রত্যাখ্যান, আইনজীবী নিয়োগ ও রেফারেল।'},
        {'id': 'support_staff', 'name_en': 'Support Staff', 'name_bn': 'সহায়ক কর্মী', 'url': 'dashboard:support_staff', 'desc_en': 'Permitted case history search, assisting officers and case records.', 'desc_bn': 'অনুমোদিত মামলার ইতিহাস অনুসন্ধান, কর্মকর্তাদের সহায়তা ও নথি ব্যবস্থাপনা।'},
        {'id': 'udc_operator', 'name_en': 'UDC Operator', 'name_bn': 'ইউডিসি উদ্যোক্তা', 'url': 'dashboard:udc_operator', 'desc_en': 'Assisted application intake, citizen consent, review read-back.', 'desc_bn': 'সহায়তাকৃত আবেদন তৈরি, নাগরিকের সম্মতি ও পর্যালোচনা পাঠ।'},
        {'id': 'panel_lawyer', 'name_en': 'Panel Lawyer', 'name_bn': 'প্যানেল আইনজীবী', 'url': 'dashboard:panel_lawyer', 'desc_en': 'Assigned worklist, accept/decline cases, hearings, status updates.', 'desc_bn': 'বরাদ্দকৃত মামলা তালিকা, গ্রহণ/প্রত্যাখ্যান, শুনানির তারিখ ও আপডেট।'},
        {'id': 'mediator', 'name_en': 'Mediator', 'name_bn': 'মধ্যস্থতাকারী', 'url': 'dashboard:mediator', 'desc_en': 'Assigned mediations, scheduling, session attendance, outcomes.', 'desc_bn': 'মধ্যস্থতা মামলা, সময় নির্ধারণ, উপস্থিতি ও নিষ্পত্তির ফলাফল লিপিবদ্ধকরণ।'},
        {'id': 'helpline_agent', 'name_en': 'Helpline Agent (16699)', 'name_bn': 'হেল্পলাইন এজেন্ট (১৬৬৯৯)', 'url': 'dashboard:helpline_agent', 'desc_en': 'Inbound call intake, safe contact logging, status checks.', 'desc_bn': 'ইনবাউন্ড কল গ্রহণ, নিরাপদ যোগাযোগের তথ্য নথিভুক্তকরণ ও অনুসন্ধান।'},
        {'id': 'representative', 'name_en': 'Representative', 'name_bn': 'প্রতিনিধি', 'url': 'dashboard:representative', 'desc_en': 'Assisted applications on behalf of citizens, tracking with consent.', 'desc_bn': 'নাগরিকের পক্ষে আবেদন পেশ ও সম্মতিসহ অগ্রগতি পর্যবেক্ষণ।'},
        {'id': 'admin', 'name_en': 'System Admin', 'name_bn': 'সিস্টেম অ্যাডমিন', 'url': 'dashboard:admin', 'desc_en': 'System overview, audit log stream, simulated service statuses.', 'desc_bn': 'সিস্টেম পর্যবেক্ষণ, অডিট ইভেন্ট লগ ও সিমুলেটেড সার্ভিস স্ট্যাটাস।'},
    ]
    return render(request, 'dashboard/index.html', {'roles': roles})

def citizen_dashboard(request):
    applications = []
    if request.user.is_authenticated:
        applications = Application.objects.filter(applicant_user=request.user).order_by('-created_at')
    
    return render(request, 'dashboard/citizen.html', {
        'applications': applications,
        'applications_count': len(applications) if applications else 0,
    })

def officer_dashboard(request):
    """
    DLAO Officer Dashboard with 6 PRD queue categories:
    New, Pending, Accepted, In Progress, Overdue, Closed.
    """
    new_apps = Application.objects.filter(status=Application.STATUS_SUBMITTED).order_by('-created_at')
    pending_apps = Application.objects.filter(status=Application.STATUS_UNDER_REVIEW).order_by('-created_at')
    accepted_cases = CaseRecord.objects.filter(status=CaseRecord.STATUS_ACCEPTED).order_by('-created_at')
    in_progress_cases = CaseRecord.objects.filter(status__in=[CaseRecord.STATUS_IN_PROGRESS, CaseRecord.STATUS_REFERRED, CaseRecord.STATUS_MEDIATION]).order_by('-created_at')
    
    now = timezone.now()
    overdue_referrals = Referral.objects.filter(deadline__lt=now, status=Referral.STATUS_PENDING) | Referral.objects.filter(status=Referral.STATUS_ESCALATED)
    overdue_tasks = Task.objects.filter(due_at__lt=now, status__in=['pending', 'in_progress']) | Task.objects.filter(status='overdue')
    
    closed_cases = CaseRecord.objects.filter(status=CaseRecord.STATUS_CLOSED).order_by('-closed_at')
    rejected_apps = Application.objects.filter(status=Application.STATUS_REJECTED).order_by('-updated_at')

    return render(request, 'dashboard/officer.html', {
        'new_apps': new_apps,
        'new_count': new_apps.count(),
        'pending_apps': pending_apps,
        'pending_count': pending_apps.count(),
        'accepted_cases': accepted_cases,
        'accepted_count': accepted_cases.count(),
        'in_progress_cases': in_progress_cases,
        'in_progress_count': in_progress_cases.count(),
        'overdue_count': overdue_referrals.count() + overdue_tasks.count(),
        'overdue_referrals': overdue_referrals,
        'overdue_tasks': overdue_tasks,
        'closed_cases': closed_cases,
        'closed_count': closed_cases.count() + rejected_apps.count(),
        'rejected_apps': rejected_apps,
    })

def support_staff_dashboard(request):
    return render(request, 'dashboard/support_staff.html')

def udc_operator_dashboard(request):
    return render(request, 'dashboard/udc_operator.html')

def panel_lawyer_dashboard(request):
    """
    Panel Lawyer Dashboard with assigned worklists and assignment actions.
    """
    pending_assignments = []
    accepted_assignments = []
    declined_assignments = []
    active_cases = []

    if request.user.is_authenticated:
        pending_assignments = LawyerAssignment.objects.filter(lawyer=request.user, status=LawyerAssignment.STATUS_PENDING).order_by('-assigned_at')
        accepted_assignments = LawyerAssignment.objects.filter(lawyer=request.user, status=LawyerAssignment.STATUS_ACCEPTED).order_by('-assigned_at')
        declined_assignments = LawyerAssignment.objects.filter(lawyer=request.user, status=LawyerAssignment.STATUS_DECLINED).order_by('-assigned_at')
        active_cases = CaseRecord.objects.filter(assigned_lawyer=request.user).exclude(status=CaseRecord.STATUS_CLOSED).order_by('-updated_at')

    return render(request, 'dashboard/panel_lawyer.html', {
        'pending_assignments': pending_assignments,
        'pending_count': len(pending_assignments),
        'accepted_assignments': accepted_assignments,
        'accepted_count': len(accepted_assignments),
        'declined_assignments': declined_assignments,
        'declined_count': len(declined_assignments),
        'active_cases': active_cases,
        'active_count': len(active_cases),
    })

def mediator_dashboard(request):
    """
    Mediator Dashboard showing assigned conciliation cases and outcome states.
    """
    mediations = []
    if request.user.is_authenticated:
        mediations = Mediation.objects.filter(mediator=request.user).order_by('-created_at')

    scheduled = [m for m in mediations if m.status == 'scheduled']
    in_progress = [m for m in mediations if m.status in ['in_progress', 'pending']]
    resolved = [m for m in mediations if m.status == 'agreed']
    failed = [m for m in mediations if m.status in ['failed', 'closed']]

    return render(request, 'dashboard/mediator.html', {
        'mediations': mediations,
        'scheduled_count': len(scheduled),
        'in_progress_count': len(in_progress),
        'resolved_count': len(resolved),
        'failed_count': len(failed),
    })

def helpline_agent_dashboard(request):
    return render(request, 'dashboard/helpline_agent.html')

def representative_dashboard(request):
    return render(request, 'dashboard/representative.html')

def admin_dashboard(request):
    """
    DLAS System Administration dashboard.
    Shows:
    - Real-time append-only CaseEvent audit stream
    - Interactive Mock External Services testing hub
    """
    from cases.models import CaseEvent
    from core.mock_services import (
        MockSMSService,
        MockIVRService,
        MockUSSDService,
        MockNIDService,
        MockPaymentService,
        MockSignatureService,
    )
    from cases.ai_service import MockAIService

    events = CaseEvent.objects.all().select_related('case', 'application', 'actor').order_by('-created_at')[:35]
    simulated_result = None

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'test_sms':
            phone = request.POST.get('phone', '01711000000').strip()
            msg = request.POST.get('message', 'DLAS Notification: Your legal aid hearing is scheduled for tomorrow.').strip()
            simulated_result = MockSMSService.send_raw_sms(phone, msg)

        elif action == 'test_ivr':
            phone = request.POST.get('phone', '01711000000').strip()
            query = request.POST.get('query', 'Application Status Check').strip()
            simulated_result = MockIVRService.simulate_inbound_helpline(phone, query)

        elif action == 'test_ussd':
            phone = request.POST.get('phone', '01711000000').strip()
            ussd_input = request.POST.get('ussd_input', '*16699#').strip()
            simulated_result = MockUSSDService.process_ussd_session('DEMO-SESSION-001', phone, ussd_input)

        elif action == 'test_nid':
            nid = request.POST.get('nid_number', '1234567890').strip()
            name = request.POST.get('name', 'Abdul Karim').strip()
            simulated_result = MockNIDService.verify_nid(nid, name=name)

        elif action == 'test_payment':
            simulated_result = MockPaymentService.check_fee_exemption(None)

    return render(request, 'dashboard/admin.html', {
        'events': events,
        'simulated_result': simulated_result,
        'mock_services': [
            {'name': 'MockSMSService', 'desc_en': 'Citizen notifications & safe-contact OTP', 'desc_bn': 'নাগরিক নোটিফিকেশন ও নিরাপদ বিকল্প নম্বর ওটিপি', 'status': 'SIMULATED'},
            {'name': 'MockIVRService', 'desc_en': '16699 National Helpline Telephony Simulator', 'desc_bn': '১৬৬৯৯ জাতীয় হেল্পলাইন ভয়েস টেলিফোনি সিমুলেটর', 'status': 'SIMULATED'},
            {'name': 'MockUSSDService', 'desc_en': 'Offline feature phone *16699# status check', 'desc_bn': 'ফিচার ফোনে অফলাইন *১৬৬৯৯# অনুসন্ধান গেটওয়ে', 'status': 'SIMULATED'},
            {'name': 'MockNIDService', 'desc_en': 'National ID deterministic applicant verification', 'desc_bn': 'জাতীয় পরিচয়পত্র পরীক্ষামূলক সিমুলেটেড যাচাইকরণ', 'status': 'SIMULATED'},
            {'name': 'MockPaymentService', 'desc_en': 'LASA 2000 fee waiver & lawyer honorarium voucher', 'desc_bn': 'লিগ্যাল এইড ফি মওকুফ ও আইনজীবী ভাতা সিমুলেশন', 'status': 'SIMULATED'},
            {'name': 'MockSignatureService', 'desc_en': 'SHA-256 document hashing & signature verification', 'desc_bn': 'এসএইচএ-২৫৬ হ্যাশিং ও ইলেকট্রনিক স্বাক্ষর সিমুলেশন', 'status': 'SIMULATED'},
            {'name': 'MockAIService', 'desc_en': 'Assistive extraction, drafting, duplicate scanning', 'desc_bn': 'সহায়তাকৃত তথ্য নিষ্কাশন ও ড্রাফটিং', 'status': 'SIMULATED'},
        ]
    })

