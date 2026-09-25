from django.contrib import admin
from .models import Referral

@admin.register(Referral)
class ReferralAdmin(admin.ModelAdmin):
    list_display = ('case', 'destination', 'created_by', 'deadline', 'status', 'returned_count')
    list_filter = ('status',)
    search_fields = ('case__case_id', 'destination', 'created_by__username')
