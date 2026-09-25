from django.contrib import admin
from .models import UserProfile

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'phone', 'language', 'created_at')
    list_filter = ('role', 'language')
    search_fields = ('user__username', 'user__email', 'phone')
