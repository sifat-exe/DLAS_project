import time
import uuid
from django.utils import timezone
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse, HttpResponseForbidden
from django.urls import reverse
from cases.conversational_service import BanglaConversationalService
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
import json
from accounts.models import UserProfile
from accounts.permissions import has_role, can_access_application, can_access_case, get_user_role
from cases.models import Application, CaseRecord, CaseEvent, Task, RelatedCase, DuplicateCandidate, Communication
from documents.models import Document, Signature
from cases.forms import CitizenApplicationForm, UDCAssistedApplicationForm, MarmaAssistedIntakeForm
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
    submit_marma_intake,
    process_offline_sync,
    resolve_offline_conflict,
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

        elif action == 'preview_referral':
            destination = request.POST.get('destination', '').strip()
            reason = request.POST.get('reason', '').strip()
            expected_action = request.POST.get('expected_action', '').strip()
            deadline = request.POST.get('deadline')
            assigned_officer_id = request.POST.get('assigned_officer_id')
            package_notes = request.POST.get('package_notes', '').strip()
            doc_ids = request.POST.getlist('document_ids')

            if not (destination and reason and expected_action and deadline):
                messages.error(request, "All required referral fields (destination, reason, expected action, deadline) must be completed before preview.")
            else:
                assigned_officer = None
                if assigned_officer_id:
                    assigned_officer = User.objects.filter(id=assigned_officer_id).first()
                if not assigned_officer:
                    assigned_officer = User.objects.filter(
                        profile__role=UserProfile.ROLE_DLAO_OFFICER,
                        is_active=True
                    ).exclude(id=request.user.id).order_by('id').first() or request.user

                selected_docs = Document.objects.filter(case=case_record, id__in=doc_ids) if doc_ids else []
                return render(request, 'referrals/package_review.html', {
                    'case_record': case_record,
                    'destination': destination,
                    'reason': reason,
                    'expected_action': expected_action,
                    'deadline': deadline,
                    'assigned_officer': assigned_officer,
                    'package_notes': package_notes,
                    'included_document_ids': ",".join(str(d) for d in doc_ids),
                    'documents': selected_docs,
                })

        elif action in ['create_referral', 'confirm_referral']:
            destination = request.POST.get('destination', '').strip()
            reason = request.POST.get('reason', '').strip()
            expected_action = request.POST.get('expected_action', '').strip()
            deadline = request.POST.get('deadline')
            assigned_officer_id = request.POST.get('assigned_officer_id')
            package_notes = request.POST.get('package_notes', '').strip()
            doc_ids_str = request.POST.get('included_document_ids', '')
            if not doc_ids_str and request.POST.getlist('document_ids'):
                doc_ids_str = ",".join(request.POST.getlist('document_ids'))

            if not (destination and reason and expected_action and deadline):
                messages.error(request, "All referral fields (destination, reason, expected action, deadline) are required.")
            else:
                try:
                    assigned_officer = None
                    if assigned_officer_id:
                        assigned_officer = User.objects.filter(id=assigned_officer_id).first()

                    create_case_referral(
                        case_record=case_record,
                        officer=request.user,
                        destination=destination,
                        reason=reason,
                        expected_action=expected_action,
                        deadline=deadline,
                        assigned_officer=assigned_officer,
                        package_notes=package_notes,
                        included_document_ids=doc_ids_str,
                    )
                    messages.success(request, f"Referral package for {destination} created and transmitted successfully.")
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
        'dlao_officers': User.objects.filter(profile__role=UserProfile.ROLE_DLAO_OFFICER, is_active=True),
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


# =============================================================================
# BATCH 2 — FEATURE 1: BANGLA CONVERSATIONAL INTAKE
# =============================================================================

