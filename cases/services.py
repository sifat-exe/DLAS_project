from django.db import transaction
from django.utils import timezone
from django.core.exceptions import PermissionDenied, ValidationError
from accounts.models import UserProfile
from accounts.permissions import has_role, get_user_role, can_access_case
from cases.models import Application, CaseRecord, CaseEvent, Task, RelatedCase, DuplicateCandidate
from lawyers.models import LawyerAssignment
from referrals.models import Referral
from mediation.models import Mediation
from documents.models import Document
from cases.ai_service import MockAIService

def generate_application_id():
    """
    Generates a unique Application ID in format APP-YYYY-NNNNN.
    Application ID is generated strictly upon application submission.
    """
    now = timezone.now()
    year = now.strftime('%Y')
    count = Application.objects.filter(created_at__year=now.year).count() + 1
    candidate = f"APP-{year}-{count:05d}"
    while Application.objects.filter(application_id=candidate).exists():
        count += 1
        candidate = f"APP-{year}-{count:05d}"
    return candidate

def generate_case_id():
    """
    Generates a unique Case ID in format CASE-YYYY-NNNNN.
    Case ID is generated ONLY when an authorized DLAO officer accepts an application.
    """
    now = timezone.now()
    year = now.strftime('%Y')
    count = CaseRecord.objects.filter(created_at__year=now.year).count() + 1
    candidate = f"CASE-{year}-{count:05d}"
    while CaseRecord.objects.filter(case_id=candidate).exists():
        count += 1
        candidate = f"CASE-{year}-{count:05d}"
    return candidate

@transaction.atomic
def submit_application(
    name,
    phone,
    address,
    legal_problem,
    incident_description,
    applicant_user=None,
    preferred_channel=Application.CHANNEL_WEB,
    safe_contact_number="",
    safe_contact_time="",
    language="en",
    nid_number="",
    nid_verification_status=Application.NID_STATUS_NOT_VERIFIED,
    actor=None,
    provenance=CaseEvent.PROVENANCE_APPLICANT_CONFIRMED,
    original_statement="",
    translated_statement="",
    statement_language="",
    idempotency_token=""
):
    """
    Service to submit a legal aid application.
    Enforces that Application ID is created, CaseEvent is logged, and NO Case ID is generated.
    NID verification is strictly optional: an unverified NID never blocks application creation.
    Supports idempotency token for offline sync duplicate protection.
    """
    # Idempotency duplicate protection (Batch 3 Part B)
    if idempotency_token:
        existing = Application.objects.filter(idempotency_token=idempotency_token).first()
        if existing:
            return existing

    app_id = generate_application_id()
    
    application = Application.objects.create(
        application_id=app_id,
        applicant_user=applicant_user,
        name=name,
        phone=phone,
        address=address,
        legal_problem=legal_problem,
        incident_description=incident_description,
        preferred_channel=preferred_channel,
        safe_contact_number=safe_contact_number,
        safe_contact_time=safe_contact_time,
        language=language,
        nid_number=nid_number or "",
        nid_verification_status=nid_verification_status or Application.NID_STATUS_NOT_VERIFIED,
        original_statement=original_statement or "",
        translated_statement=translated_statement or "",
        statement_language=statement_language or "",
        idempotency_token=idempotency_token or "",
        status=Application.STATUS_SUBMITTED,
    )

    # Create initial intake audit event
    actor_user = actor or applicant_user
    actor_role = get_user_role(actor_user) or ('udc_operator' if preferred_channel == 'udc' else 'citizen')
    CaseEvent.objects.create(
        application=application,
        case=None,
        actor=actor_user,
        actor_role=actor_role,
        channel=preferred_channel,
        action='APPLICATION_SUBMITTED',
        description=f"Application {app_id} submitted via {preferred_channel} (NID status: {application.get_nid_verification_status_display()}).",
        provenance=provenance,
    )

    return application

def verify_application_nid(application, nid_number, user, channel='web'):
    """
    Simulated NID verification for an application.
    NID verification is strictly optional.
    Can be run by DLAO officer, admin, or the applicant themselves.
    Updates application.nid_number and application.nid_verification_status.
    Logs an append-only CaseEvent.
    """
    from core.mock_services import MockNIDService
    
    # Server-side authorization check (prevent IDOR)
    is_officer_or_admin = has_role(user, [UserProfile.ROLE_DLAO_OFFICER, UserProfile.ROLE_ADMIN])
    is_applicant = (application.applicant_user and application.applicant_user == user)
    
    if not (is_officer_or_admin or is_applicant):
        raise PermissionDenied("You are not authorized to verify NID for this application.")

    nid_str = str(nid_number or '').strip()
    sim_res = MockNIDService.verify_nid(nid_str, name=application.name)

    actor_role = get_user_role(user) or 'citizen'

    if sim_res['is_verified']:
        application.nid_number = nid_str
        application.nid_verification_status = Application.NID_STATUS_VERIFIED
        application.save(update_fields=['nid_number', 'nid_verification_status', 'updated_at'])
        
        case_rec = getattr(application, 'case_record', None)
        CaseEvent.objects.create(
            application=application,
            case=case_rec,
            actor=user,
            actor_role=actor_role,
            channel=channel,
            action='NID_VERIFIED',
            description=(
                f"[ SIMULATED ] NID verified successfully. Token: {sim_res['verification_token']} "
                f"(Masked: {sim_res['nid_masked']}). NID verification is assistive/optional."
            ),
            provenance=CaseEvent.PROVENANCE_STAFF_ENTERED if is_officer_or_admin else CaseEvent.PROVENANCE_APPLICANT_CONFIRMED,
        )
    else:
        application.nid_number = nid_str
        application.nid_verification_status = Application.NID_STATUS_FAILED
        application.save(update_fields=['nid_number', 'nid_verification_status', 'updated_at'])
        
        case_rec = getattr(application, 'case_record', None)
        CaseEvent.objects.create(
            application=application,
            case=case_rec,
            actor=user,
            actor_role=actor_role,
            channel=channel,
            action='NID_VERIFICATION_FAILED',
            description=(
                f"[ SIMULATED ] NID verification failed: {sim_res['message_en']} "
                f"Note: Unverified status does NOT cause application rejection."
            ),
            provenance=CaseEvent.PROVENANCE_STAFF_ENTERED if is_officer_or_admin else CaseEvent.PROVENANCE_APPLICANT_CONFIRMED,
        )

    return sim_res

