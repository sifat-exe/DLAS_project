from django.db import models
from django.contrib.auth.models import User

class UserProfile(models.Model):
    """
    Profile extension for Django's built-in User model.
    Stores user role, phone number, and preferred language.
    Defined in docs/ARCHITECTURE.md Section 3.1.
    """
    ROLE_CITIZEN = 'citizen'
    ROLE_REPRESENTATIVE = 'representative'
    ROLE_HELPLINE_AGENT = 'helpline_agent'
    ROLE_DLAO_OFFICER = 'dlao_officer'
    ROLE_DLAO_SUPPORT_STAFF = 'dlao_support_staff'
    ROLE_UDC_OPERATOR = 'udc_operator'
    ROLE_PANEL_LAWYER = 'panel_lawyer'
    ROLE_MEDIATOR = 'mediator'
    ROLE_ADMIN = 'admin'

    ROLE_CHOICES = [
        (ROLE_CITIZEN, 'Citizen'),
        (ROLE_REPRESENTATIVE, 'Representative'),
        (ROLE_HELPLINE_AGENT, 'Helpline Agent'),
        (ROLE_DLAO_OFFICER, 'DLAO Officer'),
        (ROLE_DLAO_SUPPORT_STAFF, 'DLAO Support Staff'),
        (ROLE_UDC_OPERATOR, 'UDC Operator'),
        (ROLE_PANEL_LAWYER, 'Panel Lawyer'),
        (ROLE_MEDIATOR, 'Mediator'),
        (ROLE_ADMIN, 'Admin'),
    ]

    LANGUAGE_CHOICES = [
        ('en', 'English'),
        ('bn', 'বাংলা'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=32, choices=ROLE_CHOICES, default=ROLE_CITIZEN)
    phone = models.CharField(max_length=20, blank=True)
    language = models.CharField(max_length=10, choices=LANGUAGE_CHOICES, default='en')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'User Profile'
        verbose_name_plural = 'User Profiles'

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"
