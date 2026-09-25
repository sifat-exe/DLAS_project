from django.db import models
from django.contrib.auth.models import User

class Mediation(models.Model):
    """
    Mediation and ADR tracking attached to a CaseRecord.
    Defined in docs/ARCHITECTURE.md Section 3.10.
    """
    MODE_IN_PERSON = 'in_person'
    MODE_HYBRID = 'hybrid'
    MODE_REMOTE = 'remote'

    MODE_CHOICES = [
        (MODE_IN_PERSON, 'In-Person'),
        (MODE_HYBRID, 'Hybrid'),
        (MODE_REMOTE, 'Remote / Online'),
    ]

    STATUS_PENDING = 'pending'
    STATUS_SCHEDULED = 'scheduled'
    STATUS_IN_PROGRESS = 'in_progress'
    STATUS_AGREED = 'agreed'
    STATUS_FAILED = 'failed'
    STATUS_CLOSED = 'closed'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending Scheduling'),
        (STATUS_SCHEDULED, 'Scheduled'),
        (STATUS_IN_PROGRESS, 'In Session'),
        (STATUS_AGREED, 'Settlement Reached'),
        (STATUS_FAILED, 'Mediation Failed / Unresolved'),
        (STATUS_CLOSED, 'Closed'),
    ]

    case = models.OneToOneField(
        'cases.CaseRecord',
        on_delete=models.PROTECT,
        related_name='mediation'
    )
    mediator = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='mediations'
    )
    mode = models.CharField(max_length=32, choices=MODE_CHOICES, default=MODE_IN_PERSON)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    attendance_status = models.CharField(max_length=64, default='pending')
    outcome = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Mediation'
        verbose_name_plural = 'Mediations'

    def __str__(self):
        return f"{self.case.case_id} ADR ({self.get_status_display()})"
