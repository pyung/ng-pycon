from django.urls import path

from . import views

app_name = "conduct"

urlpatterns = [
    path("report/", views.report, name="report"),
    path("report/submitted/", views.report_submitted, name="report_submitted"),
]
