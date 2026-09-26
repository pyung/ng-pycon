from wagtail_modeladmin.options import ModelAdmin, modeladmin_register

from .models import EmailTemplate


class EmailTemplateAdmin(ModelAdmin):
    model = EmailTemplate
    menu_label = "Email wording"
    menu_icon = "mail"
    menu_order = 700
    list_display = ["key", "conference_year", "subject", "is_active", "updated_at"]
    list_filter = ["is_active", "conference_year"]
    search_fields = ["key", "subject", "body"]
    ordering = ["key", "-conference_year"]


modeladmin_register(EmailTemplateAdmin)
