from django.contrib import admin
from .models import (
    Application,
    CaseRecord,
    CaseEvent,
    Communication,
    Task,
    RelatedCase,
    DuplicateCandidate
)

@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ('application_id', 'name', 'phone', 'preferred_channel', 'status', 'created_at')
    list_filter = ('status', 'preferred_channel', 'language')
    search_fields = ('application_id', 'name', 'phone')
    readonly_fields = ('application_id', 'created_at', 'updated_at')


@admin.register(CaseRecord)
class CaseRecordAdmin(admin.ModelAdmin):
    list_display = ('case_id', 'application', 'assigned_officer', 'assigned_lawyer', 'priority', 'status', 'accepted_at')
    list_filter = ('status', 'priority')
    search_fields = ('case_id', 'application__application_id', 'application__name')
    readonly_fields = ('case_id', 'application', 'accepted_at', 'created_at', 'updated_at')


@admin.register(CaseEvent)
class CaseEventAdmin(admin.ModelAdmin):
    list_display = ('application', 'case', 'action', 'actor', 'actor_role', 'channel', 'provenance', 'created_at')
    list_filter = ('action', 'provenance', 'channel')
    search_fields = ('application__application_id', 'case__case_id', 'action', 'actor_role', 'description')
    readonly_fields = ('application', 'case', 'actor', 'actor_role', 'channel', 'action', 'description', 'provenance', 'authority', 'created_at')

    def has_change_permission(self, request, obj=None):
        # CaseEvent is strictly append-only
        return False

    def has_delete_permission(self, request, obj=None):
        # CaseEvent cannot be deleted
        return False


@admin.register(Communication)
class CommunicationAdmin(admin.ModelAdmin):
    list_display = ('case', 'channel', 'recipient', 'result', 'safe_contact_used', 'created_at')
    list_filter = ('channel', 'safe_contact_used')
    search_fields = ('case__case_id', 'recipient', 'message')


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ('case', 'title', 'assigned_to', 'status', 'priority', 'due_at')
    list_filter = ('status', 'priority')
    search_fields = ('case__case_id', 'title', 'assigned_to__username')


@admin.register(RelatedCase)
class RelatedCaseAdmin(admin.ModelAdmin):
    list_display = ('case', 'related_case', 'relationship_type', 'created_at')
    search_fields = ('case__case_id', 'related_case__case_id')


@admin.register(DuplicateCandidate)
class DuplicateCandidateAdmin(admin.ModelAdmin):
    list_display = ('case', 'possible_case', 'match_score', 'review_status', 'created_at')
    list_filter = ('review_status',)
    search_fields = ('case__case_id', 'possible_case__case_id', 'match_reason')