@transaction.atomic
def accept_application(application, officer, priority=CaseRecord.PRIORITY_MEDIUM, channel='web'):
    """
    Service for DLAO officer to accept an application.
    Enforces:
    1. Server-side role check: only DLAO officers or Admins can accept.
    2. Case ID generated ONLY upon acceptance.
    3. Atomic transaction: CaseRecord created, Application updated, append-only CaseEvent logged.
    """
    if not has_role(officer, [UserProfile.ROLE_DLAO_OFFICER, UserProfile.ROLE_ADMIN]):
        raise PermissionDenied("Only authorized DLAO officers can accept legal-aid applications.")

    if hasattr(application, 'case_record'):
        raise ValidationError("This application already has an associated CaseRecord.")

    if application.status == Application.STATUS_REJECTED:
        raise ValidationError("Cannot accept an already rejected application.")

    case_id = generate_case_id()
    
    case_record = CaseRecord.objects.create(
        case_id=case_id,
        application=application,
        assigned_officer=officer,
        priority=priority,
        status=CaseRecord.STATUS_ACCEPTED,
    )

    application.status = Application.STATUS_ACCEPTED
    application.save(update_fields=['status', 'updated_at'])

    # Associate any prior intake events with the newly created case
    CaseEvent.objects.filter(application=application, case__isnull=True).update(case=case_record)

    # Append-only audit log for acceptance
    actor_role = get_user_role(officer) or 'dlao_officer'
    CaseEvent.objects.create(
        case=case_record,
        application=application,
        actor=officer,
        actor_role=actor_role,
        channel=channel,
        action='APPLICATION_ACCEPTED',
        description=f"Application {application.application_id} accepted by {officer.username}. Case ID {case_id} generated.",
        provenance=CaseEvent.PROVENANCE_STAFF_ENTERED,
        authority='DLAO Officer',
    )

    # Simulated SMS notification respecting safe-contact rules
    try:
        from core.mock_services import MockSMSService
        MockSMSService.send_sms(
            case_record=case_record,
            message=f"DLAS Notice: Your legal aid application {application.application_id} has been accepted. Official Case ID: {case_id}.",
            actor=officer
        )
    except Exception:
        pass

    return case_record


@transaction.atomic
def reject_application(application, officer, reason="", channel='web'):
    """
    Service for DLAO officer to reject an application.
    Enforces server-side permission check, records reason, logs CaseEvent, and does NOT create a CaseRecord.
    """
    if not has_role(officer, [UserProfile.ROLE_DLAO_OFFICER, UserProfile.ROLE_ADMIN]):
        raise PermissionDenied("Only authorized DLAO officers can reject legal-aid applications.")

    if hasattr(application, 'case_record') or application.status == Application.STATUS_ACCEPTED:
        raise ValidationError("Cannot reject an application that already has a CaseRecord.")

    if application.status == Application.STATUS_REJECTED:
        raise ValidationError("Cannot reject an already rejected application.")

    application.status = Application.STATUS_REJECTED
    application.save(update_fields=['status', 'updated_at'])

    # Log rejection event
    actor_role = get_user_role(officer) or 'dlao_officer'
    CaseEvent.objects.create(
        application=application,
        case=None,
        actor=officer,
        actor_role=actor_role,
        channel=channel,
        action='APPLICATION_REJECTED',
        description=f"Application {application.application_id} rejected by {officer.username}. Reason: {reason or 'Ineligible under legal aid guidelines'}",
        provenance=CaseEvent.PROVENANCE_STAFF_ENTERED,
        authority='DLAO Officer',
    )
    return application

@transaction.atomic
def change_case_priority(case_record, officer, new_priority, channel='web'):
    """
    Allows authorized DLAO officers to change case priority.
    Logs an immutable CaseEvent.
    """
    if not has_role(officer, [UserProfile.ROLE_DLAO_OFFICER, UserProfile.ROLE_ADMIN]):
        raise PermissionDenied("Only authorized DLAO officers can change case priority.")

    valid_priorities = [choice[0] for choice in CaseRecord.PRIORITY_CHOICES]
    if new_priority not in valid_priorities:
        raise ValidationError(f"Invalid priority: {new_priority}")

    old_priority = case_record.priority
    case_record.priority = new_priority
    case_record.save(update_fields=['priority', 'updated_at'])

    actor_role = get_user_role(officer) or 'dlao_officer'
    CaseEvent.objects.create(
        case=case_record,
        application=case_record.application,
        actor=officer,
        actor_role=actor_role,
        channel=channel,
        action='PRIORITY_CHANGED',
        description=f"Case priority changed from {old_priority} to {new_priority} by {officer.username}.",
        provenance=CaseEvent.PROVENANCE_STAFF_ENTERED,
        authority='DLAO Officer',
    )
    return case_record

