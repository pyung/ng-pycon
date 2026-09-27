from django.urls import path

from . import views

app_name = "program"

urlpatterns = [
    path("talks/<int:talk_id>/save/", views.toggle_saved_talk, name="toggle_saved_talk"),
]
