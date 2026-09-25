from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib import messages
from accounts.models import UserProfile

# Role map for demo / prototype testing
ROLE_DASHBOARD_MAP = {
    UserProfile.ROLE_CITIZEN: 'dashboard:citizen',
    UserProfile.ROLE_DLAO_OFFICER: 'dashboard:officer',
    UserProfile.ROLE_DLAO_SUPPORT_STAFF: 'dashboard:support_staff',
    UserProfile.ROLE_UDC_OPERATOR: 'dashboard:udc_operator',
    UserProfile.ROLE_PANEL_LAWYER: 'dashboard:panel_lawyer',
    UserProfile.ROLE_MEDIATOR: 'dashboard:mediator',
    UserProfile.ROLE_HELPLINE_AGENT: 'dashboard:helpline_agent',
    UserProfile.ROLE_REPRESENTATIVE: 'dashboard:representative',
    UserProfile.ROLE_ADMIN: 'dashboard:admin',
}

def login_view(request):
    """
    Login view supporting standard Django authentication and quick-role preview for the prototype.
    When a demo role is selected, gets or creates the corresponding User and UserProfile.
    """
    if request.method == 'POST':
        role = request.POST.get('demo_role')
        if role and role in ROLE_DASHBOARD_MAP:
            demo_username = f"demo_{role}"
            user, created = User.objects.get_or_create(
                username=demo_username,
                defaults={'first_name': role.replace('_', ' ').title()}
            )
            if created or not hasattr(user, 'profile'):
                UserProfile.objects.update_or_create(
                    user=user,
                    defaults={'role': role, 'language': request.session.get('django_language', 'en')}
                )
            elif user.profile.role != role:
                user.profile.role = role
                user.profile.save(update_fields=['role'])

            login(request, user)
            request.session['active_role'] = role
            messages.success(request, f"Active role switched to: {role.replace('_', ' ').title()}")
            return redirect(ROLE_DASHBOARD_MAP[role])
        
        # Standard username/password login
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            messages.success(request, "Logged in successfully.")
            return redirect('dashboard:index')
        else:
            messages.error(request, "Invalid username or password.")
            
    return render(request, 'accounts/login.html')

def logout_view(request):
    """
    Logout view that clears user session and active role.
    """
    logout(request)
    if 'active_role' in request.session:
        del request.session['active_role']
    messages.info(request, "You have been logged out.")
    return redirect('core:landing')