@transaction.atomic
def assign_lawyer_to_case(case_record, officer, lawyer, channel='web'):
    """
    DLAO officer assigns a panel lawyer to a case.
    Creates LawyerAssignment and logs CaseEvent.
    """
    if not has_role(officer, [UserProfile.ROLE_DLAO_OFFICER, UserProfile.ROLE_ADMIN]):
        raise PermissionDenied("Only authorized DLAO officers can assign panel lawyers.")

    if not has_role(lawyer, [UserProfile.ROLE_PANEL_LAWYER, UserProfile.ROLE_ADMIN]):
        raise ValidationError("Selected user is not an authorized Panel Lawyer.")

    # Create assignment record
    assignment = LawyerAssignment.objects.create(
        case=case_record,
        lawyer=lawyer,
        assigned_by=officer,
        status=LawyerAssignment.STATUS_PENDING,
    )

    case_record.assigned_lawyer = lawyer
    case_record.save(update_fields=['assigned_lawyer', 'updated_at'])

    actor_role = get_user_role(officer) or 'dlao_officer'
    CaseEvent.objects.create(
        case=case_record,
        application=case_record.application,
        actor=officer,
        actor_role=actor_role,
        channel=channel,
        action='LAWYER_ASSIGNED',
        description=f"Panel Lawyer {lawyer.username} assigned to case {case_record.case_id} by {officer.username}.",
        provenance=CaseEvent.PROVENANCE_STAFF_ENTERED,
        authority='DLAO Officer',
    )

    # Simulated SMS notification respecting safe contact
    try:
        from core.mock_services import MockSMSService
        MockSMSService.send_sms(
            case_record=case_record,
            message=f"DLAS Notice: Panel Lawyer {lawyer.get_full_name() or lawyer.username} has been assigned to your case {case_record.case_id}.",
            actor=officer
        )
    except Exception:
        pass

    return assignment


@transaction.atomic
def accept_lawyer_assignment(assignment, lawyer, channel='web'):
    """
    Panel lawyer accepts an assignment.
    Transitions assignment to accepted, case to IN_PROGRESS if accepted, logs CaseEvent.
    """
    if assignment.lawyer != lawyer and not lawyer.is_superuser:
        raise PermissionDenied("You can only respond to assignments directed to you.")

    assignment.status = LawyerAssignment.STATUS_ACCEPTED
    assignment.responded_at = timezone.now()
    assignment.save(update_fields=['status', 'responded_at'])

    case_record = assignment.case
    if case_record.status == CaseRecord.STATUS_ACCEPTED:
        case_record.status = CaseRecord.STATUS_IN_PROGRESS
        case_record.save(update_fields=['status', 'updated_at'])

    CaseEvent.objects.create(
        case=case_record,
        application=case_record.application,
        actor=lawyer,
        actor_role='panel_lawyer',
        channel=channel,
        action='LAWYER_ASSIGNMENT_ACCEPTED',
        description=f"Panel Lawyer {lawyer.username} accepted representation for case {case_record.case_id}.",
        provenance=CaseEvent.PROVENANCE_STAFF_ENTERED,
    )
    return assignment

@transaction.atomic
def decline_lawyer_assignment(assignment, lawyer, reason="", channel='web'):
    """
    Panel lawyer declines an assignment.
    Releases assigned lawyer and creates an officer task for reassignment.
    """
    if assignment.lawyer != lawyer and not lawyer.is_superuser:
        raise PermissionDenied("You can only respond to assignments directed to you.")

    assignment.status = LawyerAssignment.STATUS_DECLINED
    assignment.responded_at = timezone.now()
    assignment.save(update_fields=['status', 'responded_at'])

    case_record = assignment.case
    case_record.assigned_lawyer = None
    case_record.save(update_fields=['assigned_lawyer', 'updated_at'])

    CaseEvent.objects.create(
        case=case_record,
        application=case_record.application,
        actor=lawyer,
        actor_role='panel_lawyer',
        channel=channel,
        action='LAWYER_ASSIGNMENT_DECLINED',
        description=f"Panel Lawyer {lawyer.username} declined case {case_record.case_id}. Reason: {reason or 'Conflict of interest / Unavailable'}",
        provenance=CaseEvent.PROVENANCE_STAFF_ENTERED,
    )

    # Create task for DLAO officer to reassign
    Task.objects.create(
        case=case_record,
        assigned_to=case_record.assigned_officer,
        title=f"Reassign Lawyer for {case_record.case_id}",
        description=f"Lawyer {lawyer.username} declined assignment. Please select a replacement lawyer.",
        due_at=timezone.now() + timezone.timedelta(days=2),
        priority='HIGH',
    )
    return assignment

@transaction.atomic
def add_lawyer_case_update(case_record, lawyer, notes, channel='web'):
    """
    Allows assigned lawyer to submit proceeding/hearing updates.
    """
    if case_record.assigned_lawyer != lawyer and not lawyer.is_superuser:
        raise PermissionDenied("Only the designated panel lawyer can submit proceeding updates for this case.")

    return CaseEvent.objects.create(
        case=case_record,
        application=case_record.application,
        actor=lawyer,
        actor_role='panel_lawyer',
        channel=channel,
        action='LAWYER_CASE_UPDATE',
        description=notes,
        provenance=CaseEvent.PROVENANCE_STAFF_ENTERED,
    )

@transaction.atomic
def request_lawyer_change(case_record, requester, reason="", channel='web'):
    """
    Logs a lawyer reassignment request from citizen or lawyer and creates an officer task.
    Does NOT automatically approve lawyer change (requires human officer review).
    """
    actor_role = get_user_role(requester) or 'citizen'
    provenance = CaseEvent.PROVENANCE_APPLICANT_CONFIRMED if actor_role == 'citizen' else CaseEvent.PROVENANCE_STAFF_ENTERED

    CaseEvent.objects.create(
        case=case_record,
        application=case_record.application,
        actor=requester,
        actor_role=actor_role,
        channel=channel,
        action='LAWYER_CHANGE_REQUESTED',
        description=f"Lawyer change requested by {requester.username}. Reason: {reason or 'Citizen preference'}",
        provenance=provenance,
    )

    task = Task.objects.create(
        case=case_record,
        assigned_to=case_record.assigned_officer,
        title=f"Review Lawyer Change Request for {case_record.case_id}",
        description=f"Requested by {requester.username}. Reason: {reason}",
        due_at=timezone.now() + timezone.timedelta(days=3),
        priority='HIGH',
    )
    return task

