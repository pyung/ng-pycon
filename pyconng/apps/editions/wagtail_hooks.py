from wagtail_modeladmin.options import ModelAdmin, modeladmin_register

from .models import Edition


class EditionAdmin(ModelAdmin):
    model = Edition
    menu_label = "Editions"
    menu_icon = "date"
    menu_order = 130
    list_display = ["year", "name", "theme", "is_current", "is_published", "starts_on", "ends_on"]
    list_filter = ["is_current", "is_published"]
    search_fields = ["year", "name", "description", "venue"]
    ordering = ["-year"]


modeladmin_register(EditionAdmin)
