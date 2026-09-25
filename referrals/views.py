from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.contrib.auth.decorators import login_required
from accounts.models import UserProfile
from accounts.permissions import has_role
from referrals.models import Referral
from documents.models import Document
from cases.models import CaseEvent
from cases.services import (
    acknowledge_referral,
    return_referral,
    complete_referral,
    check_and_handoff_overdue_referral,
)

def referral_list(request):
    """
    Overview of all inter-agency and inter-district referrals.
    """
    referrals = []
    if request.user.is_authenticated:
        if not has_role(request.user, [UserProfile.ROLE_DLAO_OFFICER, UserProfile.ROLE_DLAO_SUPPORT_STAFF, UserProfile.ROLE_ADMIN]):
            raise PermissionDenied("Access restricted to DLAO personnel.")
        referrals = Referral.objects.all().order_by('-created_at')

    return render(request, 'referrals/index.html', {'referrals': referrals})

index = referral_list

@login_required
def referral_detail(request, referral_id):
    """
    Referral detail view supporting Acknowledge, Return, Complete, and Handoff actions.
    Enforces server-side authorization and IDOR protection.
    """
    if not has_role(request.user, [UserProfile.ROLE_DLAO_OFFICER, UserProfile.ROLE_DLAO_SUPPORT_STAFF, UserProfile.ROLE_ADMIN]):
        raise PermissionDenied("Access restricted to authorized personnel.")

    referral = get_object_or_404(Referral, id=referral_id)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'acknowledge':
            is_admin = has_role(request.user, [UserProfile.ROLE_ADMIN])
            is_assigned = (referral.assigned_officer == request.user or referral.assigned_officer is None)
            if not is_assigned and not is_admin:
                raise PermissionDenied("You are not authorized to acknowledge this referral. Only the assigned officer may acknowledge.")

            try:
                acknowledge_referral(referral, request.user)
                messages.success(request, f"Referral to {referral.destination} acknowledged successfully.")
            except (ValidationError, PermissionDenied) as e:
                messages.error(request, str(e))

        elif action == 'trigger_handoff':
            # Demo/administrative trigger to test missed-deadline handoff
            try:
                check_and_handoff_overdue_referral(referral, actor=request.user, force=True, channel='web')
                if referral.status == Referral.STATUS_REASSIGNED:
                    messages.warning(
                        request,
                        f"Missed-deadline handoff executed. Referral reassigned to {referral.assigned_officer.username}."
                    )
                else:
                    messages.warning(
                        request,
                        "Missed-deadline handoff executed. No alternate officer available; marked for manual reassignment."
                    )
            except (ValidationError, PermissionDenied) as e:
                messages.error(request, str(e))

        elif action == 'return':
            reason = request.POST.get('reason', '').strip()
            if not reason:
                messages.error(request, "Please state the reason for returning this referral.")
            else:
                try:
                    return_referral(referral, request.user, reason=reason)
                    if referral.status == Referral.STATUS_ESCALATED:
                        messages.warning(request, f"Referral returned {referral.returned_count} times. Status escalated to HIGH PRIORITY officer review.")
                    else:
                        messages.info(request, "Referral returned to origin.")
                except (ValidationError, PermissionDenied) as e:
                    messages.error(request, str(e))

        elif action == 'complete':
            outcome = request.POST.get('outcome', '').strip()
            try:
                complete_referral(referral, request.user, outcome_notes=outcome)
                messages.success(request, f"Referral to {referral.destination} marked completed.")
            except (ValidationError, PermissionDenied) as e:
                messages.error(request, str(e))

        return redirect('referrals:referral_detail', referral_id=referral_id)

    # Document references in referral package
    included_docs = []
    if referral.included_document_ids:
        doc_ids = [int(i.strip()) for i in referral.included_document_ids.split(',') if i.strip().isdigit()]
        if doc_ids:
            included_docs = Document.objects.filter(id__in=doc_ids)
    if not included_docs and referral.case:
        included_docs = referral.case.documents.all()

    is_assigned = (referral.assigned_officer == request.user or referral.assigned_officer is None)
    is_admin = has_role(request.user, [UserProfile.ROLE_ADMIN])
    can_acknowledge = (is_assigned or is_admin)

    # CaseEvents related to this referral
    events = CaseEvent.objects.filter(
        case=referral.case
    ).order_by('-created_at')

    return render(request, 'referrals/detail.html', {
        'referral': referral,
        'case_record': referral.case,
        'included_documents': included_docs,
        'can_acknowledge': can_acknowledge,
        'events': events,
    })
