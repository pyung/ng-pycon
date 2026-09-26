from wagtail_modeladmin.options import ModelAdmin, modeladmin_register

from .models import Meetup


class MeetupAdmin(ModelAdmin):
    model = Meetup
    menu_label = "Meetups"
    menu_icon = "site"
    menu_order = 160
    list_display = ["title", "chapter", "starts_at", "conference_year", "is_published"]
    list_filter = ["conference_year", "is_published", "chapter"]
    search_fields = ["title", "venue", "chapter__city"]
    ordering = ["-starts_at"]


modeladmin_register(MeetupAdmin)
