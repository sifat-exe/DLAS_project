from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.contrib.auth.decorators import login_required
from accounts.models import UserProfile
from accounts.permissions import has_role
from mediation.models import Mediation
from cases.services import update_mediation_outcome

def mediation_list(request):
    """
    List mediation sessions.
    Mediators see their assigned sessions; Officers and Admins see all sessions.
    """
    mediations = []
    if request.user.is_authenticated:
        user = request.user
        if has_role(user, [UserProfile.ROLE_MEDIATOR]):
            mediations = Mediation.objects.filter(mediator=user).order_by('-created_at')
        elif has_role(user, [UserProfile.ROLE_DLAO_OFFICER, UserProfile.ROLE_DLAO_SUPPORT_STAFF, UserProfile.ROLE_ADMIN]):
            mediations = Mediation.objects.all().order_by('-created_at')
        else:
            raise PermissionDenied("Access restricted to mediators and DLAO personnel.")

    return render(request, 'mediation/index.html', {'mediations': mediations})

index = mediation_list

@login_required
def mediation_detail(request, mediation_id):
    """
    Mediation session workspace: schedule, attendance, mode, and human outcome recording.
    Protected strictly: only designated mediator or DLAO officer/admin can access.
    """
    mediation = get_object_or_404(Mediation, id=mediation_id)
    user = request.user

    # Permission check: must be assigned mediator or DLAO officer/admin
    is_assigned_mediator = (mediation.mediator == user)
    is_officer_or_admin = has_role(user, [UserProfile.ROLE_DLAO_OFFICER, UserProfile.ROLE_ADMIN])

    if not (is_assigned_mediator or is_officer_or_admin):
        raise PermissionDenied("You are not authorized to view or manage this mediation session.")

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'record_outcome':
            attendance_status = request.POST.get('attendance_status', 'pending')
            outcome = request.POST.get('outcome', '').strip()
            status = request.POST.get('status', mediation.status)

            try:
                update_mediation_outcome(
                    mediation=mediation,
                    mediator=user,
                    attendance_status=attendance_status,
                    outcome=outcome,
                    status=status
                )
                messages.success(request, "Mediation outcome recorded and logged in official audit trail.")
            except (ValidationError, PermissionDenied) as e:
                messages.error(request, str(e))

            return redirect('mediation:mediation_detail', mediation_id=mediation.id)

    return render(request, 'mediation/detail.html', {
        'mediation': mediation,
        'case_record': mediation.case,
        'is_assigned_mediator': is_assigned_mediator,
        'is_officer_or_admin': is_officer_or_admin,
    })
