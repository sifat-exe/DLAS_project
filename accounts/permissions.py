from functools import wraps
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from accounts.models import UserProfile

def get_user_role(user):
    """
    Returns the user's role string or None if unauthenticated / no profile.
    Superusers automatically qualify as 'admin'.
    """
    if not user or not user.is_authenticated:
        return None
    if user.is_superuser:
        return UserProfile.ROLE_ADMIN
    if hasattr(user, 'profile'):
        return user.profile.role
    return None

def has_role(user, allowed_roles):
    """
    Checks if a user holds one of the allowed roles.
    allowed_roles can be a string or an iterable of strings.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    
    if isinstance(allowed_roles, str):
        allowed_roles = [allowed_roles]
        
    role = get_user_role(user)
    return role in allowed_roles

def role_required(allowed_roles, raise_exception=True):
    """
    View decorator to enforce role-based access on the server side.
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('accounts:login')
            if not has_role(request.user, allowed_roles):
                if raise_exception:
                    raise PermissionDenied("You do not have permission to access this resource.")
                return redirect('core:landing')
            return view_func(request, *args, **kwargs)
        return _wrapped_view
    return decorator

def can_access_application(user, application):
    """
    Enforces citizen isolation:
    A citizen can ONLY view their own application.
    Authorized DLAO staff, agents, and admins can view applications.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    
    role = get_user_role(user)
    if role in [UserProfile.ROLE_DLAO_OFFICER, UserProfile.ROLE_DLAO_SUPPORT_STAFF, UserProfile.ROLE_ADMIN]:
        return True
    
    if role == UserProfile.ROLE_CITIZEN:
        return application.applicant_user_id == user.id
    
    if role == UserProfile.ROLE_REPRESENTATIVE:
        return application.applicant_user_id == user.id

    return False

def can_access_case(user, case_record):
    """
    Enforces case access boundaries:
    Citizens can only view their own accepted case.
    Lawyers can only view cases assigned to them.
    Mediators can only view cases referred to them.
    DLAO officers, support staff, and admins can access cases within their scope.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True

    role = get_user_role(user)
    if role in [UserProfile.ROLE_DLAO_OFFICER, UserProfile.ROLE_DLAO_SUPPORT_STAFF, UserProfile.ROLE_ADMIN]:
        return True

    if role == UserProfile.ROLE_CITIZEN:
        return case_record.application.applicant_user_id == user.id

    if role == UserProfile.ROLE_PANEL_LAWYER:
        return case_record.assigned_lawyer_id == user.id

    if role == UserProfile.ROLE_MEDIATOR:
        return hasattr(case_record, 'mediation') and case_record.mediation.mediator_id == user.id

    return False