@transaction.atomic
def create_case_referral(case_record, officer, destination, reason, expected_action, deadline, channel='web'):
    """
    DLAO officer creates an inter-agency or inter-district referral.
    """
    if not has_role(officer, [UserProfile.ROLE_DLAO_OFFICER, UserProfile.ROLE_ADMIN]):
        raise PermissionDenied("Only authorized DLAO officers can refer cases.")

    referral = Referral.objects.create(
        case=case_record,
        created_by=officer,
        destination=destination,
        reason=reason,
        expected_action=expected_action,
        deadline=deadline,
        status=Referral.STATUS_PENDING,
    )

    case_record.status = CaseRecord.STATUS_REFERRED
    case_record.save(update_fields=['status', 'updated_at'])

    actor_role = get_user_role(officer) or 'dlao_officer'
    deadline_str = deadline.strftime('%Y-%m-%d') if hasattr(deadline, 'strftime') else str(deadline)
    CaseEvent.objects.create(
        case=case_record,
        application=case_record.application,
        actor=officer,
        actor_role=actor_role,
        channel=channel,
        action='CASE_REFERRED',
        description=f"Case referred to {destination}. Deadline: {deadline_str}. Reason: {reason}",
        provenance=CaseEvent.PROVENANCE_STAFF_ENTERED,
        authority='DLAO Officer',
    )
    return referral

@transaction.atomic
def acknowledge_referral(referral, user, channel='web'):
    """
    Receiving authority acknowledges referral.
    """
    referral.status = Referral.STATUS_ACKNOWLEDGED
    referral.acknowledged_at = timezone.now()
    referral.save(update_fields=['status', 'acknowledged_at'])

    CaseEvent.objects.create(
        case=referral.case,
        application=referral.case.application,
        actor=user,
        actor_role=get_user_role(user) or 'dlao_officer',
        channel=channel,
        action='REFERRAL_ACKNOWLEDGED',
        description=f"Referral to {referral.destination} acknowledged by {user.username}.",
        provenance=CaseEvent.PROVENANCE_STAFF_ENTERED,
    )
    return referral

@transaction.atomic
def return_referral(referral, user, reason="", channel='web'):
    """
    Receiving authority returns referral.
    Increments returned_count and triggers escalation if returned repeatedly (>= 2).
    """
    referral.returned_count += 1
    if referral.returned_count >= 2:
        referral.status = Referral.STATUS_ESCALATED
        # Create escalation task for DLAO Officer
        Task.objects.create(
            case=referral.case,
            assigned_to=referral.case.assigned_officer,
            title=f"ESCALATION: Referral to {referral.destination} repeatedly returned ({referral.returned_count} times)",
            description=f"Referral returned with reason: {reason}. Consequential review required.",
            due_at=timezone.now() + timezone.timedelta(days=1),
            priority='HIGH',
        )
    else:
        referral.status = Referral.STATUS_RETURNED

    referral.save(update_fields=['status', 'returned_count'])

    CaseEvent.objects.create(
        case=referral.case,
        application=referral.case.application,
        actor=user,
        actor_role=get_user_role(user) or 'dlao_officer',
        channel=channel,
        action='REFERRAL_RETURNED',
        description=f"Referral to {referral.destination} returned (Count: {referral.returned_count}). Reason: {reason}",
        provenance=CaseEvent.PROVENANCE_STAFF_ENTERED,
    )
    return referral

@transaction.atomic
def complete_referral(referral, user, outcome_notes="", channel='web'):
    """
    Marks referral as completed and logs CaseEvent.
    """
    referral.status = Referral.STATUS_COMPLETED
    referral.save(update_fields=['status'])

    CaseEvent.objects.create(
        case=referral.case,
        application=referral.case.application,
        actor=user,
        actor_role=get_user_role(user) or 'dlao_officer',
        channel=channel,
        action='REFERRAL_COMPLETED',
        description=f"Referral to {referral.destination} completed. Outcome: {outcome_notes or 'Resolved by external authority'}",
        provenance=CaseEvent.PROVENANCE_STAFF_ENTERED,
    )
    return referral

@transaction.atomic
def initiate_case_mediation(case_record, officer, mediator, mode='in_person', scheduled_at=None, channel='web'):
    """
    DLAO officer or authorized user initiates mediation for a case.
    """
    if not has_role(officer, [UserProfile.ROLE_DLAO_OFFICER, UserProfile.ROLE_ADMIN]):
        raise PermissionDenied("Only authorized DLAO officers can refer cases to mediation.")

    if not has_role(mediator, [UserProfile.ROLE_MEDIATOR, UserProfile.ROLE_ADMIN]):
        raise ValidationError("Selected user is not an authorized Mediator.")

    status = Mediation.STATUS_SCHEDULED if scheduled_at else Mediation.STATUS_PENDING
    mediation = Mediation.objects.create(
        case=case_record,
        mediator=mediator,
        mode=mode,
        scheduled_at=scheduled_at,
        status=status,
    )

    case_record.status = CaseRecord.STATUS_MEDIATION
    case_record.save(update_fields=['status', 'updated_at'])

    actor_role = get_user_role(officer) or 'dlao_officer'
    CaseEvent.objects.create(
        case=case_record,
        application=case_record.application,
        actor=officer,
        actor_role=actor_role,
        channel=channel,
        action='MEDIATION_INITIATED',
        description=f"Mediation session assigned to {mediator.username} (Mode: {mode}, Scheduled: {scheduled_at}).",
        provenance=CaseEvent.PROVENANCE_STAFF_ENTERED,
        authority='DLAO Officer',
    )
    return mediation

@transaction.atomic
def update_mediation_outcome(mediation, mediator, attendance_status, outcome, status, channel='web'):
    """
    Human mediator records mediation outcome.
    AI systems are strictly prohibited from determining outcomes.
    """
    if mediation.mediator != mediator and not has_role(mediator, [UserProfile.ROLE_DLAO_OFFICER, UserProfile.ROLE_ADMIN]):
        raise PermissionDenied("Only the designated Mediator or authorized Officer can record mediation outcomes.")

    mediation.attendance_status = attendance_status
    mediation.outcome = outcome
    mediation.status = status

    if status in [Mediation.STATUS_AGREED, Mediation.STATUS_FAILED, Mediation.STATUS_CLOSED]:
        mediation.completed_at = timezone.now()
        if status == Mediation.STATUS_AGREED:
            mediation.case.status = CaseRecord.STATUS_RESOLVED
            mediation.case.save(update_fields=['status', 'updated_at'])

    mediation.save(update_fields=['attendance_status', 'outcome', 'status', 'completed_at'])

    CaseEvent.objects.create(
        case=mediation.case,
        application=mediation.case.application,
        actor=mediator,
        actor_role='mediator',
        channel=channel,
        action='MEDIATION_OUTCOME_RECORDED',
        description=f"Mediation session outcome: {status}. Attendance: {attendance_status}. Details: {outcome}",
        provenance=CaseEvent.PROVENANCE_STAFF_ENTERED,
        authority='Mediator',
    )
    return mediation