def conversational_intake_view(request):
    """
    Conversational Intake View (Batch 2 Part A):
    Provides a multi-turn Bangla conversational assistant for legal aid intake.
    Maintains server-side conversation state in request.session.
    Collects: name, phone, address, legal_problem, incident_description, safe contact.
    Shows review card before explicit confirmation.
    """
    state = request.session.get('conversational_intake')
    if not state or not isinstance(state, dict) or 'slots' not in state:
        state = BanglaConversationalService.init_session(user=request.user)
        request.session['conversational_intake'] = state
        request.session.modified = True

    return render(request, 'cases/conversational_intake.html', {
        'state': state,
        'slots': state['slots'],
        'history': state['history'],
        'status': state['status'],
        'current_slot': state.get('current_slot'),
        'ai_label_en': BanglaConversationalService.LABEL_EN,
        'ai_label_bn': BanglaConversationalService.LABEL_BN,
        'ai_disclaimer_en': BanglaConversationalService.DISCLAIMER_EN,
        'ai_disclaimer_bn': BanglaConversationalService.DISCLAIMER_BN,
    })


def conversational_intake_message(request):
    """
    Handles single-turn message submission for the conversational intake.
    Extracts slots, processes corrections, detects missing info, and updates session state.
    """
    if request.method != 'POST':
        return redirect('cases:conversational_intake')

    state = request.session.get('conversational_intake')
    if not state or not isinstance(state, dict) or 'slots' not in state:
        state = BanglaConversationalService.init_session(user=request.user)

    user_text = request.POST.get('message', '').strip()
    if not user_text and request.body:
        import json
        try:
            body_data = json.loads(request.body.decode('utf-8'))
            user_text = body_data.get('message', '').strip()
        except Exception:
            pass

    state, reply_bn, reply_en = BanglaConversationalService.process_turn(state, user_text)
    request.session['conversational_intake'] = state
    request.session.modified = True

    # If AJAX/JSON request
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or 'application/json' in request.headers.get('Accept', ''):
        return JsonResponse({
            'status': state['status'],
            'reply_bn': reply_bn,
            'reply_en': reply_en,
            'current_slot': state.get('current_slot'),
            'slots': state['slots'],
            'history': state['history'],
        })

    return redirect('cases:conversational_intake')


def conversational_intake_confirm(request):
    """
    Explicit applicant confirmation for conversational intake.
    Submits application, generates Application ID, logs CaseEvent with applicant_confirmed provenance.
    Strictly forbids creating Case ID.
    """
    if request.method != 'POST':
        return redirect('cases:conversational_intake')

    state = request.session.get('conversational_intake')
    if not state or state.get('status') != 'review':
        messages.error(request, "আবেদন নিশ্চিত করার পূর্বে সকল তথ্য সংগ্রহ সম্পন্ন হতে হবে। / All intake fields must be collected before confirmation.")
        return redirect('cases:conversational_intake')

    try:
        app = BanglaConversationalService.confirm_and_submit(
            state,
            user=request.user if request.user.is_authenticated else None,
            channel=Application.CHANNEL_WEB
        )
        request.session['conversational_intake'] = None
        request.session.modified = True

        messages.success(
            request,
            f"আপনার আবেদন সফলভাবে দাখিল হয়েছে! অ্যাপ্লিকেশন আইডি: {app.application_id} (ডিএলএও পর্যালোচনার অপেক্ষায়)"
        )
        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or 'application/json' in request.headers.get('Accept', ''):
            return JsonResponse({
                'status': 'confirmed',
                'application_id': app.application_id,
                'redirect_url': reverse('cases:application_detail', kwargs={'application_id': app.application_id}),
            })
        return redirect('cases:application_detail', application_id=app.application_id)
    except Exception as e:
        messages.error(request, f"দাখিলে ত্রুটি: {str(e)}")
        return redirect('cases:conversational_intake')


def conversational_intake_reset(request):
    """
    Resets the conversational intake session state.
    """
    request.session['conversational_intake'] = None
    request.session.modified = True
    messages.info(request, "কথোপকথন রিসেট করা হয়েছে। নতুন করে শুরু করুন। / Conversation has been reset.")
    return redirect('cases:conversational_intake')


