from django.contrib import admin

from .models import EmailTemplate


@admin.register(EmailTemplate)
class EmailTemplateAdmin(admin.ModelAdmin):
    list_display = ["key", "conference_year", "subject", "is_active", "updated_at"]
    list_filter = ["is_active", "conference_year", "key"]
    search_fields = ["key", "subject", "body"]
    ordering = ["key", "-conference_year"]
