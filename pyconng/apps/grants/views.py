"""
Travel Grant views.

Applicant: landing, apply, closed, my_application, application_detail, withdraw
Reviewer:  review_list, review_detail, review_score
Chair:     admin_dashboard, admin_detail, admin_assign, admin_decisions, admin_export
Finance:   finance_list, finance_detail
"""

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from pyconng.context_processors import CURRENT_YEAR

from .decorators import (
    grant_chair_required,
    grant_finance_required,
    grant_open_required,
    grant_reviewer_required,
    login_required,
)
from .forms import (
    GrantBulkDecisionForm,
    GrantPaymentForm,
    GrantReviewForm,
    TravelGrantApplicationForm,
)
from .models import (
    GrantReviewerAssignment,
    TravelGrantApplication,
    TravelGrantPayment,
    TravelGrantReview,
)
from accounts.roles import Role, roles_for, users_with_role

from .services import GrantService


def grant_landing(request):
    """Landing page - status badge and Apply CTA when open."""
    settings_obj = GrantService.get_current_settings()
    return render(request, "grants/landing.html", {
        "grant_settings": settings_obj,
        "conference_year": CURRENT_YEAR,
    })


@grant_open_required
@login_required
def grant_apply(request):
    """Application form - create new or edit draft."""
    settings_obj = GrantService.get_current_settings()
    application = GrantService.get_user_application(request.user)
    cfp_info = GrantService.get_user_cfp_info(request.user)

    if application and not application.is_editable:
        # Already submitted – redirect to dashboard
        return redirect("grants:my_application")

    if request.method == "POST":
        action = request.POST.get("action", "draft")
        submit_action = action == "submit"

        form = TravelGrantApplicationForm(
            request.POST,
            instance=application,
            submit_action=submit_action,
        )
        if form.is_valid():
            app = form.save(commit=False)
            app.user = request.user
            app.conference_year = CURRENT_YEAR
            if submit_action:
                app.status = TravelGrantApplication.STATUS_SUBMITTED
                app.submitted_at = timezone.now()
            app.save()

            if submit_action:
                GrantService.on_application_submitted(app)
                messages.success(
                    request,
                    "Your travel grant application has been submitted successfully!",
                )
            else:
                messages.success(request, "Draft saved. Remember to submit before the deadline.")
            return redirect("grants:my_application")
    else:
        form = TravelGrantApplicationForm(instance=application)

    return render(request, "grants/apply.html", {
        "form": form,
        "grant_settings": settings_obj,
        "application": application,
        "cfp_info": cfp_info,
        "conference_year": CURRENT_YEAR,
        "user": request.user,
    })


def grant_closed(request):
    """Notice that applications are closed."""
    settings_obj = GrantService.get_current_settings()
    return render(request, "grants/closed.html", {
        "grant_settings": settings_obj,
        "conference_year": CURRENT_YEAR,
    })


@login_required
def grant_my_application(request):
    """Applicant dashboard - status, view, withdraw."""
    application = GrantService.get_user_application(request.user)
    return render(request, "grants/my_application.html", {
        "application": application,
        "conference_year": CURRENT_YEAR,
        "grant_open": GrantService.is_grant_open(),
    })


@login_required
def grant_application_detail(request, application_id):
    """Read-only view of a submitted application."""
    application = get_object_or_404(
        TravelGrantApplication,
        pk=application_id,
        user=request.user,
    )
    return render(request, "grants/application_detail.html", {
        "application": application,
        "conference_year": CURRENT_YEAR,
    })


@login_required
@require_POST
def grant_withdraw(request, application_id):
    """Withdraw a submitted application."""
    application = get_object_or_404(
        TravelGrantApplication,
        pk=application_id,
        user=request.user,
    )
    if not application.can_withdraw:
        messages.error(request, "This application cannot be withdrawn.")
        return redirect("grants:my_application")
    application.status = TravelGrantApplication.STATUS_WITHDRAWN
    application.save(update_fields=["status", "updated_at"])
    messages.success(request, "Your application has been withdrawn.")
    return redirect("grants:my_application")

@grant_reviewer_required
def grant_review_list(request):
    """List applications assigned to this reviewer."""
    assignments = (
        GrantReviewerAssignment.objects.filter(reviewer=request.user)
        .select_related("application__user")
        .prefetch_related("review")
        .order_by("-assigned_at")
    )
    return render(request, "grants/review_list.html", {
        "assignments": assignments,
        "conference_year": CURRENT_YEAR,
    })