# =============================================================================
# BATCH 2 — FEATURE 2: MOYURI'S OWN CONFIRMATION
# =============================================================================

@login_required
def moyuri_confirmation_view(request):
    """
    Moyuri's Own Confirmation (Batch 2 Part B):
    Allows applicant Moyuri to review her collected intake information, make corrections,
    and explicitly confirm submission.
    Creates CaseEvent with action='moyuri_confirmed', actor=Moyuri, provenance='applicant_confirmed'.
    Guarded with strict server-side authorization / IDOR protection.
    """
    # Authorization & IDOR protection: only Moyuri (or citizen owner) can confirm
    is_moyuri = (
        request.user.username in ['moyuri', 'demo_moyuri'] or
        request.user.first_name.strip().lower() == 'moyuri' or
        (hasattr(request.user, 'profile') and request.user.profile.role == UserProfile.ROLE_CITIZEN and 'moyuri' in request.user.username.lower())
    )
    if not is_moyuri and not request.user.is_superuser:
        raise PermissionDenied("403 Forbidden: You are not authorized to access Moyuri's confirmation workspace.")

    # Load or initialize Moyuri's assisted intake data
    intake_data = request.session.get('moyuri_intake_data') or {
        'name': 'ময়ূরী আক্তার',
        'phone': '01755123456',
        'address': 'গ্রাম: রূপনগর, থানা: সাভার, জেলা: ঢাকা',
        'legal_problem': 'জমি বেদখল ও সীমানা বিরোধ সংক্রান্ত আইনি প্রতিকার',
        'incident_description': 'পৈতৃক বসতভিটার জমি প্রতিপক্ষ জোরপূর্বক দখল ও সীমানা প্রাচীর ভেঙে ফেলার হুমকি দিচ্ছে। স্থানীয় গণ্যমান্য ব্যক্তিদের মাধ্যমে সমাধানের চেষ্টা ব্যর্থ হয়েছে।',
        'safe_contact_number': '01811223344',
        'safe_contact_time': 'সকাল ১০টা - দুপুর ১টা',
        'nid_number': '19922615500000000',
    }

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'edit':
            intake_data['name'] = request.POST.get('name', intake_data['name']).strip()
            intake_data['phone'] = request.POST.get('phone', intake_data['phone']).strip()
            intake_data['address'] = request.POST.get('address', intake_data['address']).strip()
            intake_data['legal_problem'] = request.POST.get('legal_problem', intake_data['legal_problem']).strip()
            intake_data['incident_description'] = request.POST.get('incident_description', intake_data['incident_description']).strip()
            intake_data['safe_contact_number'] = request.POST.get('safe_contact_number', intake_data['safe_contact_number']).strip()
            intake_data['safe_contact_time'] = request.POST.get('safe_contact_time', intake_data['safe_contact_time']).strip()
            request.session['moyuri_intake_data'] = intake_data
            request.session.modified = True
            messages.success(request, "তথ্য সফলভাবে হালনাগাদ করা হয়েছে। অনুগ্রহ করে পর্যালোচনা করে নিশ্চিত করুন। / Information updated.")
            return redirect('cases:moyuri_confirm')

        elif action == 'confirm':
            # Explicit Confirmation by Moyuri
            app = submit_application(
                name=intake_data['name'],
                phone=intake_data['phone'],
                address=intake_data['address'],
                legal_problem=intake_data['legal_problem'],
                incident_description=intake_data['incident_description'],
                applicant_user=request.user,
                preferred_channel=Application.CHANNEL_WEB,
                safe_contact_number=intake_data['safe_contact_number'],
                safe_contact_time=intake_data['safe_contact_time'],
                language='bn',
                nid_number=intake_data.get('nid_number', ''),
                actor=request.user,
                provenance=CaseEvent.PROVENANCE_APPLICANT_CONFIRMED,
            )

            # Record exact CaseEvent: moyuri_confirmed
            CaseEvent.objects.create(
                application=app,
                case=None,
                actor=request.user,
                actor_role='citizen',
                channel='web',
                action='moyuri_confirmed',
                description="Applicant Moyuri personally reviewed, verified, and explicitly confirmed her assisted intake information.",
                provenance=CaseEvent.PROVENANCE_APPLICANT_CONFIRMED,
                authority='applicant_personal_confirmation',
            )

            request.session['moyuri_intake_data'] = None
            request.session.modified = True
            messages.success(
                request,
                f"ধন্যবাদ ময়ূরী! আপনার আবেদন নিশ্চিত ও সফলভাবে দাখিল হয়েছে। অ্যাপ্লিকেশন আইডি: {app.application_id}।"
            )
            return redirect('cases:application_detail', application_id=app.application_id)

    return render(request, 'cases/moyuri_confirm.html', {
        'intake_data': intake_data,
        'user': request.user,
    })


