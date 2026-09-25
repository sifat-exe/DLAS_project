from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.contrib.auth.decorators import login_required
from accounts.models import UserProfile
from accounts.permissions import has_role
from cases.models import CaseRecord, CaseEvent
from lawyers.models import LawyerAssignment
from cases.services import (
    accept_lawyer_assignment,
    decline_lawyer_assignment,
    add_lawyer_case_update,
    request_lawyer_change,
    upload_case_document,
)

def index(request):
    return render(request, 'lawyers/index.html')

@login_required
def respond_assignment(request, assignment_id):
    """
    Panel lawyer accepts or declines a case assignment.
    """
    if not has_role(request.user, [UserProfile.ROLE_PANEL_LAWYER, UserProfile.ROLE_ADMIN]):
        raise PermissionDenied("Only authorized Panel Lawyers can respond to case assignments.")

    assignment = get_object_or_404(LawyerAssignment, id=assignment_id)
    if assignment.lawyer != request.user and not request.user.is_superuser:
        raise PermissionDenied("You can only respond to assignments allocated to you.")

    if request.method == 'POST':
        decision = request.POST.get('decision')
        if decision == 'accept':
            try:
                accept_lawyer_assignment(assignment, request.user)
                messages.success(request, f"Case {assignment.case.case_id} accepted and added to your active worklist.")
            except (ValidationError, PermissionDenied) as e:
                messages.error(request, str(e))
        elif decision == 'decline':
            reason = request.POST.get('reason', '').strip()
            try:
                decline_lawyer_assignment(assignment, request.user, reason=reason)
                messages.info(request, f"Case {assignment.case.case_id} assignment declined.")
            except (ValidationError, PermissionDenied) as e:
                messages.error(request, str(e))

    return redirect('dashboard:panel_lawyer')


@login_required
def lawyer_case_detail(request, case_id):
    """
    Panel Lawyer Case Detail view:
    Shows case details, allows adding proceeding updates and requesting lawyer change.
    Enforces that lawyers can only access cases assigned to them.
    """
    if not has_role(request.user, [UserProfile.ROLE_PANEL_LAWYER, UserProfile.ROLE_ADMIN]):
        raise PermissionDenied("Only Panel Lawyers can access this view.")

    case_record = get_object_or_404(CaseRecord, case_id=case_id)
    if case_record.assigned_lawyer != request.user and not request.user.is_superuser:
        raise PermissionDenied("You are not the assigned lawyer for this case.")

    events = CaseEvent.objects.filter(case=case_record).order_by('created_at')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add_update':
            notes = request.POST.get('notes', '').strip()
            if not notes:
                messages.error(request, "Update notes cannot be blank.")
            else:
                add_lawyer_case_update(case_record, request.user, notes)
                messages.success(request, "Proceeding update logged to case record.")

        elif action == 'request_change':
            reason = request.POST.get('reason', '').strip()
            request_lawyer_change(case_record, request.user, reason=reason)
            messages.info(request, "Lawyer reassignment request submitted for DLAO officer approval.")

        elif action == 'upload_document':
            title = request.POST.get('title', '').strip()
            description = request.POST.get('description', '').strip()
            file_obj = request.FILES.get('file')
            if not title:
                messages.error(request, "Document title is required.")
            elif not file_obj:
                messages.error(request, "Please choose a file to upload.")
            else:
                try:
                    upload_case_document(case_record, request.user, title, file_obj, description)
                    messages.success(request, f"Document '{title}' uploaded to case.")
                except (ValidationError, PermissionDenied) as e:
                    messages.error(request, str(e))

        return redirect('lawyers:case_detail', case_id=case_id)

    return render(request, 'lawyers/case_detail.html', {
        'case_record': case_record,
        'application': case_record.application,
        'events': events,
        'documents': case_record.documents.all().order_by('-created_at'),
    })