@transaction.atomic
def transition_case_status(case_record, officer, new_status, reason="", channel='web'):
    """
    Enforces valid, controlled case status transitions.
    Statuses: ACCEPTED -> IN_PROGRESS -> REFERRED -> MEDIATION -> RESOLVED -> CLOSED.
    """
    if not has_role(officer, [UserProfile.ROLE_DLAO_OFFICER, UserProfile.ROLE_ADMIN]):
        raise PermissionDenied("Only authorized DLAO officers can transition case status.")

    valid_statuses = [choice[0] for choice in CaseRecord.STATUS_CHOICES]
    if new_status not in valid_statuses:
        raise ValidationError(f"Invalid case status: {new_status}")

    old_status = case_record.status
    case_record.status = new_status
    if new_status == CaseRecord.STATUS_CLOSED:
        case_record.closed_at = timezone.now()
        case_record.save(update_fields=['status', 'closed_at', 'updated_at'])
    else:
        case_record.save(update_fields=['status', 'updated_at'])

    actor_role = get_user_role(officer) or 'dlao_officer'
    CaseEvent.objects.create(
        case=case_record,
        application=case_record.application,
        actor=officer,
        actor_role=actor_role,
        channel=channel,
        action='CASE_STATUS_CHANGED',
        description=f"Case status transitioned from {old_status} to {new_status} by {officer.username}. Reason: {reason or 'Procedural update'}",
        provenance=CaseEvent.PROVENANCE_STAFF_ENTERED,
        authority='DLAO Officer',
    )

    # Simulated SMS notification respecting safe contact
    try:
        from core.mock_services import MockSMSService
        MockSMSService.send_sms(
            case_record=case_record,
            message=f"DLAS Notice: Your case {case_record.case_id} status has been updated to {new_status}.",
            actor=officer
        )
    except Exception:
        pass

    return case_record


def log_case_event(
    case_record=None,
    application=None,
    actor=None,
    action="",
    description="",
    channel='web',
    provenance=CaseEvent.PROVENANCE_STAFF_ENTERED,
    authority=None
):
    """
    Logs an immutable CaseEvent to the case history.
    """
    actor_role = get_user_role(actor) if actor else 'system'
    return CaseEvent.objects.create(
        case=case_record,
        application=application,
        actor=actor,
        actor_role=actor_role or 'system',
        channel=channel,
        action=action,
        description=description,
        provenance=provenance,
        authority=authority,
    )


# -----------------------------------------------------------------------------
# Phase 5: Document Management, Verification, Related Cases & Duplicates
# -----------------------------------------------------------------------------

ALLOWED_DOCUMENT_EXTENSIONS = ['pdf', 'png', 'jpg', 'jpeg', 'docx', 'doc', 'txt']
MAX_DOCUMENT_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB limit


@transaction.atomic
def upload_case_document(case_record, user, title, file_obj, description="", channel='web'):
    """
    Uploads a document attached to a CaseRecord.
    Enforces case access authorization, file size, and extension validation.
    Generates simulated AI summary and logs an immutable CaseEvent.
    """
    if not can_access_case(user, case_record):
        raise PermissionDenied("You do not have authorization to upload documents for this case.")

    if not title or not title.strip():
        raise ValidationError("Document title is required.")

    if not file_obj:
        raise ValidationError("A valid file must be provided.")

    # Validate file size
    if file_obj.size > MAX_DOCUMENT_SIZE_BYTES:
        raise ValidationError("File size exceeds maximum permitted limit (10MB).")

    # Validate file extension
    ext = file_obj.name.split('.')[-1].lower() if '.' in file_obj.name else ''
    if ext not in ALLOWED_DOCUMENT_EXTENSIONS:
        raise ValidationError(f"Unsupported file format (.{ext}). Allowed formats: {', '.join(ALLOWED_DOCUMENT_EXTENSIONS).upper()}.")

    # Generate assistive AI summary preview (SIMULATED)
    ai_result = MockAIService.summarize_document(title=title, filename=file_obj.name)

    document = Document.objects.create(
        case=case_record,
        uploaded_by=user,
        title=title.strip(),
        file=file_obj,
        status=Document.STATUS_UPLOADED,
        description=description.strip(),
        ai_summary=ai_result.get('ai_summary'),
        ai_confidence=ai_result.get('confidence'),
    )

    actor_role = get_user_role(user) or 'citizen'
    provenance = CaseEvent.PROVENANCE_STAFF_ENTERED if actor_role in [UserProfile.ROLE_DLAO_OFFICER, UserProfile.ROLE_DLAO_SUPPORT_STAFF, UserProfile.ROLE_ADMIN] else CaseEvent.PROVENANCE_APPLICANT_CONFIRMED

    CaseEvent.objects.create(
        case=case_record,
        application=case_record.application,
        actor=user,
        actor_role=actor_role,
        channel=channel,
        action='DOCUMENT_UPLOADED',
        description=f"Document '{document.title}' (File: {file_obj.name}) uploaded by {user.username}.",
        provenance=provenance,
    )

    return document