# =============================================================================
# BATCH 2 — FEATURE 3: RIPON VOICE-ONLY TASK
# =============================================================================

@login_required
def voice_task_view(request, task_id):
    """
    Ripon Voice-Only Task Workspace (Batch 2 Part C):
    Allows Ripon (or assigned staff) to complete an assigned task via simulated voice commands.
    IDOR protected: verifies logged-in user is strictly task.assigned_to.
    """
    task = get_object_or_404(Task, id=task_id)
    if task.assigned_to != request.user and not request.user.is_superuser:
        raise PermissionDenied("403 Forbidden: You are not authorized to view or execute this task.")

    return render(request, 'cases/voice_task.html', {
        'task': task,
        'case_record': task.case,
        'mock_ivr_label_en': MockIVRService.LABEL_EN,
        'mock_ivr_label_bn': MockIVRService.LABEL_BN,
    })


@login_required
def voice_task_execute(request, task_id):
    """
    Voice command execution endpoint.
    Validates controlled voice vocabulary, enforces server-side ownership,
    transitions task state to COMPLETED, and records append-only CaseEvent voice_task_completed.
    """
    if request.method != 'POST':
        return redirect('cases:voice_task', task_id=task_id)

    task = get_object_or_404(Task, id=task_id)
    if task.assigned_to != request.user and not request.user.is_superuser:
        raise PermissionDenied("403 Forbidden: IDOR violation. Cannot execute another user's task.")

    if task.status == Task.STATUS_COMPLETED:
        messages.warning(request, "এই কাজটি ইতোমধ্যে সম্পন্ন হিসেবে চিহ্নিত হয়েছে। / This task is already completed.")
        return redirect('cases:voice_task', task_id=task.id)

    command = request.POST.get('command', '').strip()
    if not command and request.body:
        import json
        try:
            bdata = json.loads(request.body.decode('utf-8'))
            command = bdata.get('command', '').strip()
        except Exception:
            pass

    # Normalize command
    norm_cmd = command.lower()
    valid_phrases = [
        'হ্যাঁ, গ্রহণ করছি', 'হ্যাঁ', 'গ্রহণ করছি', 'কাজটি সম্পন্ন করুন', 'সম্পন্ন করুন', 'সম্পন্ন',
        'yes', 'yes, accept', 'accept', 'complete', 'complete task', 'confirm',
    ]

    is_valid = any(p in norm_cmd for p in valid_phrases)

    if not is_valid:
        error_bn = "অস্বীকৃত ভয়েস কমান্ড। অনুগ্রহ করে 'হ্যাঁ, গ্রহণ করছি' অথবা 'সম্পন্ন করুন' বলুন।"
        error_en = "Unrecognized voice command. Please say 'Yes, I accept' or 'Complete task'."
        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or 'application/json' in request.headers.get('Accept', ''):
            return JsonResponse({'success': False, 'message_bn': error_bn, 'message_en': error_en}, status=400)
        messages.error(request, f"{error_bn} / {error_en}")
        return redirect('cases:voice_task', task_id=task.id)

    # Valid command: transition task to COMPLETED
    task.status = Task.STATUS_COMPLETED
    task.completed_at = timezone.now()
    task.save(update_fields=['status', 'completed_at'])

    # If referral related task, update referral state if pending
    case = task.case
    referral = case.referrals.filter(status='pending').first()
    if referral:
        referral.status = 'acknowledged'
        referral.acknowledged_at = timezone.now()
        referral.save(update_fields=['status', 'acknowledged_at'])

    # Log append-only CaseEvent: voice_task_completed
    actor_role = getattr(request.user, 'profile', None).role if hasattr(request.user, 'profile') else 'support_staff'
    CaseEvent.objects.create(
        case=task.case,
        application=task.case.application,
        actor=request.user,
        actor_role=actor_role,
        channel='voice',
        action='voice_task_completed',
        description=f"Assigned user {request.user.username} successfully completed task '{task.title}' via voice interaction.",
        provenance=CaseEvent.PROVENANCE_STAFF_ENTERED,
        authority='assigned_user_voice_command',
    )

    success_bn = f"কাজ '{task.title}' সফলভাবে সম্পন্ন হয়েছে এবং অডিট সিস্টেমে সংরক্ষিত হয়েছে।"
    success_en = f"Task '{task.title}' has been successfully completed and recorded in CaseEvent audit."

    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or 'application/json' in request.headers.get('Accept', ''):
        return JsonResponse({
            'success': True,
            'message_bn': success_bn,
            'message_en': success_en,
            'task_id': task.id,
            'status': task.status,
            'completed_at': task.completed_at.strftime('%Y-%m-%d %H:%M'),
        })

    messages.success(request, f"{success_bn} / {success_en}")
    return redirect('cases:voice_task', task_id=task.id)


