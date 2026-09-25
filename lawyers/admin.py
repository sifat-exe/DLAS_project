from django.contrib import admin
from .models import LawyerAssignment

@admin.register(LawyerAssignment)
class LawyerAssignmentAdmin(admin.ModelAdmin):
    list_display = ('case', 'lawyer', 'assigned_by', 'status', 'assigned_at', 'responded_at')
    list_filter = ('status',)
    search_fields = ('case__case_id', 'lawyer__username', 'assigned_by__username')
