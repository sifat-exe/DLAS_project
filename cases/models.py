from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

class Application(models.Model):
    """
    Represents an intake application before acceptance as a legal-aid case.
    Defined in docs/ARCHITECTURE.md Section 3.2.
    Important: Application ID exists here; Case ID does NOT exist here.
    """
    STATUS_DRAFT = 'DRAFT'
    STATUS_SUBMITTED = 'SUBMITTED'
    STATUS_UNDER_REVIEW = 'UNDER_REVIEW'
    STATUS_ACCEPTED = 'ACCEPTED'
    STATUS_REJECTED = 'REJECTED'

    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_SUBMITTED, 'Submitted'),
        (STATUS_UNDER_REVIEW, 'Under Review'),
        (STATUS_ACCEPTED, 'Accepted'),
        (STATUS_REJECTED, 'Rejected'),
    ]

    CHANNEL_WEB = 'web'
    CHANNEL_UDC = 'udc'
    CHANNEL_HELPLINE = 'helpline'
    CHANNEL_WALK_IN = 'walk_in'

    CHANNEL_CHOICES = [
        (CHANNEL_WEB, 'Web Portal'),
        (CHANNEL_UDC, 'Union Digital Centre (UDC)'),
        (CHANNEL_HELPLINE, '16699 Helpline'),
        (CHANNEL_WALK_IN, 'Office Walk-in'),
    ]

    NID_STATUS_NOT_VERIFIED = 'not_verified'
    NID_STATUS_PENDING = 'verification_pending'
    NID_STATUS_VERIFIED = 'verified'
    NID_STATUS_FAILED = 'verification_failed'

    NID_STATUS_CHOICES = [
        (NID_STATUS_NOT_VERIFIED, 'Not verified'),
        (NID_STATUS_PENDING, 'Verification pending'),
        (NID_STATUS_VERIFIED, 'Verified'),
        (NID_STATUS_FAILED, 'Verification failed'),
    ]

    application_id = models.CharField(max_length=64, unique=True, db_index=True)
    applicant_user = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='applications'
    )
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=32)
    address = models.TextField()
    legal_problem = models.TextField()
    incident_description = models.TextField()
    preferred_channel = models.CharField(max_length=32, choices=CHANNEL_CHOICES, default=CHANNEL_WEB)
    safe_contact_number = models.CharField(max_length=32, blank=True)
    safe_contact_time = models.CharField(max_length=128, blank=True)
    language = models.CharField(max_length=10, default='en')
    nid_number = models.CharField(max_length=32, blank=True, default='')
    nid_verification_status = models.CharField(
        max_length=32,
        choices=NID_STATUS_CHOICES,
        default=NID_STATUS_NOT_VERIFIED
    )
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_SUBMITTED)
    # Marma & Linguistic Provenance (Batch 3 Part A)
    original_statement = models.TextField(blank=True, default='')
    translated_statement = models.TextField(blank=True, default='')
    statement_language = models.CharField(max_length=32, blank=True, default='')
    # Offline sync idempotency & duplicate protection (Batch 3 Part B)
    idempotency_token = models.CharField(max_length=128, blank=True, default='', db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def is_nid_verified(self):
        return self.nid_verification_status == self.NID_STATUS_VERIFIED

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Application'
        verbose_name_plural = 'Applications'

    def __str__(self):
        return f"{self.application_id} - {self.name} ({self.get_status_display()})"


class CaseRecord(models.Model):
    """
    Created ONLY after an authorized DLAO officer accepts an application.
    Defined in docs/ARCHITECTURE.md Section 3.3.
    """
    PRIORITY_LOW = 'LOW'
    PRIORITY_MEDIUM = 'MEDIUM'
    PRIORITY_HIGH = 'HIGH'
    PRIORITY_URGENT = 'URGENT'

    PRIORITY_CHOICES = [
        (PRIORITY_LOW, 'Low'),
        (PRIORITY_MEDIUM, 'Medium'),
        (PRIORITY_HIGH, 'High'),
        (PRIORITY_URGENT, 'Urgent'),
    ]

    STATUS_ACCEPTED = 'ACCEPTED'
    STATUS_IN_PROGRESS = 'IN_PROGRESS'
    STATUS_REFERRED = 'REFERRED'
    STATUS_MEDIATION = 'MEDIATION'
    STATUS_RESOLVED = 'RESOLVED'
    STATUS_CLOSED = 'CLOSED'

    STATUS_CHOICES = [
        (STATUS_ACCEPTED, 'Accepted'),
        (STATUS_IN_PROGRESS, 'In Progress'),
        (STATUS_REFERRED, 'Referred'),
        (STATUS_MEDIATION, 'Mediation'),
        (STATUS_RESOLVED, 'Resolved'),
        (STATUS_CLOSED, 'Closed'),
    ]

    case_id = models.CharField(max_length=64, unique=True, db_index=True)
    application = models.OneToOneField(
        Application,
        on_delete=models.PROTECT,
        related_name='case_record'
    )
    assigned_officer = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='assigned_officer_cases'
    )
    assigned_lawyer = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='assigned_lawyer_cases'
    )
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default=PRIORITY_MEDIUM)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_ACCEPTED)
    accepted_at = models.DateTimeField(default=timezone.now)
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Case Record'
        verbose_name_plural = 'Case Records'

    def __str__(self):
        return f"{self.case_id} ({self.get_status_display()})"


