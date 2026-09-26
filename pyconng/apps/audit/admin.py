from django.contrib import admin

from .models import AuditEntry


@admin.register(AuditEntry)
class AuditEntryAdmin(admin.ModelAdmin):
    """Read-only: an audit trail you can edit is not a trail."""

    list_display = ["created_at", "actor_label", "action", "transition", "target_label", "conference_year"]
    list_filter = ["action", "conference_year", "target_type"]
    search_fields = ["actor_label", "action", "target_label", "note", "target_id"]
    date_hierarchy = "created_at"
    readonly_fields = [f.name for f in AuditEntry._meta.fields] + ["transition"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
