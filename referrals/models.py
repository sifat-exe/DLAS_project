from django.db import models
from django.contrib.auth.models import User

class Referral(models.Model):
    """
    Case referrals between DLAO offices or external authorities.
    Defined in docs/ARCHITECTURE.md Section 3.8.
    """
    STATUS_PENDING = 'pending'
    STATUS_ACKNOWLEDGED = 'acknowledged'
    STATUS_RETURNED = 'returned'
    STATUS_ESCALATED = 'escalated'
    STATUS_REASSIGNED = 'reassigned'
    STATUS_COMPLETED = 'completed'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending Acknowledgement'),
        (STATUS_ACKNOWLEDGED, 'Acknowledged'),
        (STATUS_RETURNED, 'Returned'),
        (STATUS_ESCALATED, 'Escalated'),
        (STATUS_REASSIGNED, 'Reassigned'),
        (STATUS_COMPLETED, 'Completed'),
    ]

    case = models.ForeignKey(
        'cases.CaseRecord',
        on_delete=models.PROTECT,
        related_name='referrals'
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='created_referrals'
    )
    destination = models.CharField(max_length=255)
    reason = models.TextField()
    expected_action = models.TextField()
    deadline = models.DateTimeField()
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_PENDING)
    returned_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)

    # Batch 4: Package details & Missed-deadline handoff fields
    assigned_officer = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='assigned_referrals'
    )
    previous_officer = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='previous_referrals'
    )
    reassigned_at = models.DateTimeField(null=True, blank=True)
    missed_deadline_at = models.DateTimeField(null=True, blank=True)
    package_notes = models.TextField(blank=True, default='')
    included_document_ids = models.CharField(max_length=255, blank=True, default='')

    @property
    def is_overdue(self):
        from django.utils import timezone
        if self.status in [self.STATUS_PENDING, self.STATUS_REASSIGNED]:
            return self.deadline < timezone.now()
        return False

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Referral'
        verbose_name_plural = 'Referrals'

    def __str__(self):
        return f"{self.case.case_id} -> {self.destination} ({self.get_status_display()})"
