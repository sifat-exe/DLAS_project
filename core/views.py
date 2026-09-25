from django.shortcuts import render, redirect
from django.utils.http import url_has_allowed_host_and_scheme


def landing(request):
    """
    DLAS Landing Page.
    Introduces the Digital Legal Aid System and provides quick entry points
    for citizens, representatives, staff, and simulated roles.
    """
    return render(request, 'core/landing.html')

def set_language(request, lang):
    """
    Switches system language between 'en' and 'bn'.
    Stores preference in session and cookie, preserving user state.
    """
    if lang in ['en', 'bn']:
        request.session['django_language'] = lang
    
    # Redirect safely to the previous page or landing
    next_url = request.GET.get('next') or request.META.get('HTTP_REFERER') or '/'
    
    # Ensure redirect stays within safe hosts
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = '/'
        
    response = redirect(next_url)
    if lang in ['en', 'bn']:
        response.set_cookie('django_language', lang, max_age=60*60*24*365)
    return response
