import time
import uuid
from django.utils import timezone
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from accounts.models import UserProfile
from accounts.permissions import has_role, can_access_application, can_access_case
from cases.models import Application, CaseRecord, CaseEvent, Task, RelatedCase, DuplicateCandidate, Communication
from documents.models import Document, Signature
from cases.forms import CitizenApplicationForm, UDCAssistedApplicationForm
from cases.services import (
    submit_application,
    accept_application,
    reject_application,
    verify_application_nid,
    change_case_priority,
    assign_lawyer_to_case,
    create_case_referral,
    initiate_case_mediation,
    transition_case_status,
    upload_case_document,
    verify_case_document,
    link_related_cases,
    review_duplicate_candidate,
)
from cases.ai_service import MockAIService
from core.mock_services import (
    MockSMSService,
    MockIVRService,
    MockUSSDService,
    MockNIDService,
    MockPaymentService,
    MockSignatureService,
)

def index(request):
    """
    Cases index view: displays case records overview and entry points.
    """
    return render(request, 'cases/index.html')

@login_required
def application_create(request):
    """
    Citizen intake workflow:
    Application Form -> Review -> Final Submit -> Application ID generated.
    Includes backend duplicate submission protection:
    - Unique submission token / idempotency key
    - Session timestamp guard against double-clicks
    - Recent duplicate submission suppression
    Defined in docs/MASTER_PRD.md Section 4.1.
    """
    if request.method == 'POST':
        action = request.POST.get('action', 'review')
        submission_token = request.POST.get('submission_token', '').strip()

        # If returning from review to edit
        if action == 'edit':
            form = CitizenApplicationForm(request.POST)
            return render(request, 'cases/application_form.html', {
                'form': form,
                'submission_token': submission_token or uuid.uuid4().hex,
            })

        form = CitizenApplicationForm(request.POST)

        if form.is_valid():
            cleaned = form.cleaned_data

            # Review step: show review summary screen before final submission
            if action == 'review':
                token = submission_token or request.session.get('pending_submission_token') or uuid.uuid4().hex
                request.session['pending_submission_token'] = token
                return render(request, 'cases/application_review.html', {
                    'form_data': cleaned,
                    'form': form,
                    'submission_token': token,
                })

            # Final submit step
            elif action == 'submit':
                # Backend duplicate submission protection: Token Check
                token = submission_token or request.session.get('pending_submission_token')
                consumed_tokens = request.session.get('consumed_submission_tokens', [])
                if token and token in consumed_tokens:
                    last_app_id = request.session.get('last_submitted_app_id')
                    if last_app_id:
                        messages.info(
                            request,
                            f"This application has already been submitted (Application ID: {last_app_id}). / এই আবেদনটি ইতিমধ্যে জমা দেওয়া হয়েছে।"
                        )
                        return redirect('cases:application_detail', application_id=last_app_id)
                    else:
                        messages.warning(request, "Duplicate submission prevented. / ডুপ্লিকেট সাবমিশন রোধ করা হয়েছে।")
                        return redirect('dashboard:citizen')

                # Double-click rapid guard (3 seconds)
                last_submit = request.session.get('last_application_submit_time', 0)
                now = time.time()
                if now - last_submit < 3:
                    messages.warning(request, "Submission already in progress. Please do not submit repeatedly. / আবেদন প্রক্রিয়াধীন রয়েছে, পুনরায় চাপবেন না।")
                    last_app_id = request.session.get('last_submitted_app_id')
                    if last_app_id:
                        return redirect('cases:application_detail', application_id=last_app_id)
                    return redirect('dashboard:citizen')
                request.session['last_application_submit_time'] = now

                # Check if identical application was submitted within last 10 seconds
                recent_duplicate = Application.objects.filter(
                    applicant_user=request.user,
                    name=cleaned['name'],
                    phone=cleaned['phone'],
                    legal_problem=cleaned['legal_problem'],
                    created_at__gte=timezone.now() - timezone.timedelta(seconds=10)
                ).first()
                if recent_duplicate:
                    messages.info(
                        request,
                        f"This application has already been received (Application ID: {recent_duplicate.application_id}). / এই আবেদনটি ইতিমধ্যে জমা দেওয়া হয়েছে।"
                    )
                    return redirect('cases:application_detail', application_id=recent_duplicate.application_id)

                application = submit_application(
                    name=cleaned['name'],
                    phone=cleaned['phone'],
                    address=cleaned['address'],
                    legal_problem=cleaned['legal_problem'],
                    incident_description=cleaned['incident_description'],
                    applicant_user=request.user,
                    preferred_channel=cleaned['preferred_channel'],
                    safe_contact_number=cleaned.get('safe_contact_number', ''),
                    safe_contact_time=cleaned.get('safe_contact_time', ''),
                    language=cleaned.get('language', 'bn'),
                    nid_number=cleaned.get('nid_number', ''),
                    actor=request.user,
                    provenance=CaseEvent.PROVENANCE_APPLICANT_CONFIRMED
                )

                # Consume token and store last submitted ID
                if token:
                    consumed_tokens.append(token)
                    request.session['consumed_submission_tokens'] = consumed_tokens[-20:]
                request.session['last_submitted_app_id'] = application.application_id
                if 'pending_submission_token' in request.session:
                    del request.session['pending_submission_token']

                messages.success(
                    request,
                    f"Application submitted successfully! Your Application ID is: {application.application_id} / আবেদন সফলভাবে জমা হয়েছে! আবেদন নম্বর: {application.application_id}"
                )
                return redirect('cases:application_detail', application_id=application.application_id)
        else:
            messages.error(request, "Please correct the errors in the application form / অনুগ্রহ করে ফর্মের ত্রুটিগুলো সংশোধন করুন।")
    else:
        # Pre-fill phone or language if available from profile
        initial = {}
        if hasattr(request.user, 'profile'):
            if request.user.profile.phone:
                initial['phone'] = request.user.profile.phone
            if request.user.profile.language:
                initial['language'] = request.user.profile.language
        if request.user.get_full_name():
            initial['name'] = request.user.get_full_name()
            
        form = CitizenApplicationForm(initial=initial)
        new_token = uuid.uuid4().hex
        request.session['pending_submission_token'] = new_token

    token_for_render = request.session.get('pending_submission_token') or uuid.uuid4().hex
    return render(request, 'cases/application_form.html', {
        'form': form,
        'submission_token': token_for_render,
    })