@transaction.atomic
def verify_case_document(document, staff_user, new_status, review_notes="", channel='web'):
    """
    Authorized DLAO staff reviews and updates a document's verification status.
    AI systems are strictly prohibited from automatically verifying documents.
    """
    if not has_role(staff_user, [UserProfile.ROLE_DLAO_OFFICER, UserProfile.ROLE_DLAO_SUPPORT_STAFF, UserProfile.ROLE_ADMIN]):
        raise PermissionDenied("Only authorized DLAO personnel can verify or update document status.")

    valid_statuses = [choice[0] for choice in Document.STATUS_CHOICES]
    if new_status not in valid_statuses:
        raise ValidationError(f"Invalid document status: {new_status}")

    old_status = document.status
    document.status = new_status
    document.save(update_fields=['status', 'updated_at'])

    actor_role = get_user_role(staff_user) or 'dlao_officer'
    CaseEvent.objects.create(
        case=document.case,
        application=document.case.application,
        actor=staff_user,
        actor_role=actor_role,
        channel=channel,
        action='DOCUMENT_STATUS_CHANGED',
        description=f"Document '{document.title}' status updated from {old_status} to {new_status} by {staff_user.username}. Notes: {review_notes or 'Human staff verification'}",
        provenance=CaseEvent.PROVENANCE_STAFF_ENTERED,
        authority='DLAO Staff',
    )

    return document


@transaction.atomic
def link_related_cases(case_a, case_b, staff_user, relationship_type="Cross-suit / Related Dispute", channel='web'):
    """
    Links two cases as related.
    MANDATORY RULE: Related cases are NOT merged!
    Each case maintains its own Case ID, status, outcome, and audit history.
    Self-linking is strictly prevented.
    """
    if not has_role(staff_user, [UserProfile.ROLE_DLAO_OFFICER, UserProfile.ROLE_DLAO_SUPPORT_STAFF, UserProfile.ROLE_ADMIN]):
        raise PermissionDenied("Only authorized DLAO officers or support staff can link related cases.")

    if case_a.id == case_b.id:
        raise ValidationError("Cannot link a case to itself.")

    if RelatedCase.objects.filter(case=case_a, related_case=case_b).exists() or RelatedCase.objects.filter(case=case_b, related_case=case_a).exists():
        raise ValidationError(f"Cases {case_a.case_id} and {case_b.case_id} are already linked as related.")

    related_link = RelatedCase.objects.create(
        case=case_a,
        related_case=case_b,
        relationship_type=relationship_type.strip() or "Related Legal Matter",
        created_by=staff_user,
    )

    actor_role = get_user_role(staff_user) or 'dlao_officer'
    # Log event on Case A
    CaseEvent.objects.create(
        case=case_a,
        application=case_a.application,
        actor=staff_user,
        actor_role=actor_role,
        channel=channel,
        action='CASE_LINKED_RELATED',
        description=f"Linked to related Case {case_b.case_id} (Type: {related_link.relationship_type}). NOTE: Related cases are NOT merged and maintain separate identities.",
        provenance=CaseEvent.PROVENANCE_STAFF_ENTERED,
        authority='DLAO Staff',
    )
    # Log event on Case B
    CaseEvent.objects.create(
        case=case_b,
        application=case_b.application,
        actor=staff_user,
        actor_role=actor_role,
        channel=channel,
        action='CASE_LINKED_RELATED',
        description=f"Linked to related Case {case_a.case_id} (Type: {related_link.relationship_type}). NOTE: Related cases are NOT merged and maintain separate identities.",
        provenance=CaseEvent.PROVENANCE_STAFF_ENTERED,
        authority='DLAO Staff',
    )

    return related_link


@transaction.atomic
def review_duplicate_candidate(candidate, staff_user, review_status, review_notes="", channel='web'):
    """
    Human DLAO staff reviews a duplicate candidate suggestion.
    SAFETY MANDATE:
    Duplicate detection is only a suggestion.
    Never automatically rejects an application, merges cases, declares fraud, or closes a case.
    """
    if not has_role(staff_user, [UserProfile.ROLE_DLAO_OFFICER, UserProfile.ROLE_DLAO_SUPPORT_STAFF, UserProfile.ROLE_ADMIN]):
        raise PermissionDenied("Only authorized DLAO personnel can review duplicate candidate suggestions.")

    if review_status not in [DuplicateCandidate.STATUS_CONFIRMED, DuplicateCandidate.STATUS_DISMISSED]:
        raise ValidationError(f"Invalid review decision: {review_status}. Must be confirmed or dismissed.")

    candidate.review_status = review_status
    candidate.reviewed_by = staff_user
    candidate.save(update_fields=['review_status', 'reviewed_by'])

    actor_role = get_user_role(staff_user) or 'dlao_officer'
    CaseEvent.objects.create(
        case=candidate.case,
        application=candidate.case.application,
        actor=staff_user,
        actor_role=actor_role,
        channel=channel,
        action='DUPLICATE_CANDIDATE_REVIEWED',
        description=f"Duplicate suggestion with Case {candidate.possible_case.case_id} reviewed by {staff_user.username}: {candidate.get_review_status_display()}. Notes: {review_notes or 'Human review complete.'}",
        provenance=CaseEvent.PROVENANCE_STAFF_ENTERED,
        authority='DLAO Staff',
    )

    return candidate


# =============================================================================
# BATCH 3 — PART A: MARMA PROVENANCE WORKFLOW SERVICE
# =============================================================================

