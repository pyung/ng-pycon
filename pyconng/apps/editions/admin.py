from django.contrib import admin

from .models import Edition


@admin.register(Edition)
class EditionAdmin(admin.ModelAdmin):
    list_display = ["year", "name", "theme", "is_current", "is_published", "starts_on", "ends_on"]
    list_filter = ["is_current", "is_published"]
    search_fields = ["year", "name", "description", "venue"]
    ordering = ["-year"]