@login_required
def application_detail(request, application_id):
    """
    Application tracking and detail page.
    Enforces citizen isolation: citizens can only view their own application.
    Allows citizens to upload case documents once accepted.
    """
    application = get_object_or_404(Application, application_id=application_id)

    # Server-side ownership / access check
    if not can_access_application(request.user, application):
        raise PermissionDenied("You do not have permission to access this application record.")

    case_record = getattr(application, 'case_record', None)

    # Citizen document upload or optional NID simulation
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'upload_document' and case_record:
            title = request.POST.get('title', '').strip()
            description = request.POST.get('description', '').strip()
            file_obj = request.FILES.get('file')

            if not title:
                messages.error(request, "Document title is required.")
            elif not file_obj:
                messages.error(request, "Please choose a file to upload.")
            else:
                try:
                    doc = upload_case_document(
                        case_record=case_record,
                        user=request.user,
                        title=title,
                        file_obj=file_obj,
                        description=description,
                    )
                    messages.success(request, f"Document '{doc.title}' uploaded successfully.")
                except (ValidationError, PermissionDenied) as e:
                    messages.error(request, str(e))

            return redirect('cases:application_detail', application_id=application_id)

        elif action == 'verify_nid':
            nid_input = request.POST.get('nid_number', '').strip()
            try:
                res = verify_application_nid(application, nid_input, request.user)
                if res['is_verified']:
                    messages.success(
                        request,
                        f"[ SIMULATED ] NID Verification Successful. Token: {res['verification_token']} (Masked: {res['nid_masked']}) / এনআইডি যাচাই সফল (সিমুলেটেড)"
                    )
                else:
                    messages.warning(
                        request,
                        f"[ SIMULATED ] NID Verification: {res['message_en']} / {res['message_bn']}"
                    )
            except PermissionDenied as e:
                messages.error(request, str(e))
            return redirect('cases:application_detail', application_id=application_id)

    events = CaseEvent.objects.filter(application=application).order_by('created_at')
    documents = case_record.documents.all().order_by('-created_at') if case_record else []

    return render(request, 'cases/application_detail.html', {
        'application': application,
        'events': events,
        'case_record': case_record,
        'documents': documents,
    })