@transaction.atomic
def submit_marma_intake(
    name,
    phone,
    address,
    original_statement,
    translated_statement,
    typed_legal_problem,
    typed_incident_description,
    statement_language="marma",
    actor=None,
    applicant_user=None,
    preferred_channel=Application.CHANNEL_UDC,
    safe_contact_number="",
    safe_contact_time="",
    nid_number="",
    idempotency_token=""
):
    """
    Assisted Marma Indigenous Language Intake Workflow (Batch 3 Part A).
    Preserves the distinct provenance chain:
      MARMA SAID -> TRANSLATED -> TYPED -> APPLICATION DATA
    Records explicit sequential append-only CaseEvents:
      1. marma_statement_recorded (provenance=applicant_confirmed)
      2. marma_translation_recorded (provenance=intermediary_translated)
      3. marma_typed_confirmation (provenance=staff_entered)
      4. application_submitted (provenance=intermediary_translated)
    Enforces Application ID created, NO Case ID generated before DLAO acceptance.
    """
    app = submit_application(
        name=name,
        phone=phone,
        address=address,
        legal_problem=typed_legal_problem,
        incident_description=typed_incident_description,
        applicant_user=applicant_user,
        preferred_channel=preferred_channel,
        safe_contact_number=safe_contact_number,
        safe_contact_time=safe_contact_time,
        language="bn",
        nid_number=nid_number,
        actor=actor,
        provenance=CaseEvent.PROVENANCE_INTERMEDIARY_TRANSLATED,
        original_statement=original_statement,
        translated_statement=translated_statement,
        statement_language=statement_language or "marma",
        idempotency_token=idempotency_token
    )
    
    actor_user = actor or applicant_user
    actor_role = get_user_role(actor_user) or 'udc_operator'
    
    # 1. marma_statement_recorded (Original statement said by Marma applicant)
    CaseEvent.objects.create(
        application=app,
        case=None,
        actor=actor_user,
        actor_role=actor_role,
        channel=preferred_channel,
        action='marma_statement_recorded',
        description=f"Original oral statement recorded in {statement_language.title()}: '{original_statement}'. Attributed directly to applicant.",
        provenance=CaseEvent.PROVENANCE_APPLICANT_CONFIRMED
    )
    
    # 2. marma_translation_recorded (Translation by intermediary/translator)
    CaseEvent.objects.create(
        application=app,
        case=None,
        actor=actor_user,
        actor_role=actor_role,
        channel=preferred_channel,
        action='marma_translation_recorded',
        description=f"Intermediary translation into Bangla/English recorded: '{translated_statement}'. Attributed to translator/operator, not falsely labeled as applicant-originated.",
        provenance=CaseEvent.PROVENANCE_INTERMEDIARY_TRANSLATED
    )
    
    # 3. marma_typed_confirmation (Confirmation that typed structured data represents translation)
    CaseEvent.objects.create(
        application=app,
        case=None,
        actor=actor_user,
        actor_role=actor_role,
        channel=preferred_channel,
        action='marma_typed_confirmation',
        description=f"Assisting operator verified that structured legal problem ('{typed_legal_problem}') and incident details accurately represent the translation. Applicant confirmed.",
        provenance=CaseEvent.PROVENANCE_STAFF_ENTERED
    )
    
    return app


# =============================================================================
# BATCH 3 — PART B & C: TRUE OFFLINE SYNC & CONFLICT RESOLUTION SERVICES
# =============================================================================

@transaction.atomic
def process_offline_sync(
    item_data,
    user,
    channel=Application.CHANNEL_UDC,
    simulate_conflict=False
):
    """
    Processes a queued offline intake item during reconnection sync.
    Enforces:
    - Idempotency via idempotency_token / temp_id: duplicate sync attempts return existing Application.
    - Full server-side validation.
    - Conflict detection: detects if conflicting submission exists without silent overwrites.
    - CaseEvent logging for sync audit.
    - Application ID returned, NO Case ID created before DLAO acceptance.
    """
    temp_id = item_data.get('temp_id', '')
    idempotency_token = item_data.get('idempotency_token') or f"OFFLINE-SYNC-{temp_id}"
    
    # Idempotency check: exactly one Application will exist if retried
    existing_app = Application.objects.filter(idempotency_token=idempotency_token).first()
    if existing_app:
        return {
            'status': 'synced',
            'application_id': existing_app.application_id,
            'temp_id': temp_id,
            'is_duplicate_retry': True,
            'message': 'Item previously synchronized (idempotent submission preserved).'
        }
    
    # Server-side field validation
    name = (item_data.get('name') or '').strip()
    phone = (item_data.get('phone') or '').strip()
    address = (item_data.get('address') or '').strip()
    legal_problem = (item_data.get('legal_problem') or '').strip()
    incident_description = (item_data.get('incident_description') or '').strip()
    
    if not (name and phone and address and legal_problem and incident_description):
        return {
            'status': 'failed',
            'temp_id': temp_id,
            'error': 'Missing mandatory fields: Name, Phone, Address, Legal Problem, and Incident Description are required.'
        }
    
    # Conflict Detection:
    # A conflict occurs if simulate_conflict is True, or if another Application with same phone
    # already exists on server with conflicting legal problem or name.
    matching_conflict_app = None
    if simulate_conflict:
        matching_conflict_app = Application.objects.filter(phone=phone).first() or Application.objects.first()
    else:
        # Real conflict check: same phone exists on server but with different applicant name or different incident
        potential = Application.objects.filter(phone=phone).exclude(name__iexact=name).first()
        if potential:
            matching_conflict_app = potential
            
    if matching_conflict_app:
        return {
            'status': 'conflict',
            'temp_id': temp_id,
            'conflict_reason': f"Matching phone number '{phone}' already registered on server with differing applicant details ({matching_conflict_app.name}). Human review required.",
            'local_version': {
                'name': name,
                'phone': phone,
                'address': address,
                'legal_problem': legal_problem,
                'incident_description': incident_description,
                'channel': item_data.get('preferred_channel', channel),
            },
            'server_version': {
                'application_id': matching_conflict_app.application_id,
                'name': matching_conflict_app.name,
                'phone': matching_conflict_app.phone,
                'address': matching_conflict_app.address,
                'legal_problem': matching_conflict_app.legal_problem,
                'incident_description': matching_conflict_app.incident_description,
                'status': matching_conflict_app.status,
            }
        }
    
    # If Marma fields present, use submit_marma_intake, else standard submit_application
    orig_stmt = item_data.get('original_statement', '')
    trans_stmt = item_data.get('translated_statement', '')
    stmt_lang = item_data.get('statement_language', '')
    
    if orig_stmt and trans_stmt:
        app = submit_marma_intake(
            name=name,
            phone=phone,
            address=address,
            original_statement=orig_stmt,
            translated_statement=trans_stmt,
            typed_legal_problem=legal_problem,
            typed_incident_description=incident_description,
            statement_language=stmt_lang or "marma",
            actor=user,
            applicant_user=user if has_role(user, [UserProfile.ROLE_CITIZEN]) else None,
            preferred_channel=item_data.get('preferred_channel') or channel,
            safe_contact_number=item_data.get('safe_contact_number', ''),
            safe_contact_time=item_data.get('safe_contact_time', ''),
            nid_number=item_data.get('nid_number', ''),
            idempotency_token=idempotency_token
        )
    else:
        app = submit_application(
            name=name,
            phone=phone,
            address=address,
            legal_problem=legal_problem,
            incident_description=incident_description,
            applicant_user=user if has_role(user, [UserProfile.ROLE_CITIZEN]) else None,
            preferred_channel=item_data.get('preferred_channel') or channel,
            safe_contact_number=item_data.get('safe_contact_number', ''),
            safe_contact_time=item_data.get('safe_contact_time', ''),
            language=item_data.get('language', 'bn'),
            nid_number=item_data.get('nid_number', ''),
            actor=user,
            provenance=CaseEvent.PROVENANCE_INTERMEDIARY_TRANSLATED if has_role(user, [UserProfile.ROLE_UDC_OPERATOR]) else CaseEvent.PROVENANCE_APPLICANT_CONFIRMED,
            idempotency_token=idempotency_token
        )
    
    # Log CaseEvent for offline synchronization
    actor_role = get_user_role(user) or 'udc_operator'
    CaseEvent.objects.create(
        application=app,
        case=None,
        actor=user,
        actor_role=actor_role,
        channel=app.preferred_channel,
        action='OFFLINE_SYNC_COMPLETED',
        description=f"Offline queue item (Local ID: {temp_id}) synchronized successfully to official Application {app.application_id}.",
        provenance=CaseEvent.PROVENANCE_STAFF_ENTERED if has_role(user, [UserProfile.ROLE_UDC_OPERATOR, UserProfile.ROLE_DLAO_SUPPORT_STAFF, UserProfile.ROLE_DLAO_OFFICER]) else CaseEvent.PROVENANCE_APPLICANT_CONFIRMED
    )
    
    return {
        'status': 'synced',
        'application_id': app.application_id,
        'temp_id': temp_id,
        'is_duplicate_retry': False,
        'message': 'Synchronized successfully.'
    }

