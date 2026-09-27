from django.contrib import admin

from .models import EditionPhoto, Talk


@admin.register(Talk)
class TalkAdmin(admin.ModelAdmin):
    list_display = ["title", "conference_year", "speaker_display", "track_name", "has_video", "is_published"]
    list_filter = ["conference_year", "is_published", "track_name"]
    search_fields = ["title", "abstract", "speaker_names"]
    filter_horizontal = ["speakers"]
    ordering = ["-conference_year", "display_order", "title"]

    @admin.display(description="Speakers")
    def speaker_display(self, obj):
        return obj.speaker_display

    @admin.display(description="Video", boolean=True)
    def has_video(self, obj):
        return obj.has_video


@admin.register(EditionPhoto)
class EditionPhotoAdmin(admin.ModelAdmin):
    list_display = ["alt", "conference_year", "caption", "display_order", "is_published"]
    list_filter = ["conference_year", "is_published"]
    search_fields = ["alt", "caption"]