@login_required
def udc_intake(request):
    """
    UDC Assisted Intake Workflow:
    Consent Confirmation -> Citizen Data -> Read-Back -> Submit -> End Access.
    Includes backend duplicate submission protection:
    - Submission token idempotency
    - Rapid retry guard
    - Recent duplicate detection
    Defined in docs/MASTER_PRD.md Section 4.5.
    """
    if not has_role(request.user, [UserProfile.ROLE_UDC_OPERATOR, UserProfile.ROLE_ADMIN]):
        raise PermissionDenied("Only authorized UDC Operators can access the assisted intake portal.")

    if request.method == 'POST':
        action = request.POST.get('action', 'review')
        submission_token = request.POST.get('submission_token', '').strip()

        if action == 'edit':
            form = UDCAssistedApplicationForm(request.POST)
            return render(request, 'cases/udc_intake.html', {
                'form': form,
                'submission_token': submission_token or uuid.uuid4().hex,
            })

        form = UDCAssistedApplicationForm(request.POST)

        if form.is_valid():
            cleaned = form.cleaned_data

            if action == 'review':
                token = submission_token or request.session.get('pending_udc_submission_token') or uuid.uuid4().hex
                request.session['pending_udc_submission_token'] = token
                return render(request, 'cases/udc_review.html', {
                    'form_data': cleaned,
                    'form': form,
                    'submission_token': token,
                })

            elif action == 'submit':
                # Enforce mandatory consent & read-back server-side
                if not cleaned.get('citizen_consent') or not cleaned.get('review_read_back'):
                    messages.error(request, "Consent and review read-back are mandatory.")
                    return render(request, 'cases/udc_intake.html', {'form': form})

                # Backend duplicate submission protection: Token Check
                token = submission_token or request.session.get('pending_udc_submission_token')
                consumed_tokens = request.session.get('consumed_submission_tokens', [])
                if token and token in consumed_tokens:
                    last_app_id = request.session.get('last_submitted_udc_app_id')
                    if last_app_id:
                        messages.info(
                            request,
                            f"Assisted application has already been submitted (Application ID: {last_app_id}). / এই আবেদনটি ইতিমধ্যে জমা দেওয়া হয়েছে।"
                        )
                        return redirect('cases:udc_confirmation', application_id=last_app_id)
                    else:
                        messages.warning(request, "Duplicate submission prevented. / ডুপ্লিকেট সাবমিশন রোধ করা হয়েছে।")
                        return redirect('dashboard:udc_operator')

                # Rapid duplicate check within 10 seconds
                recent_udc = Application.objects.filter(
                    preferred_channel=Application.CHANNEL_UDC,
                    name=cleaned['name'],
                    phone=cleaned['phone'],
                    legal_problem=cleaned['legal_problem'],
                    created_at__gte=timezone.now() - timezone.timedelta(seconds=10)
                ).first()
                if recent_udc:
                    messages.info(
                        request,
                        f"Assisted application already recorded (Application ID: {recent_udc.application_id}). / এই আবেদনটি ইতিমধ্যে জমা দেওয়া হয়েছে।"
                    )
                    return redirect('cases:udc_confirmation', application_id=recent_udc.application_id)

                # UDC operator facilitates, but does NOT become owner of citizen's case
                application = submit_application(
                    name=cleaned['name'],
                    phone=cleaned['phone'],
                    address=cleaned['address'],
                    legal_problem=cleaned['legal_problem'],
                    incident_description=cleaned['incident_description'],
                    applicant_user=None,  # Do not attach UDC operator as case owner
                    preferred_channel=Application.CHANNEL_UDC,
                    safe_contact_number=cleaned.get('safe_contact_number', ''),
                    safe_contact_time=cleaned.get('safe_contact_time', ''),
                    language=cleaned.get('language', 'bn'),
                    nid_number=cleaned.get('nid_number', ''),
                    actor=request.user,
                    provenance=CaseEvent.PROVENANCE_INTERMEDIARY_TRANSLATED
                )

                # Consume token and store last submitted ID
                if token:
                    consumed_tokens.append(token)
                    request.session['consumed_submission_tokens'] = consumed_tokens[-20:]
                request.session['last_submitted_udc_app_id'] = application.application_id
                if 'pending_udc_submission_token' in request.session:
                    del request.session['pending_udc_submission_token']

                messages.success(
                    request,
                    f"Assisted application submitted! Application ID: {application.application_id}"
                )
                # Terminate UDC operator access immediately post-submission
                return redirect('cases:udc_confirmation', application_id=application.application_id)
        else:
            messages.error(request, "Please correct the errors in the assisted intake form / অনুগ্রহ করে ফর্মের ত্রুটিগুলো সংশোধন করুন।")
    else:
        form = UDCAssistedApplicationForm()
        new_token = uuid.uuid4().hex
        request.session['pending_udc_submission_token'] = new_token

    token_for_render = request.session.get('pending_udc_submission_token') or uuid.uuid4().hex
    return render(request, 'cases/udc_intake.html', {
        'form': form,
        'submission_token': token_for_render,
    })



