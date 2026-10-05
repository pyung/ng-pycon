from django.urls import path

from . import views

app_name = "visas"

urlpatterns = [
    # Applicant
    path("", views.visa_landing, name="landing"),
    path("request/", views.visa_request, name="request"),
    path("my-request/", views.visa_my_request, name="my_request"),
    path("<int:pk>/withdraw/", views.visa_withdraw, name="withdraw"),
    path("<int:pk>/letter.pdf", views.visa_letter_download, name="letter"),
    # Public: an embassy has no account here.
    path("verify/", views.visa_verify, name="verify"),
    # Visa officer
    path("queue/", views.visa_queue, name="queue"),
    path("queue/purge/", views.visa_purge, name="purge"),
    path("queue/<int:pk>/", views.visa_detail, name="detail"),
    path("queue/<int:pk>/passport/", views.visa_reveal, name="reveal"),
]
