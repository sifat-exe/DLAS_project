from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.contrib.auth.decorators import login_required
from accounts.models import UserProfile
from accounts.permissions import has_role
from referrals.models import Referral
from cases.services import (
    acknowledge_referral,
    return_referral,
    complete_referral,
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
    Referral detail view supporting Acknowledge, Return, and Complete actions.
    Defined in docs/MASTER_PRD.md Section 2.5.
    """
    if not has_role(request.user, [UserProfile.ROLE_DLAO_OFFICER, UserProfile.ROLE_DLAO_SUPPORT_STAFF, UserProfile.ROLE_ADMIN]):
        raise PermissionDenied("Access restricted to authorized personnel.")

    referral = get_object_or_404(Referral, id=referral_id)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'acknowledge':
            try:
                acknowledge_referral(referral, request.user)
                messages.success(request, f"Referral to {referral.destination} acknowledged successfully.")
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

    return render(request, 'referrals/detail.html', {
        'referral': referral,
        'case_record': referral.case,
    })