@login_required
def udc_confirmation(request, application_id):
    """
    Confirmation screen post-submission for UDC operator.
    Confirms Application ID and clearly indicates that UDC operator session access has ended.
    """
    if not has_role(request.user, [UserProfile.ROLE_UDC_OPERATOR, UserProfile.ROLE_ADMIN]):
        raise PermissionDenied("Access restricted.")

    application = get_object_or_404(Application, application_id=application_id)

    return render(request, 'cases/udc_confirmation.html', {
        'application_id': application.application_id,
        'applicant_name': application.name,
        'created_at': application.created_at,
    })


# -----------------------------------------------------------------------------
# DLAO Officer Application & Case Workflows (Phase 4)
# -----------------------------------------------------------------------------

@login_required
def officer_application_detail(request, application_id):
    """
    DLAO officer review page for a submitted application.
    Allows Accept (creates CaseRecord with Case ID) or Reject (with reason).
    """
    if not has_role(request.user, [UserProfile.ROLE_DLAO_OFFICER, UserProfile.ROLE_ADMIN]):
        raise PermissionDenied("Only authorized DLAO officers can access this application review.")

    application = get_object_or_404(Application, application_id=application_id)
    events = CaseEvent.objects.filter(application=application).order_by('created_at')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'accept':
            priority = request.POST.get('priority', CaseRecord.PRIORITY_MEDIUM)
            try:
                case_record = accept_application(application, request.user, priority=priority)
                messages.success(
                    request,
                    f"Application {application.application_id} accepted successfully! Case ID {case_record.case_id} generated."
                )
                return redirect('cases:officer_case_detail', case_id=case_record.case_id)
            except (ValidationError, PermissionDenied) as e:
                messages.error(request, str(e))

        elif action == 'reject':
            reason = request.POST.get('reason', '').strip()
            if not reason:
                messages.error(request, "Please provide a reason for rejecting the application / প্রত্যাখ্যানের কারণ উল্লেখ করা আবশ্যক।")
            else:
                try:
                    reject_application(application, request.user, reason=reason)
                    messages.info(request, f"Application {application.application_id} has been marked as rejected.")
                    return redirect('dashboard:officer')
                except (ValidationError, PermissionDenied) as e:
                    messages.error(request, str(e))

        elif action == 'verify_nid':
            nid_input = request.POST.get('nid_number', '').strip()
            try:
                res = verify_application_nid(application, nid_input, request.user)
                if res['is_verified']:
                    messages.success(
                        request,
                        f"[ SIMULATED ] NID Verification Successful. Token: {res['verification_token']} (Masked: {res['nid_masked']}) / এনআইডি যাচাই সফল (সিমুলেটেড)"
                    )
                else:
                    messages.warning(
                        request,
                        f"[ SIMULATED ] NID Verification Failed: {res['message_en']} / {res['message_bn']}"
                    )
            except PermissionDenied as e:
                messages.error(request, str(e))
            return redirect('cases:officer_application_detail', application_id=application_id)

    return render(request, 'cases/officer_application_detail.html', {
        'application': application,
        'events': events,
        'priority_choices': CaseRecord.PRIORITY_CHOICES,
    })


