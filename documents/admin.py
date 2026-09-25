from django.contrib import admin
from .models import Document, Signature

@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ('case', 'title', 'uploaded_by', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('case__case_id', 'title', 'uploaded_by__username')


@admin.register(Signature)
class SignatureAdmin(admin.ModelAdmin):
    list_display = ('case', 'document', 'signer', 'signed_at', 'verified', 'offline_created')
    list_filter = ('verified', 'offline_created')
    search_fields = ('case__case_id', 'document__title', 'signer__username')