@login_required
def ripon_voice_task_demo(request):
    """
    Direct demo jump to Ripon's assigned voice task.
    Ensures an assigned task exists for Ripon and redirects to the voice task workspace.
    """
    task = Task.objects.filter(assigned_to=request.user, status=Task.STATUS_PENDING).first()
    if not task:
        case = CaseRecord.objects.first()
        if not case:
            from cases.models import Application
            app = Application.objects.first()
            if not app:
                app = submit_application(
                    name="আব্দুল করিম",
                    phone="01711223344",
                    address="মিরপুর, ঢাকা",
                    legal_problem="ভাড়াটিয়া উচ্ছেদ সংক্রান্ত",
                    incident_description="বাড়িওয়ালা কোনো লিখিত নোটিশ ছাড়াই উচ্ছেদের হুমকি দিচ্ছে।",
                    preferred_channel=Application.CHANNEL_WEB,
                )
            case = accept_application(app, assigned_officer=request.user, priority='HIGH')

        task = Task.objects.create(
            case=case,
            assigned_to=request.user,
            title="Referral Acknowledgement: REF-16699",
            description="Verify and acknowledge incoming referral from National Legal Aid Helpline 16699",
            status=Task.STATUS_PENDING,
            due_at=timezone.now() + timezone.timedelta(days=2),
            priority='HIGH',
        )

    return redirect('cases:voice_task', task_id=task.id)


# =============================================================================
# BATCH 3 — PART A: MARMA PROVENANCE WORKFLOW
# =============================================================================

