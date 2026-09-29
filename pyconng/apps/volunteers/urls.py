from django.urls import path

from . import views

app_name = "volunteers"

urlpatterns = [
    # Applicant
    path("", views.landing, name="landing"),
    path("apply/", views.apply, name="apply"),
    path("closed/", views.closed, name="closed"),
    path("my-application/", views.my_application, name="my_application"),
    path("withdraw/", views.withdraw, name="withdraw"),
    path("certificate/", views.certificate, name="certificate"),

    # Coordinator
    path("coordinate/", views.coordinator_dashboard, name="coordinator_dashboard"),
    path("coordinate/export/", views.export_csv, name="export"),
    path("coordinate/shifts/", views.shifts, name="shifts"),
    path("coordinate/shifts/<int:shift_id>/", views.shift_detail, name="shift_detail"),
    path("coordinate/<uuid:application_id>/", views.review_detail, name="review_detail"),
]
