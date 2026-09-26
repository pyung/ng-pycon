from wagtail_modeladmin.options import ModelAdmin, modeladmin_register

from .models import AuditEntry


class AuditEntryAdmin(ModelAdmin):
    model = AuditEntry
    menu_label = "Audit trail"
    menu_icon = "history"
    menu_order = 900
    list_display = ["created_at", "actor_label", "action", "target_label", "conference_year"]
    list_filter = ["action", "conference_year", "target_type"]
    search_fields = ["actor_label", "action", "target_label", "note"]
    inspect_view_enabled = True


modeladmin_register(AuditEntryAdmin)
