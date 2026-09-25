"""
Context processors for DLAS Core.
Handles language preferences and bilingual support.
"""

def language_context(request):
    """
    Provides the current active language code ('en' or 'bn') and a boolean helper.
    Language is retrieved from session, cookie, or query parameters.
    """
    lang = request.session.get('django_language') or request.COOKIES.get('django_language', 'en')
    if lang not in ['en', 'bn']:
        lang = 'en'
    
    return {
        'current_language': lang,
        'is_bangla': (lang == 'bn'),
        'is_english': (lang == 'en'),
    }
