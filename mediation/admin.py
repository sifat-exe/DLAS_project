from django.contrib import admin
from .models import Mediation

@admin.register(Mediation)
class MediationAdmin(admin.ModelAdmin):
    list_display = ('case', 'mediator', 'mode', 'status', 'scheduled_at', 'attendance_status')
    list_filter = ('mode', 'status')
    search_fields = ('case__case_id', 'mediator__username')