class CaseEvent(models.Model):
    """
    Central audit and history log.
    Treat as append-only: do NOT edit or delete historical events.
    Defined in docs/ARCHITECTURE.md Section 3.4.
    """
    PROVENANCE_APPLICANT_CONFIRMED = 'applicant_confirmed'
    PROVENANCE_REPRESENTATIVE_REPORTED = 'representative_reported'
    PROVENANCE_INTERMEDIARY_TRANSLATED = 'intermediary_translated'
    PROVENANCE_STAFF_ENTERED = 'staff_entered'
    PROVENANCE_AI_INFERRED = 'ai_inferred'

    PROVENANCE_CHOICES = [
        (PROVENANCE_APPLICANT_CONFIRMED, 'Applicant-Confirmed'),
        (PROVENANCE_REPRESENTATIVE_REPORTED, 'Representative-Reported'),
        (PROVENANCE_INTERMEDIARY_TRANSLATED, 'Intermediary-Translated'),
        (PROVENANCE_STAFF_ENTERED, 'Staff-Entered'),
        (PROVENANCE_AI_INFERRED, 'AI-Inferred'),
    ]

    case = models.ForeignKey(
        CaseRecord,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='events'
    )
    application = models.ForeignKey(
        Application,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='events'
    )
    actor = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='case_events'
    )
    actor_role = models.CharField(max_length=64)
    channel = models.CharField(max_length=32)
    action = models.CharField(max_length=64)
    description = models.TextField()
    provenance = models.CharField(max_length=64, choices=PROVENANCE_CHOICES)
    authority = models.CharField(max_length=128, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        verbose_name = 'Case Event'
        verbose_name_plural = 'Case Events'

    def save(self, *args, **kwargs):
        # Enforce append-only integrity: disallow updates to existing events
        if self.pk is not None:
            raise PermissionError("CaseEvent records are append-only and cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        # Enforce append-only integrity: disallow deletion
        raise PermissionError("CaseEvent records are append-only and cannot be deleted.")

    def __str__(self):
        ref = self.case.case_id if self.case else (self.application.application_id if self.application else "N/A")
        return f"[{self.created_at:%Y-%m-%d %H:%M}] {ref} - {self.action} ({self.actor_role})"


class Communication(models.Model):
    """
    Logs communications sent regarding a case across voice, SMS, web, UDC, and walk-in.
    Defined in docs/ARCHITECTURE.md Section 3.6.
    """
    CHANNEL_VOICE = 'voice'
    CHANNEL_SMS = 'sms'
    CHANNEL_WEB = 'web'
    CHANNEL_UDC = 'udc'
    CHANNEL_WALK_IN = 'walk_in'

    CHANNEL_CHOICES = [
        (CHANNEL_VOICE, 'Voice'),
        (CHANNEL_SMS, 'SMS'),
        (CHANNEL_WEB, 'Web'),
        (CHANNEL_UDC, 'UDC'),
        (CHANNEL_WALK_IN, 'Walk-in'),
    ]

    case = models.ForeignKey(
        CaseRecord,
        on_delete=models.CASCADE,
        related_name='communications'
    )
    actor = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='sent_communications'
    )
    channel = models.CharField(max_length=32, choices=CHANNEL_CHOICES)
    recipient = models.CharField(max_length=128)
    message = models.TextField()
    result = models.CharField(max_length=64, default='sent')
    safe_contact_used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Communication'
        verbose_name_plural = 'Communications'

    def __str__(self):
        return f"{self.case.case_id} - {self.channel} to {self.recipient}"


class Task(models.Model):
    """
    Officer and team task tracking for case milestones and deadlines.
    Defined in docs/ARCHITECTURE.md Section 3.7.
    """
    STATUS_PENDING = 'pending'
    STATUS_IN_PROGRESS = 'in_progress'
    STATUS_COMPLETED = 'completed'
    STATUS_OVERDUE = 'overdue'
    STATUS_CANCELLED = 'cancelled'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_IN_PROGRESS, 'In Progress'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_OVERDUE, 'Overdue'),
        (STATUS_CANCELLED, 'Cancelled'),
    ]

    PRIORITY_CHOICES = [
        ('LOW', 'Low'),
        ('MEDIUM', 'Medium'),
        ('HIGH', 'High'),
    ]

    case = models.ForeignKey(
        CaseRecord,
        on_delete=models.CASCADE,
        related_name='tasks'
    )
    assigned_to = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='assigned_tasks'
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    due_at = models.DateTimeField()
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_PENDING)
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='MEDIUM')
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['due_at']
        verbose_name = 'Task'
        verbose_name_plural = 'Tasks'

    def __str__(self):
        return f"{self.case.case_id} - {self.title} ({self.get_status_display()})"


