from django.db import models
from django.contrib.auth.models import User

class LawyerAssignment(models.Model):
    """
    Panel lawyer assignment history and workflow states.
    Defined in docs/ARCHITECTURE.md Section 3.9.
    """
    STATUS_PENDING = 'pending'
    STATUS_ACCEPTED = 'accepted'
    STATUS_DECLINED = 'declined'
    STATUS_CHANGED = 'changed'
    STATUS_COMPLETED = 'completed'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending Acceptance'),
        (STATUS_ACCEPTED, 'Accepted'),
        (STATUS_DECLINED, 'Declined'),
        (STATUS_CHANGED, 'Reassigned/Changed'),
        (STATUS_COMPLETED, 'Completed'),
    ]

    case = models.ForeignKey(
        'cases.CaseRecord',
        on_delete=models.PROTECT,
        related_name='lawyer_assignments'
    )
    lawyer = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='assigned_cases_history'
    )
    assigned_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='lawyer_assignments_made'
    )
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_PENDING)
    assigned_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-assigned_at']
        verbose_name = 'Lawyer Assignment'
        verbose_name_plural = 'Lawyer Assignments'

    def __str__(self):
        return f"{self.case.case_id} -> {self.lawyer.username} ({self.get_status_display()})"
