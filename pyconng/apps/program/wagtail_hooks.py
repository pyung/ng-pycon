from wagtail_modeladmin.options import ModelAdmin, ModelAdminGroup, modeladmin_register

from .models import EditionPhoto, Talk


class TalkAdmin(ModelAdmin):
    model = Talk
    menu_label = "Talks"
    menu_icon = "openquote"
    menu_order = 100
    list_display = ["title", "conference_year", "track_name", "is_published"]
    list_filter = ["conference_year", "is_published"]
    search_fields = ["title", "abstract", "speaker_names"]
    ordering = ["-conference_year", "display_order", "title"]
    inspect_view_enabled = True


class EditionPhotoAdmin(ModelAdmin):
    model = EditionPhoto
    menu_label = "Photos"
    menu_icon = "image"
    menu_order = 200
    list_display = ["alt", "conference_year", "caption", "display_order", "is_published"]
    list_filter = ["conference_year", "is_published"]


class ProgramAdminGroup(ModelAdminGroup):
    menu_label = "Programme & archive"
    menu_icon = "openquote"
    menu_order = 180
    items = (TalkAdmin, EditionPhotoAdmin)


modeladmin_register(ProgramAdminGroup)