@grant_reviewer_required
def grant_review_detail(request, application_id):
    """View application and submit/update review scores."""
    assignment = get_object_or_404(
        GrantReviewerAssignment,
        reviewer=request.user,
        application_id=application_id,
    )
    application = assignment.application
    existing_review = getattr(assignment, "review", None)

    if request.method == "POST":
        form = GrantReviewForm(request.POST)
        if form.is_valid():
            TravelGrantReview.objects.update_or_create(
                assignment=assignment,
                defaults={
                    "need_score": form.cleaned_data["need_score"],
                    "impact_score": form.cleaned_data["impact_score"],
                    "contribution_score": form.cleaned_data["contribution_score"],
                    "diversity_score": form.cleaned_data["diversity_score"],
                    "comments": form.cleaned_data.get("comments", ""),
                },
            )
            messages.success(
                request,
                "Review submitted." if not existing_review else "Review updated.",
            )
            return redirect("grants:review_detail", application_id=application_id)
    else:
        if existing_review:
            form = GrantReviewForm(initial={
                "need_score": existing_review.need_score,
                "impact_score": existing_review.impact_score,
                "contribution_score": existing_review.contribution_score,
                "diversity_score": existing_review.diversity_score,
                "comments": existing_review.comments,
            })
        else:
            form = GrantReviewForm()

    cfp_info = GrantService.get_user_cfp_info(application.user)
    return render(request, "grants/review_detail.html", {
        "assignment": assignment,
        "application": application,
        "form": form,
        "existing_review": existing_review,
        "cfp_info": cfp_info,
        "conference_year": CURRENT_YEAR,
    })

@grant_chair_required
def grant_admin_dashboard(request):
    """Admin dashboard: stats, filters, application list."""
    stats = GrantService.get_dashboard_stats()
    settings_obj = GrantService.get_current_settings()

    applications = (
        TravelGrantApplication.objects.filter(conference_year=CURRENT_YEAR)
        .exclude(status=TravelGrantApplication.STATUS_DRAFT)
        .select_related("user")
        .prefetch_related("assignments__reviewer")
        .order_by("-submitted_at")
    )

    status_filter = request.GET.get("status")
    country_filter = request.GET.get("country")
    assigned_to_me = request.GET.get("assigned_to_me") == "1"
    if status_filter:
        applications = applications.filter(status=status_filter)
    if country_filter:
        applications = applications.filter(country_of_residence=country_filter)
    if assigned_to_me:
            applications = applications.filter(assignments__reviewer=request.user).distinct()

    countries = (
        TravelGrantApplication.objects.filter(conference_year=CURRENT_YEAR)
        .exclude(status=TravelGrantApplication.STATUS_DRAFT)
        .values_list("country_of_residence", flat=True)
        .distinct()
        .order_by("country_of_residence")
    )

    reviewers = users_with_role(Role.GRANT_REVIEWER, CURRENT_YEAR)

    return render(request, "grants/admin_dashboard.html", {
        "stats": stats,
        "grant_settings": settings_obj,
        "applications": applications,
        "countries": countries,
        "status_filter": status_filter,
        "country_filter": country_filter,
        "assigned_to_me": assigned_to_me,
        "reviewers": reviewers,
        "conference_year": CURRENT_YEAR,
        "status_choices": TravelGrantApplication.STATUS_CHOICES,
    })


@grant_chair_required
def grant_admin_detail(request, application_id):
    """Chair view: full application, reviews, decision form."""
    application = get_object_or_404(
        TravelGrantApplication,
        pk=application_id,
        conference_year=CURRENT_YEAR,
    )
    cfp_info = GrantService.get_user_cfp_info(application.user)
    assignments = application.assignments.select_related("reviewer").prefetch_related("review")
    return render(request, "grants/admin_detail.html", {
        "application": application,
        "cfp_info": cfp_info,
        "assignments": assignments,
        "conference_year": CURRENT_YEAR,
    })