@login_required
def marma_intake_view(request):
    """
    Marma Indigenous Language Assisted Intake (Batch 3 Part A).
    Preserves:
      MARMA SAID -> TRANSLATED -> TYPED -> APPLICATION DATA
    Workflow:
      1. Entry of applicant data, Marma oral statement, intermediary translation, typed data.
      2. Verification & Confirmation Step: confirms translation represents what was said,
         and typed data represents translation.
      3. Corrections allowed before submission.
      4. Submits via submit_marma_intake service:
         - Generates Application ID
         - No Case ID before DLAO acceptance
         - Logs marma_statement_recorded, marma_translation_recorded, marma_typed_confirmation, APPLICATION_SUBMITTED.
    """
    if request.method == 'POST':
        action = request.POST.get('action', 'confirm')
        form = MarmaAssistedIntakeForm(request.POST)
        
        if action == 'edit':
            return render(request, 'cases/marma_intake.html', {'form': form})
            
        if form.is_valid():
            cleaned = form.cleaned_data
            
            if action == 'confirm':
                # Show explicit verification preview step
                return render(request, 'cases/marma_review.html', {
                    'form': form,
                    'data': cleaned,
                })
                
            elif action == 'submit':
                # Final submission using existing Application submission architecture
                app = submit_marma_intake(
                    name=cleaned['name'],
                    phone=cleaned['phone'],
                    address=cleaned['address'],
                    original_statement=cleaned['original_statement'],
                    translated_statement=cleaned['translated_statement'],
                    typed_legal_problem=cleaned['legal_problem'],
                    typed_incident_description=cleaned['incident_description'],
                    statement_language=cleaned.get('statement_language', 'marma'),
                    actor=request.user,
                    applicant_user=None,  # Assisted intake
                    preferred_channel=Application.CHANNEL_UDC,
                    safe_contact_number=cleaned.get('safe_contact_number', ''),
                    safe_contact_time=cleaned.get('safe_contact_time', ''),
                    nid_number=cleaned.get('nid_number', '')
                )
                messages.success(
                    request,
                    f"Marma assisted application submitted successfully! Application ID: {app.application_id} / মারমা ভাষায় সহায়তাকৃত আবেদন সফলভাবে গৃহীত হয়েছে! আবেদন নম্বর: {app.application_id}"
                )
                return redirect('cases:application_detail', application_id=app.application_id)
        else:
            messages.error(request, "Please correct the errors in the Marma intake form / অনুগ্রহ করে ফর্মের ত্রুটিগুলো সংশোধন করুন।")
    else:
        # Pre-fill sample Marma case for instant hackathon demonstration
        initial = {
            'name': 'মং শোয়ে প্রু মারমা',
            'phone': '01844000999',
            'address': 'রোয়াংছড়ি মৌজা, রোয়াংছড়ি, বান্দরবান',
            'statement_language': 'marma',
            'original_statement': 'အကျွန်မြေယာ ပြဿနာ ကြုံနေရပါတယ် (আমি আমার জমি নিয়ে সমস্যায় পড়েছি — পৈতৃক কৃষিজমি জবরদখল)',
            'translated_statement': 'আমার পৈতৃক কৃষিজমি প্রভাবশালী প্রতিপক্ষরা জোরপূর্বক দখল করে নিয়েছে এবং সীমানা পিলার ভেঙে ফেলেছে।',
            'legal_problem': 'জমি জবরদখল ও সীমানা বিরোধ (Land Encroachment & Boundary Dispute)',
            'incident_description': 'বান্দরবান রোয়াংছড়ি মৌজায় পৈতৃক রেকর্ডভুক্ত ২ একর কৃষিজমি গত ১৫ সেপ্টেম্বর স্থানীয় প্রতিপক্ষরা জোরপূর্বক দখল করে নিয়েছে। আইনি প্রতিকার ও সীমানা পুনর্নির্ধারণ প্রার্থনা।',
        }
        form = MarmaAssistedIntakeForm(initial=initial)
        
    return render(request, 'cases/marma_intake.html', {'form': form})


# =============================================================================
# BATCH 3 — PART B, C, D: TRUE OFFLINE QUEUE, SYNC & CONFLICT RESOLUTION
# =============================================================================

