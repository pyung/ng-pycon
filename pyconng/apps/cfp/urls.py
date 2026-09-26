from django.urls import path

from . import views

app_name = "cfp"

urlpatterns = [
    # --- Public -----------------------------------------------------------
    path("", views.cfp_landing, name="landing"),
    path("submit/", views.cfp_submit, name="submit"),
    path("closed/", views.cfp_closed, name="closed"),

    # --- Speaker proposal management -------------------------------------
    path("mine/", views.cfp_my_proposals, name="my_proposals"),
    path(
        "proposal/<uuid:proposal_id>/",
        views.cfp_proposal_detail,
        name="proposal_detail",
    ),
    path(
        "proposal/<uuid:proposal_id>/edit/",
        views.cfp_proposal_edit,
        name="proposal_edit",
    ),
    path(
        "proposal/<uuid:proposal_id>/withdraw/",
        views.cfp_proposal_withdraw,
        name="proposal_withdraw",
    ),
    path(
        "proposal/<uuid:proposal_id>/status/",
        views.cfp_proposal_status,
        name="proposal_status",
    ),
    path(
        "proposal/<uuid:proposal_id>/confirm/",
        views.cfp_proposal_confirm,
        name="proposal_confirm",
    ),

    # --- Reviewer ---------------------------------------------------------
    path("review/", views.review_list, name="review_list"),
    path(
        "review/<uuid:proposal_id>/",
        views.review_detail,
        name="review_detail",
    ),
    path(
        "review/<uuid:proposal_id>/score/",
        views.review_score,
        name="review_score",
    ),
    path(
        "review/<uuid:proposal_id>/conflict/",
        views.review_conflict,
        name="review_conflict",
    ),

    # --- Chair / Admin ----------------------------------------------------
    path("admin/", views.admin_dashboard, name="admin_dashboard"),
    path("admin/assign/", views.admin_assign, name="admin_assign"),
    path("admin/decisions/", views.admin_decisions, name="admin_decisions"),
    path("admin/export/", views.admin_export, name="admin_export"),
    path("admin/email/", views.admin_email, name="admin_email"),
]