@require_POST
@grant_chair_required
def grant_admin_assign(request):
    """Assign reviewers to applications."""
    from .forms import GrantAssignReviewerForm

    form = GrantAssignReviewerForm(request.POST)
    if form.is_valid():
        app_ids = form.cleaned_data["application_ids"]
        reviewer_ids = form.cleaned_data["reviewer_ids"]
        reviewers = users_with_role(Role.GRANT_REVIEWER, CURRENT_YEAR).filter(pk__in=reviewer_ids)
        created = 0
        for app in TravelGrantApplication.objects.filter(pk__in=app_ids):
            for rev in reviewers:
                _, c = GrantReviewerAssignment.objects.get_or_create(
                    application=app,
                    reviewer=rev,
                )
                if c:
                    created += 1
            if app.status == TravelGrantApplication.STATUS_SUBMITTED:
                app.status = TravelGrantApplication.STATUS_UNDER_REVIEW
                app.save(update_fields=["status", "updated_at"])
        messages.success(request, f"Created {created} assignment(s).")
    else:
        messages.error(request, "Invalid assignment data.")
    return redirect("grants:admin_dashboard")


@require_POST
@grant_chair_required
def grant_admin_decisions(request):
    """Bulk approve / reject / waitlist."""
    form = GrantBulkDecisionForm(request.POST)
    if form.is_valid():
        app_ids = form.cleaned_data["application_ids"]
        decision = form.cleaned_data["decision"]
        approved_amounts = {}
        for aid in app_ids:
            key = f"amount_{aid}"
            if key in request.POST and request.POST[key]:
                try:
                    approved_amounts[aid] = float(request.POST[key])
                except (ValueError, TypeError):
                    pass
        count = GrantService.bulk_decision(app_ids, decision, approved_amounts, request.user.email)
        messages.success(request, f"{count} application(s) updated.")
    else:
        messages.error(request, "Invalid decision data.")
    return redirect("grants:admin_dashboard")


@grant_chair_required
def grant_admin_export(request):
    """Export approved grants as CSV or JSON."""
    fmt = request.GET.get("format", "csv")
    content = GrantService.export_approved_grants(fmt=fmt)
    if fmt == "json":
        response = HttpResponse(content, content_type="application/json")
        response["Content-Disposition"] = f'attachment; filename="pycon_ng_{CURRENT_YEAR}_grants.json"'
    else:
        response = HttpResponse(content, content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="pycon_ng_{CURRENT_YEAR}_grants.csv"'
    return response

@grant_finance_required
def grant_finance_list(request):
    """List approved grants for payment processing."""
    applications = (
        TravelGrantApplication.objects.filter(
            conference_year=CURRENT_YEAR,
            status__in=[TravelGrantApplication.STATUS_APPROVED, TravelGrantApplication.STATUS_PAID],
        )
        .select_related("user")
        .prefetch_related("payment")
    )
    # Ensure payment records exist for each approved application
    for app in applications:
        TravelGrantPayment.objects.get_or_create(
            application=app,
            defaults={"amount_paid": app.approved_amount, "payment_status": TravelGrantPayment.STATUS_PENDING},
        )
    return render(request, "grants/finance_list.html", {
        "applications": applications,
        "conference_year": CURRENT_YEAR,
    })


@grant_finance_required
def grant_finance_detail(request, application_id):
    """Mark payment status, upload receipt."""
    application = get_object_or_404(
        TravelGrantApplication,
        pk=application_id,
        conference_year=CURRENT_YEAR,
        status__in=[TravelGrantApplication.STATUS_APPROVED, TravelGrantApplication.STATUS_PAID],
    )
    payment, _ = TravelGrantPayment.objects.get_or_create(
        application=application,
        defaults={"amount_paid": application.approved_amount},
    )

    if request.method == "POST":
        form = GrantPaymentForm(request.POST, request.FILES)
        if form.is_valid():
            payment.payment_status = form.cleaned_data["payment_status"]
            if form.cleaned_data.get("amount_paid"):
                payment.amount_paid = form.cleaned_data["amount_paid"]
            payment.reference = form.cleaned_data.get("reference", "")
            if form.cleaned_data["payment_status"] == TravelGrantPayment.STATUS_PAID:
                payment.paid_at = timezone.now()
                application.status = TravelGrantApplication.STATUS_PAID
                application.save(update_fields=["status", "updated_at"])
            if form.cleaned_data.get("receipt"):
                payment.receipt = form.cleaned_data["receipt"]
            payment.save()
            messages.success(request, "Payment record updated.")
            return redirect("grants:finance_list")
    else:
        form = GrantPaymentForm(initial={
            "payment_status": payment.payment_status,
            "amount_paid": payment.amount_paid,
            "reference": payment.reference,
        })

    return render(request, "grants/finance_detail.html", {
        "application": application,
        "payment": payment,
        "form": form,
        "conference_year": CURRENT_YEAR,
    })