class RelatedCase(models.Model):
    """
    Links related incidents without merging individual case records or outcomes.
    Defined in docs/ARCHITECTURE.md Section 3.12.
    """
    case = models.ForeignKey(
        CaseRecord,
        on_delete=models.CASCADE,
        related_name='related_cases'
    )
    related_case = models.ForeignKey(
        CaseRecord,
        on_delete=models.CASCADE,
        related_name='reverse_related_cases'
    )
    relationship_type = models.CharField(max_length=64)
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='created_relationships'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Related Case Link'
        verbose_name_plural = 'Related Case Links'
        unique_together = ('case', 'related_case')

    def __str__(self):
        return f"{self.case.case_id} <-> {self.related_case.case_id} ({self.relationship_type})"


class DuplicateCandidate(models.Model):
    """
    System-identified potential duplicate cases for human review only.
    Defined in docs/ARCHITECTURE.md Section 3.13.
    """
    STATUS_PENDING = 'pending'
    STATUS_CONFIRMED = 'confirmed'
    STATUS_DISMISSED = 'dismissed'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending Review'),
        (STATUS_CONFIRMED, 'Confirmed Duplicate'),
        (STATUS_DISMISSED, 'Dismissed'),
    ]

    case = models.ForeignKey(
        CaseRecord,
        on_delete=models.CASCADE,
        related_name='duplicate_candidates'
    )
    possible_case = models.ForeignKey(
        CaseRecord,
        on_delete=models.CASCADE,
        related_name='reverse_duplicate_candidates'
    )
    match_score = models.FloatField()
    match_reason = models.TextField()
    review_status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_PENDING)
    reviewed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='reviewed_duplicate_candidates'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Duplicate Candidate'
        verbose_name_plural = 'Duplicate Candidates'

    def __str__(self):
        return f"{self.case.case_id} matches {self.possible_case.case_id} ({self.match_score * 100:.1f}%)"
