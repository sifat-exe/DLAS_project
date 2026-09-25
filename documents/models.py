from django.db import models
from django.contrib.auth.models import User

class Document(models.Model):
    """
    Case-attached document records.
    Defined in docs/ARCHITECTURE.md Section 3.5.
    """
    STATUS_UPLOADED = 'uploaded'
    STATUS_VERIFIED = 'verified'
    STATUS_MISSING = 'missing'
    STATUS_UNREADABLE = 'unreadable'

    STATUS_CHOICES = [
        (STATUS_UPLOADED, 'Uploaded'),
        (STATUS_VERIFIED, 'Verified'),
        (STATUS_MISSING, 'Missing Information'),
        (STATUS_UNREADABLE, 'Unreadable/Blurry'),
    ]

    case = models.ForeignKey(
        'cases.CaseRecord',
        on_delete=models.CASCADE,
        related_name='documents'
    )
    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='uploaded_documents'
    )
    title = models.CharField(max_length=255)
    file = models.FileField(upload_to='documents/%Y/%m/')
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_UPLOADED)
    description = models.TextField(blank=True)
    ai_summary = models.TextField(null=True, blank=True)
    ai_confidence = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Document'
        verbose_name_plural = 'Documents'

    def __str__(self):
        return f"{self.case.case_id} - {self.title} ({self.get_status_display()})"


class Signature(models.Model):
    """
    Digital/simulated signatures for documents and ADR accords.
    Defined in docs/ARCHITECTURE.md Section 3.11.
    """
    case = models.ForeignKey(
        'cases.CaseRecord',
        on_delete=models.CASCADE,
        related_name='signatures'
    )
    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name='signatures'
    )
    signer = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='signatures'
    )
    document_hash = models.CharField(max_length=128)
    signature_data = models.TextField()
    signed_at = models.DateTimeField(auto_now_add=True)
    offline_created = models.BooleanField(default=False)
    verified = models.BooleanField(default=False)

    class Meta:
        ordering = ['-signed_at']
        verbose_name = 'Signature'
        verbose_name_plural = 'Signatures'

    def __str__(self):
        return f"Sig by {self.signer.username} on {self.document.title} ({'Verified' if self.verified else 'Unverified'})"
