from django.urls import path

from . import views

app_name = "grants"

urlpatterns = [
    # Applicant
    path("", views.grant_landing, name="landing"),
    path("apply/", views.grant_apply, name="apply"),
    path("closed/", views.grant_closed, name="closed"),
    path("my-application/", views.grant_my_application, name="my_application"),
    path("<uuid:application_id>/withdraw/", views.grant_withdraw, name="withdraw"),
    path("<uuid:application_id>/accept/", views.grant_accept, name="accept"),
    path("<uuid:application_id>/decline/", views.grant_decline, name="decline"),
    path("<uuid:application_id>/payout/", views.grant_payout_details, name="payout_details"),
    path("<uuid:application_id>/receipt/", views.grant_upload_receipt, name="upload_receipt"),
    path("<uuid:application_id>/", views.grant_application_detail, name="application_detail"),
    # Reviewer
    path("review/", views.grant_review_list, name="review_list"),
    path("review/<uuid:application_id>/", views.grant_review_detail, name="review_detail"),
    # Chair / Admin
    path("admin/assign/", views.grant_admin_assign, name="admin_assign"),
    path("admin/decisions/", views.grant_admin_decisions, name="admin_decisions"),
    path("admin/export/", views.grant_admin_export, name="admin_export"),
    path("admin/promote/", views.grant_promote_waitlist, name="promote_waitlist"),
    path("admin/expire-offers/", views.grant_expire_offers, name="expire_offers"),
    path("admin/reports/", views.grant_reports, name="reports"),
    path("admin/reports/export/", views.grant_reports_export, name="reports_export"),
    path("admin/<uuid:application_id>/", views.grant_admin_detail, name="admin_detail"),
    path("admin/", views.grant_admin_dashboard, name="admin_dashboard"),
    # Finance
    path("finance/", views.grant_finance_list, name="finance_list"),
    path("finance/<uuid:application_id>/", views.grant_finance_detail, name="finance_detail"),
    path(
        "finance/<uuid:application_id>/verify/",
        views.grant_verify_receipt,
        name="verify_receipt",
    ),
]