@login_required
def officer_case_detail(request, case_id):
    """
    Comprehensive DLAO Officer Case Workspace.
    Displays:
    - Application info
    - Case ID
    - Safe contact details
    - Status & Priority
    - Assigned lawyer
    - Referrals, Mediation, Tasks, and CaseEvent audit stream
    Allows:
    - Change Priority
    - Assign Panel Lawyer
    - Refer Case
    - Initiate Mediation
    - Transition Status / Close Case
    """
    if not has_role(request.user, [UserProfile.ROLE_DLAO_OFFICER, UserProfile.ROLE_DLAO_SUPPORT_STAFF, UserProfile.ROLE_ADMIN]):
        raise PermissionDenied("Only authorized DLAO officers or support staff can access this case.")

    case_record = get_object_or_404(CaseRecord, case_id=case_id)
    events = CaseEvent.objects.filter(case=case_record).order_by('created_at')
    panel_lawyers = User.objects.filter(profile__role=UserProfile.ROLE_PANEL_LAWYER)
    mediators = User.objects.filter(profile__role=UserProfile.ROLE_MEDIATOR)

    if request.method == 'POST':
        if not has_role(request.user, [UserProfile.ROLE_DLAO_OFFICER, UserProfile.ROLE_ADMIN]):
            raise PermissionDenied("Only DLAO officers can perform consequential actions on cases.")

        action = request.POST.get('action')

        if action == 'change_priority':
            new_priority = request.POST.get('priority')
            try:
                change_case_priority(case_record, request.user, new_priority)
                messages.success(request, f"Case priority changed to {new_priority}.")
            except (ValidationError, PermissionDenied) as e:
                messages.error(request, str(e))

        elif action == 'assign_lawyer':
            lawyer_id = request.POST.get('lawyer_id')
            lawyer = get_object_or_404(User, id=lawyer_id)
            try:
                assign_lawyer_to_case(case_record, request.user, lawyer)
                messages.success(request, f"Panel lawyer {lawyer.username} assigned to case.")
            except (ValidationError, PermissionDenied) as e:
                messages.error(request, str(e))

        elif action == 'create_referral':
            destination = request.POST.get('destination', '').strip()
            reason = request.POST.get('reason', '').strip()
            expected_action = request.POST.get('expected_action', '').strip()
            deadline = request.POST.get('deadline')
            if not (destination and reason and expected_action and deadline):
                messages.error(request, "All referral fields (destination, reason, expected action, deadline) are required.")
            else:
                try:
                    create_case_referral(case_record, request.user, destination, reason, expected_action, deadline)
                    messages.success(request, f"Case referred to {destination}.")
                except (ValidationError, PermissionDenied) as e:
                    messages.error(request, str(e))

        elif action == 'initiate_mediation':
            mediator_id = request.POST.get('mediator_id')
            mediator = get_object_or_404(User, id=mediator_id)
            mode = request.POST.get('mode', 'in_person')
            scheduled_at = request.POST.get('scheduled_at') or None
            try:
                initiate_case_mediation(case_record, request.user, mediator, mode=mode, scheduled_at=scheduled_at)
                messages.success(request, f"Mediation initiated with mediator {mediator.username}.")
            except (ValidationError, PermissionDenied) as e:
                messages.error(request, str(e))

        elif action == 'transition_status':
            new_status = request.POST.get('status')
            reason = request.POST.get('reason', '').strip()
            try:
                transition_case_status(case_record, request.user, new_status, reason=reason)
                messages.success(request, f"Case status updated to {new_status}.")
            except (ValidationError, PermissionDenied) as e:
                messages.error(request, str(e))

        elif action == 'upload_document':
            title = request.POST.get('title', '').strip()
            description = request.POST.get('description', '').strip()
            file_obj = request.FILES.get('file')
            try:
                doc = upload_case_document(case_record, request.user, title, file_obj, description)
                messages.success(request, f"Document '{doc.title}' uploaded successfully.")
            except (ValidationError, PermissionDenied) as e:
                messages.error(request, str(e))

        elif action == 'verify_document':
            document_id = request.POST.get('document_id')
            new_status = request.POST.get('status')
            notes = request.POST.get('notes', '').strip()
            doc = get_object_or_404(Document, id=document_id, case=case_record)
            try:
                verify_case_document(doc, request.user, new_status, notes)
                messages.success(request, f"Document '{doc.title}' status updated to {doc.get_status_display()}.")
            except (ValidationError, PermissionDenied) as e:
                messages.error(request, str(e))

        elif action == 'link_related_case':
            target_case_id = request.POST.get('target_case_id', '').strip()
            relationship_type = request.POST.get('relationship_type', '').strip()
            target_case = CaseRecord.objects.filter(case_id=target_case_id).first()
            if not target_case:
                messages.error(request, f"Target Case ID '{target_case_id}' does not exist.")
            else:
                try:
                    link_related_cases(case_record, target_case, request.user, relationship_type)
                    messages.success(
                        request,
                        f"Case {case_record.case_id} successfully linked to {target_case.case_id}. "
                        "NOTE: Related cases remain distinct and are not merged."
                    )
                except (ValidationError, PermissionDenied) as e:
                    messages.error(request, str(e))

        elif action == 'review_duplicate':
            candidate_id = request.POST.get('candidate_id')
            decision = request.POST.get('decision')
            notes = request.POST.get('notes', '').strip()
            candidate = get_object_or_404(DuplicateCandidate, id=candidate_id, case=case_record)
            try:
                review_duplicate_candidate(candidate, request.user, decision, notes)
                messages.success(request, f"Duplicate suggestion review saved: {candidate.get_review_status_display()}.")
            except (ValidationError, PermissionDenied) as e:
                messages.error(request, str(e))

        elif action == 'scan_duplicates':
            candidates = MockAIService.run_duplicate_scan(case_record)
            messages.info(request, f"Duplicate scan completed. {len(candidates)} suggestion(s) ready for human evaluation.")

        elif action == 'send_safe_sms':
            message_text = request.POST.get('message', '').strip()
            if not message_text:
                messages.error(request, "SMS message content cannot be blank / বার্তা খালি হতে পারে না।")
            else:
                res = MockSMSService.send_sms(case_record, message_text, actor=request.user)
                safe_tag = " [SAFE CONTACT USED / নিরাপদ বিকল্প নম্বর]" if res['safe_contact_used'] else ""
                messages.success(
                    request,
                    f"[ SIMULATED ] SMS dispatched to {res['recipient']}{safe_tag}. Status: {res['status_en']} / {res['status_bn']}"
                )

        elif action == 'simulate_ivr_call':
            script_summary = request.POST.get('script_summary', 'Proceeding & Hearing Notification').strip()
            res = MockIVRService.initiate_call(case_record, script_summary, actor=request.user)
            safe_tag = " [SAFE CONTACT USED]" if res['safe_contact_used'] else ""
            messages.success(
                request,
                f"[ SIMULATED ] Automated voice call completed to {res['recipient']}{safe_tag}. Permitted hours: {res['permitted_hours']}"
            )

        elif action == 'verify_nid':
            nid_input = request.POST.get('nid_number', '').strip()
            res = verify_application_nid(case_record.application, nid_input, request.user)
            if res['is_verified']:
                messages.success(
                    request,
                    f"[ SIMULATED ] NID Verification Successful. Token: {res['verification_token']} (Masked: {res['nid_masked']}) / এনআইডি যাচাই সফল (সিমুলেটেড)"
                )
            else:
                messages.warning(
                    request,
                    f"[ SIMULATED ] NID Verification Failed: {res['message_en']} / {res['message_bn']}"
                )

        elif action == 'simulate_payment':
            amount = request.POST.get('amount', '1500')
            lawyer_user = case_record.assigned_lawyer or request.user
            purpose = request.POST.get('purpose', 'Panel Lawyer Representation Honorarium')
            res = MockPaymentService.process_legal_aid_disbursement(case_record, amount, lawyer_user, purpose=purpose)
            messages.success(
                request,
                f"[ SIMULATED PAYMENT ] Disbursement voucher {res['transaction_id']} generated for {res['recipient']} ({res['amount_bdt']:.2f} BDT). "
                "No real financial transaction occurred / কোনো প্রকৃত আর্থিক লেনদেন হয়নি।"
            )

        elif action == 'simulate_fee_waiver':
            res = MockPaymentService.check_fee_exemption(case_record.application)
            messages.info(
                request,
                f"[ SIMULATED PAYMENT ] Court Fee Status: {res['exemption_status']} pursuant to {res['statutory_reference']}."
            )

        return redirect('cases:officer_case_detail', case_id=case_id)

    # Simulated Assistive AI calculations (Strictly assistive)
    ai_categorization = MockAIService.categorize_case(case_record.application.incident_description)
    ai_missing_info = MockAIService.detect_missing_information(case_record.application)
    ai_inconsistencies = MockAIService.flag_inconsistencies(case_record.application)
    ai_draft_summary = MockAIService.generate_draft(case_record, 'case_summary')

    return render(request, 'cases/officer_case_detail.html', {
        'case_record': case_record,
        'application': case_record.application,
        'events': events,
        'panel_lawyers': panel_lawyers,
        'mediators': mediators,
        'referrals': case_record.referrals.all(),
        'tasks': case_record.tasks.all(),
        'mediation': getattr(case_record, 'mediation', None),
        'documents': case_record.documents.all().order_by('-created_at'),
        'signatures': case_record.signatures.all().order_by('-signed_at'),
        'communications': case_record.communications.all().order_by('-created_at'),
        'document_status_choices': Document.STATUS_CHOICES,
        'related_cases': case_record.related_cases.all().select_related('related_case'),
        'reverse_related_cases': case_record.reverse_related_cases.all().select_related('case'),
        'duplicate_candidates': case_record.duplicate_candidates.all().select_related('possible_case'),
        'other_cases': CaseRecord.objects.exclude(id=case_record.id).order_by('-created_at')[:25],
        'priority_choices': CaseRecord.PRIORITY_CHOICES,
        'status_choices': CaseRecord.STATUS_CHOICES,
        'ai_categorization': ai_categorization,
        'ai_missing_info': ai_missing_info,
        'ai_inconsistencies': ai_inconsistencies,
        'ai_draft_summary': ai_draft_summary,
        'ai_label_en': MockAIService.LABEL_EN,
        'ai_label_bn': MockAIService.LABEL_BN,
        'ai_disclaimer_en': MockAIService.DISCLAIMER_EN,
        'ai_disclaimer_bn': MockAIService.DISCLAIMER_BN,
        'mock_sms_label_en': MockSMSService.LABEL_EN,
        'mock_sms_label_bn': MockSMSService.LABEL_BN,
        'mock_ivr_label_en': MockIVRService.LABEL_EN,
        'mock_ivr_label_bn': MockIVRService.LABEL_BN,
        'mock_nid_label_en': MockNIDService.LABEL_EN,
        'mock_nid_label_bn': MockNIDService.LABEL_BN,
        'mock_payment_label_en': MockPaymentService.LABEL_EN,
        'mock_payment_label_bn': MockPaymentService.LABEL_BN,
        'mock_sig_label_en': MockSignatureService.LABEL_EN,
        'mock_sig_label_bn': MockSignatureService.LABEL_BN,
    })

