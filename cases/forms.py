import re
from django import forms
from django.core.exceptions import ValidationError
from cases.models import Application

class CitizenApplicationForm(forms.Form):
    """
    Intake form for citizens submitting a legal aid application directly.
    Defined in docs/MASTER_PRD.md Section 2.2.
    """
    name = forms.CharField(
        max_length=255,
        required=True,
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'আপনার সম্পূর্ণ নাম / Full Legal Name'})
    )
    phone = forms.CharField(
        max_length=32,
        required=True,
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': '01XXXXXXXXX'})
    )
    address = forms.CharField(
        required=True,
        widget=forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2, 'placeholder': 'ঠিকানা (গ্রাম/মহল্লা, ডাকঘর, উপজেলা, জেলা) / Full Address'})
    )
    legal_problem = forms.CharField(
        required=True,
        widget=forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2, 'placeholder': 'আইনি সমস্যার সারসংক্ষেপ / Legal Problem Summary'})
    )
    incident_description = forms.CharField(
        required=True,
        widget=forms.Textarea(attrs={'class': 'form-textarea', 'rows': 4, 'placeholder': 'ঘটনার বিস্তারিত বিবরণ / Detailed Incident Description'})
    )
    preferred_channel = forms.ChoiceField(
        choices=Application.CHANNEL_CHOICES,
        initial=Application.CHANNEL_WEB,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    safe_contact_number = forms.CharField(
        max_length=32,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'বিকল্প নিরাপদ ফোন নম্বর (যদি থাকে) / Safe Contact Phone'})
    )
    safe_contact_time = forms.CharField(
        max_length=128,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'যোগাযোগের নিরাপদ সময় (যেমন: সকাল ১০টা - ১২টা) / Safe Contact Time'})
    )
    nid_number = forms.CharField(
        max_length=32,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'জাতীয় পরিচয়পত্র নম্বর (ঐচ্ছিক) / NID Number (Optional)'
        })
    )
    language = forms.ChoiceField(
        choices=[('bn', 'বাংলা (Bangla)'), ('en', 'English')],
        initial='bn',
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    def clean_nid_number(self):
        return self.cleaned_data.get('nid_number', '').strip()

    def clean_name(self):
        name = self.cleaned_data.get('name', '').strip()
        if len(name) < 2:
            raise ValidationError("Name must be at least 2 characters long / নাম কমপক্ষে ২ অক্ষরের হতে হবে।")
        return name

    def clean_phone(self):
        phone = self.cleaned_data.get('phone', '').strip()
        # Clean non-digit characters except +
        digits = re.sub(r'[^\d+]', '', phone)
        if len(digits) < 6:
            raise ValidationError("Please provide a valid contact phone number / অনুগ্রহ করে একটি সঠিক ফোন নম্বর লিখুন।")
        return phone

    def clean_legal_problem(self):
        problem = self.cleaned_data.get('legal_problem', '').strip()
        if len(problem) < 5:
            raise ValidationError("Please describe your legal problem in more detail / আইনি সমস্যাটি অনুগ্রহ করে একটু বিস্তারিত লিখুন।")
        return problem

    def clean_incident_description(self):
        desc = self.cleaned_data.get('incident_description', '').strip()
        if len(desc) < 10:
            raise ValidationError("Please provide a description of the incident / ঘটনার বিবরণ কমপক্ষে ১০ অক্ষরের হতে হবে।")
        return desc


class UDCAssistedApplicationForm(CitizenApplicationForm):
    """
    Intake form for Union Digital Centre (UDC) assisted applications.
    Enforces mandatory citizen informed consent and review read-back before submission.
    """
    citizen_consent = forms.BooleanField(
        required=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
        error_messages={'required': 'Citizen informed consent is strictly mandatory / নাগরিকের সম্মতি গ্রহণ বাধ্যতামূলক।'}
    )
    review_read_back = forms.BooleanField(
        required=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
        error_messages={'required': 'Review read-back confirmation is strictly mandatory / নাগরিককে সম্পূর্ণ বিবরণ পাঠ করে শোনানো বাধ্যতামূলক।'}
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['preferred_channel'].initial = Application.CHANNEL_UDC


class MarmaAssistedIntakeForm(forms.Form):
    """
    Intake form for Marma indigenous language assisted intake (Batch 3 Part A).
    Preserves:
    1. Marma Said (original_statement in Marma language)
    2. Intermediary Translation (translated_statement in Bangla/English)
    3. Typed application data (legal_problem, incident_description)
    4. Explicit verification confirmations before submission.
    """
    name = forms.CharField(
        max_length=255,
        required=True,
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'আবেদনকারীর নাম / Applicant Full Name'})
    )
    phone = forms.CharField(
        max_length=32,
        required=True,
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': '01XXXXXXXXX'})
    )
    address = forms.CharField(
        required=True,
        widget=forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2, 'placeholder': 'ঠিকানা (মৌজা, পাড়া, ইউনিয়ন, উপজেলা, পার্বত্য জেলা) / Address'})
    )
    statement_language = forms.CharField(
        max_length=32,
        initial='marma',
        widget=forms.TextInput(attrs={'class': 'form-input', 'readonly': 'readonly'})
    )
    # Layer 1: Marma Said
    original_statement = forms.CharField(
        required=True,
        widget=forms.Textarea(attrs={
            'class': 'form-textarea',
            'rows': 3,
            'placeholder': 'মারমা ভাষায় আবেদনকারী যা বলেছেন (আসল বক্তব্য) / Original statement as spoken by the applicant in Marma'
        })
    )
    # Layer 2: Intermediary Translation
    translated_statement = forms.CharField(
        required=True,
        widget=forms.Textarea(attrs={
            'class': 'form-textarea',
            'rows': 3,
            'placeholder': 'সহকারী/অনুবাদকের করা বাংলা বা ইংরেজি অনুবাদ / Translation into Bangla/English by intermediary'
        })
    )
    # Layer 3: Typed Application Data
    legal_problem = forms.CharField(
        required=True,
        widget=forms.Textarea(attrs={
            'class': 'form-textarea',
            'rows': 2,
            'placeholder': 'আইনি সমস্যার সারসংক্ষেপ (টাইপকৃত) / Structured Legal Problem'
        })
    )
    incident_description = forms.CharField(
        required=True,
        widget=forms.Textarea(attrs={
            'class': 'form-textarea',
            'rows': 4,
            'placeholder': 'ঘটনার সুনির্দিষ্ট আইনি বিবরণ (টাইপকৃত) / Structured Incident Description'
        })
    )
    safe_contact_number = forms.CharField(
        max_length=32,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'বিকল্প নিরাপদ ফোন (ঐচ্ছিক)'})
    )
    safe_contact_time = forms.CharField(
        max_length=128,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'নিরাপদ সময় (ঐচ্ছিক)'})
    )
    nid_number = forms.CharField(
        max_length=32,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'এনআইডি নম্বর (ঐচ্ছিক)'})
    )
    verify_translation = forms.BooleanField(
        required=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
        error_messages={'required': 'You must verify that the translation accurately represents what was said / অনুবাদটি আসল বক্তব্যের সঠিক প্রতিফলন তা যাচাই করা বাধ্যতামূলক।'}
    )
    verify_typed_data = forms.BooleanField(
        required=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
        error_messages={'required': 'You must verify that the typed structured data accurately represents the translation / টাইপকৃত তথ্য অনুবাদের সাথে সামঞ্জস্যপূর্ণ তা যাচাই করা বাধ্যতামূলক।'}
    )
    applicant_consent = forms.BooleanField(
        required=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
        error_messages={'required': 'Applicant consent and review read-back are mandatory / আবেদনকারীর সম্মতি ও পাঠ করে শোনানো বাধ্যতামূলক।'}
    )

    def clean_phone(self):
        phone = self.cleaned_data.get('phone', '').strip()
        digits = re.sub(r'[^\d+]', '', phone)
        if len(digits) < 6:
            raise ValidationError("Please provide a valid contact phone number / অনুগ্রহ করে একটি সঠিক ফোন নম্বর লিখুন।")
        return phone