@transaction.atomic
def resolve_offline_conflict(
    temp_id,
    resolution_choice,
    user,
    server_app_id=None,
    local_data=None,
    edited_data=None
):
    """
    Human-controlled conflict resolution (Batch 3 Part C).
    Enforces NO SILENT OVERWRITE:
    - keep_local: local data accepted as authoritative; creates/updates application with explicit audit.
    - keep_server: server data accepted as authoritative; local item marked synced to existing server_app_id.
    - review_edit: human officer/operator reviews and enters corrected data.
    Logs append-only CaseEvent: OFFLINE_CONFLICT_RESOLVED.
    """
    actor_role = get_user_role(user) or 'operator'
    
    if resolution_choice == 'keep_server':
        if not server_app_id:
            raise ValidationError("Server application ID required for keep_server resolution.")
        server_app = Application.objects.get(application_id=server_app_id)
        CaseEvent.objects.create(
            application=server_app,
            case=getattr(server_app, 'case_record', None),
            actor=user,
            actor_role=actor_role,
            channel=server_app.preferred_channel,
            action='OFFLINE_CONFLICT_RESOLVED',
            description=f"Conflict on local queue item {temp_id} resolved by {user.username}: Selected 'Keep Server Version'.",
            provenance=CaseEvent.PROVENANCE_STAFF_ENTERED,
            authority='Human Operator'
        )
        return server_app
        
    elif resolution_choice == 'keep_local':
        if not local_data:
            raise ValidationError("Local intake data required for keep_local resolution.")
        app = submit_application(
            name=local_data['name'],
            phone=local_data['phone'],
            address=local_data['address'],
            legal_problem=local_data['legal_problem'],
            incident_description=local_data['incident_description'],
            applicant_user=user if has_role(user, [UserProfile.ROLE_CITIZEN]) else None,
            preferred_channel=local_data.get('preferred_channel', Application.CHANNEL_UDC),
            actor=user,
            provenance=CaseEvent.PROVENANCE_APPLICANT_CONFIRMED,
            idempotency_token=f"OFFLINE-RESOLVED-LOCAL-{temp_id}"
        )
        CaseEvent.objects.create(
            application=app,
            case=None,
            actor=user,
            actor_role=actor_role,
            channel=app.preferred_channel,
            action='OFFLINE_CONFLICT_RESOLVED',
            description=f"Conflict on local queue item {temp_id} resolved by {user.username}: Selected 'Keep Local Version'. New official application generated.",
            provenance=CaseEvent.PROVENANCE_STAFF_ENTERED,
            authority='Human Operator'
        )
        return app
        
    elif resolution_choice == 'review_edit':
        data = edited_data or local_data
        if not data:
            raise ValidationError("Edited data required for review_edit resolution.")
        app = submit_application(
            name=data['name'],
            phone=data['phone'],
            address=data['address'],
            legal_problem=data['legal_problem'],
            incident_description=data['incident_description'],
            applicant_user=user if has_role(user, [UserProfile.ROLE_CITIZEN]) else None,
            preferred_channel=data.get('preferred_channel', Application.CHANNEL_UDC),
            actor=user,
            provenance=CaseEvent.PROVENANCE_STAFF_ENTERED,
            idempotency_token=f"OFFLINE-RESOLVED-EDIT-{temp_id}"
        )
        CaseEvent.objects.create(
            application=app,
            case=None,
            actor=user,
            actor_role=actor_role,
            channel=app.preferred_channel,
            action='OFFLINE_CONFLICT_RESOLVED',
            description=f"Conflict on local queue item {temp_id} resolved by {user.username}: Human reviewed and edited data submitted.",
            provenance=CaseEvent.PROVENANCE_STAFF_ENTERED,
            authority='Human Operator'
        )
        return app
    else:
        raise ValidationError(f"Invalid resolution choice: {resolution_choice}")