@login_required
def offline_queue_view(request):
    """
    True Offline Queue & Sync Demonstration Workspace (Batch 3 Parts B, C, D).
    Runs hackathon-grade local persistence via browser localStorage:
    - Simulated Offline / Online Mode Toggle
    - Offline queueing with temporary local ID (OFFLINE-TMP-...)
    - Zero server-side Application created while offline
    - Survives browser page reload
    - Server synchronization on reconnection via existing Application submission service
    - Idempotency & duplicate protection
    - Human-controlled conflict resolution (NO silent overwrites)
    """
    return render(request, 'cases/offline_queue.html', {
        'user_role': get_user_role(request.user) or 'citizen',
    })


@login_required
def offline_sync_api(request):
    """
    Server-side Synchronization Endpoint for Offline Queue (POST).
    Receives locally queued item data.
    Validates idempotency, field validity, and conflicts.
    Submits through submit_application/submit_marma_intake service.
    """
    if request.method != 'POST':
        return JsonResponse({'status': 'failed', 'error': 'POST method required.'}, status=405)
        
    try:
        if request.content_type == 'application/json':
            payload = json.loads(request.body.decode('utf-8'))
        else:
            payload = request.POST.dict()
    except Exception as e:
        return JsonResponse({'status': 'failed', 'error': f'Invalid request data: {str(e)}'}, status=400)
        
    item_data = payload.get('item', payload)
    simulate_conflict = bool(payload.get('simulate_conflict') or item_data.get('simulate_conflict'))
    
    result = process_offline_sync(
        item_data=item_data,
        user=request.user,
        channel=item_data.get('preferred_channel', Application.CHANNEL_UDC),
        simulate_conflict=simulate_conflict
    )
    
    return JsonResponse(result)


@login_required
def offline_conflict_resolve_api(request):
    """
    Server-side Conflict Resolution Endpoint (POST).
    Enforces NO SILENT OVERWRITE:
    - keep_local
    - keep_server
    - review_edit
    Protected against IDOR: checks user authorization.
    """
    if request.method != 'POST':
        return JsonResponse({'status': 'failed', 'error': 'POST method required.'}, status=405)
        
    try:
        if request.content_type == 'application/json':
            payload = json.loads(request.body.decode('utf-8'))
        else:
            payload = request.POST.dict()
    except Exception as e:
        return JsonResponse({'status': 'failed', 'error': f'Invalid request data: {str(e)}'}, status=400)
        
    temp_id = payload.get('temp_id', '')
    resolution_choice = payload.get('resolution_choice', '')
    server_app_id = payload.get('server_app_id')
    local_data = payload.get('local_data')
    edited_data = payload.get('edited_data')
    
    # IDOR Security Check:
    # If resolving against existing server_app_id, verify applicant ownership or staff role
    if server_app_id:
        server_app = get_object_or_404(Application, application_id=server_app_id)
        is_staff = has_role(request.user, [
            UserProfile.ROLE_DLAO_OFFICER,
            UserProfile.ROLE_DLAO_SUPPORT_STAFF,
            UserProfile.ROLE_UDC_OPERATOR,
            UserProfile.ROLE_ADMIN
        ])
        is_owner = (server_app.applicant_user and server_app.applicant_user == request.user)
        if not (is_staff or is_owner):
            return JsonResponse({'status': 'failed', 'error': 'Unauthorized: You cannot resolve conflict for another user\'s application.'}, status=403)
            
    try:
        app = resolve_offline_conflict(
            temp_id=temp_id,
            resolution_choice=resolution_choice,
            user=request.user,
            server_app_id=server_app_id,
            local_data=local_data,
            edited_data=edited_data
        )
        return JsonResponse({
            'status': 'resolved',
            'temp_id': temp_id,
            'resolution_choice': resolution_choice,
            'application_id': app.application_id,
            'message': f"Conflict resolved successfully using '{resolution_choice}'."
        })
    except (ValidationError, PermissionDenied) as e:
        return JsonResponse({'status': 'failed', 'error': str(e)}, status=400)



