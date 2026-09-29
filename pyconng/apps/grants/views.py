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
from django.views.decorators.http import require_POST, require_http_methods

from editions.current import current_year

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
from audit.services import record

from .emails import send_grant_payment_sent

from .services import GrantService


def grant_landing(request):
    """Landing page - status badge and Apply CTA when open."""
    settings_obj = GrantService.get_current_settings()
    return render(request, "grants/landing.html", {
        "grant_settings": settings_obj,
        "conference_year": current_year(),
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
            app.conference_year = current_year()
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
        "conference_year": current_year(),
        "user": request.user,
    })


def grant_closed(request):
    """Notice that applications are closed."""
    settings_obj = GrantService.get_current_settings()
    return render(request, "grants/closed.html", {
        "grant_settings": settings_obj,
        "conference_year": current_year(),
    })


@login_required
def grant_my_application(request):
    """
    Where an applicant sees where they stand, and answers an offer.

    Everything that used to require an email now happens here: accept, decline, say
    where the money should go, and send the receipt.
    """
    from . import offers, payouts
    from .forms import GrantPayoutDetailsForm, GrantReceiptForm

    application = GrantService.get_user_application(request.user)
    context = {
        "application": application,
        "conference_year": current_year(),
        "grant_open": GrantService.is_grant_open(),
    }
    if application is not None:
        payment = getattr(application, "payment", None)
        context.update({
            "payment": payment,
            "ticket": offers.grant_ticket(application),
            "payout_form": GrantPayoutDetailsForm(instance=application),
            "receipt_form": GrantReceiptForm(),
            "needs_payout_details": application.is_awarded and not application.has_payout_details,
            "needs_receipt": bool(payment and payment.needs_receipt),
        })
    return render(request, "grants/my_application.html", context)


@login_required
@require_POST
def grant_accept(request, application_id):
    """The recipient says yes, which fixes the money and issues their ticket."""
    from . import offers

    application = get_object_or_404(
        TravelGrantApplication, pk=application_id, user=request.user
    )
    try:
        offers.accept(application, by=request.user)
    except offers.OfferError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            "Your travel grant is confirmed, and your conference ticket has been "
            "issued. Please add your bank details below so we can pay you.",
        )
    return redirect("grants:my_application")


@login_required
@require_http_methods(["GET", "POST"])
def grant_decline(request, application_id):
    """
    The recipient says no, which releases the money to the waiting list.

    A page of its own rather than a button, because it asks for a reason and because
    it is irreversible — worth one deliberate step.
    """
    from . import offers
    from .forms import GrantOfferResponseForm

    application = get_object_or_404(
        TravelGrantApplication, pk=application_id, user=request.user
    )
    if not application.can_decline:
        messages.error(request, "There is no offer to decline.")
        return redirect("grants:my_application")

    if request.method == "POST":
        form = GrantOfferResponseForm(request.POST)
        if form.is_valid():
            try:
                _, promoted = offers.decline(
                    application, by=request.user, reason=form.cleaned_data["reason"]
                )
            except offers.OfferError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(
                    request,
                    "Thank you for telling us. Your grant has been released"
                    + (
                        " and offered to somebody on the waiting list."
                        if promoted
                        else "."
                    ),
                )
                return redirect("grants:my_application")
    else:
        form = GrantOfferResponseForm()

    return render(request, "grants/decline.html", {
        "application": application,
        "form": form,
        "conference_year": current_year(),
    })


@login_required
@require_POST
def grant_payout_details(request, application_id):
    """Record where to send the money."""
    from . import payouts
    from .forms import GrantPayoutDetailsForm

    application = get_object_or_404(
        TravelGrantApplication, pk=application_id, user=request.user
    )
    if not application.is_awarded:
        messages.error(request, "There is nothing to pay out yet.")
        return redirect("grants:my_application")

    form = GrantPayoutDetailsForm(request.POST, instance=application)
    if form.is_valid():
        payouts.save_payout_details(application, form.cleaned_data, by=request.user)
        messages.success(request, "Thank you — we have your payment details.")
    else:
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, error)
    return redirect("grants:my_application")


@login_required
@require_POST
def grant_upload_receipt(request, application_id):
    """The recipient sends evidence of what they spent."""
    from . import payouts
    from .forms import GrantReceiptForm

    application = get_object_or_404(
        TravelGrantApplication, pk=application_id, user=request.user
    )
    form = GrantReceiptForm(request.POST, request.FILES)
    if not form.is_valid():
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, error)
        return redirect("grants:my_application")

    try:
        payouts.upload_receipt(application, form.cleaned_data["receipt"], by=request.user)
    except payouts.PayoutError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Receipt received. Thank you.")
    return redirect("grants:my_application")


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
        "conference_year": current_year(),
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
    """
    Applications assigned to this reviewer, filterable by what was asked for.

    The grant-type filter is the reason that field now exists. It used to be implied
    by which cost boxes happened to be filled in, so a queue of forty could not be
    split into "the flights" and "the hotel rooms" — which is how a panel actually
    divides the work.
    """
    grant_type = request.GET.get("type") or ""
    assignments = (
        GrantReviewerAssignment.objects.filter(reviewer=request.user)
        .select_related("application__user")
        .prefetch_related("review")
        .order_by("-assigned_at")
    )
    if grant_type:
        assignments = assignments.filter(application__grant_type=grant_type)

    return render(request, "grants/review_list.html", {
        "assignments": assignments,
        "grant_types": TravelGrantApplication.GRANT_TYPE_CHOICES,
        "selected_type": grant_type,
        "conference_year": current_year(),
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
        "conference_year": current_year(),
    })

@grant_chair_required
def grant_admin_dashboard(request):
    """Admin dashboard: stats, filters, application list."""
    stats = GrantService.get_dashboard_stats()
    settings_obj = GrantService.get_current_settings()

    applications = (
        TravelGrantApplication.objects.filter(conference_year=current_year())
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
        TravelGrantApplication.objects.filter(conference_year=current_year())
        .exclude(status=TravelGrantApplication.STATUS_DRAFT)
        .values_list("country_of_residence", flat=True)
        .distinct()
        .order_by("country_of_residence")
    )

    reviewers = users_with_role(Role.GRANT_REVIEWER, current_year())

    return render(request, "grants/admin_dashboard.html", {
        "stats": stats,
        "grant_settings": settings_obj,
        "applications": applications,
        "countries": countries,
        "status_filter": status_filter,
        "country_filter": country_filter,
        "assigned_to_me": assigned_to_me,
        "reviewers": reviewers,
        "conference_year": current_year(),
        "status_choices": TravelGrantApplication.STATUS_CHOICES,
    })


@grant_chair_required
def grant_admin_detail(request, application_id):
    """Chair view: full application, reviews, decision form."""
    application = get_object_or_404(
        TravelGrantApplication,
        pk=application_id,
        conference_year=current_year(),
    )
    cfp_info = GrantService.get_user_cfp_info(application.user)
    assignments = application.assignments.select_related("reviewer").prefetch_related("review")
    return render(request, "grants/admin_detail.html", {
        "application": application,
        "cfp_info": cfp_info,
        "assignments": assignments,
        "conference_year": current_year(),
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
        reviewers = users_with_role(Role.GRANT_REVIEWER, current_year()).filter(pk__in=reviewer_ids)
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
        count, refused = GrantService.bulk_decision(
            app_ids, decision, approved_amounts,
            actor_email=request.user.email, actor=request.user,
        )
        if count:
            messages.success(request, f"{count} application(s) updated.")
        # Reported per application rather than as one failure: a chair deciding
        # twenty needs to know which three did not fit the budget.
        for application, reason in refused:
            messages.error(
                request,
                f"{application.user.get_full_name() or application.user.email}: {reason}",
            )
        if not count and not refused:
            messages.info(request, "Nothing to update.")
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
        response["Content-Disposition"] = f'attachment; filename="pycon_ng_{current_year()}_grants.json"'
    else:
        response = HttpResponse(content, content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="pycon_ng_{current_year()}_grants.csv"'
    return response

@grant_finance_required
def grant_finance_list(request):
    """
    Grants to pay: accepted ones, not merely approved ones.

    Approved used to be the trigger, which stopped being right once an approval
    became an offer somebody has to answer. Paying out an unanswered offer sends
    money to somebody who may yet decline.
    """
    applications = (
        TravelGrantApplication.objects.filter(
            conference_year=current_year(),
            status__in=TravelGrantApplication.PAYABLE_STATUSES,
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
        "conference_year": current_year(),
    })


@grant_finance_required
def grant_finance_detail(request, application_id):
    """Mark payment status, upload receipt."""
    application = get_object_or_404(
        TravelGrantApplication,
        pk=application_id,
        conference_year=current_year(),
        status__in=TravelGrantApplication.PAYABLE_STATUSES,
    )
    payment, _ = TravelGrantPayment.objects.get_or_create(
        application=application,
        defaults={"amount_paid": application.approved_amount},
    )

    if request.method == "POST":
        form = GrantPaymentForm(request.POST, request.FILES)
        if form.is_valid():
            previous_payment_status = payment.payment_status
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

            if (
                payment.payment_status == TravelGrantPayment.STATUS_PAID
                and previous_payment_status != TravelGrantPayment.STATUS_PAID
            ):
                send_grant_payment_sent(application, payment)

            record(
                target=application,
                action="Marked travel grant payment "
                       f"{payment.get_payment_status_display().lower()}",
                actor=request.user,
                old_value=previous_payment_status,
                new_value=payment.payment_status,
                note=f"Amount: {payment.amount_paid} Reference: {payment.reference or '-'}",
            )
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
        "conference_year": current_year(),
    })


# ---------------------------------------------------------------------------
# Chair and finance: the budget, the waitlist, and the reports
# ---------------------------------------------------------------------------


@grant_chair_required
@require_POST
def grant_promote_waitlist(request):
    """
    Offer whatever budget is free to the best-scored waitlisted applicants.

    A deliberate action rather than something that happens quietly in the background:
    it commits money, and a chair should see how much and to whom.
    """
    from . import offers

    promoted = offers.promote_from_waitlist(current_year(), by=request.user)
    if promoted:
        names = ", ".join(a.user.get_full_name() or a.user.email for a in promoted)
        messages.success(
            request,
            f"{len(promoted)} offer(s) made from the waiting list: {names}. "
            f"Each has its own acceptance deadline.",
        )
    else:
        messages.info(
            request,
            "Nothing to promote. Either the waiting list is empty, the remaining "
            "budget does not cover anybody on it, or automatic promotion is off for "
            "this edition.",
        )
    return redirect("grants:admin_dashboard")


@grant_chair_required
@require_POST
def grant_expire_offers(request):
    """Lapse every offer past its deadline, releasing the money."""
    from . import offers

    lapsed, promoted = offers.expire_overdue(current_year(), by=request.user)
    if lapsed:
        messages.success(
            request,
            f"{len(lapsed)} offer(s) lapsed and their budget released."
            + (f" {len(promoted)} new offer(s) made from the waiting list." if promoted else ""),
        )
    else:
        messages.info(request, "No offers are past their deadline.")
    return redirect("grants:admin_dashboard")


@grant_chair_required
def grant_reports(request):
    """
    The breakdowns sponsors and the PSF ask for.

    Gender is shown here and nowhere else in the module: a breakdown for a funder is a
    legitimate use, and a reviewer seeing it while deciding is not.
    """
    from . import reporting

    year = current_year()
    return render(request, "grants/reports.html", {
        "report": reporting.summary(year),
        "conference_year": year,
    })


@grant_chair_required
def grant_reports_export(request):
    """The same breakdowns as a CSV."""
    from . import reporting

    year = current_year()
    response = HttpResponse(reporting.to_csv(year), content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="pyconng-{year}-grant-report.csv"'
    )
    return response


@grant_finance_required
@require_POST
def grant_verify_receipt(request, application_id):
    """Finance confirms a receipt matches what was paid."""
    from . import payouts

    application = get_object_or_404(
        TravelGrantApplication, pk=application_id, conference_year=current_year()
    )
    try:
        payouts.verify_receipt(application, by=request.user)
    except payouts.PayoutError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            f"Receipt verified for {application.user.get_full_name() or application.user.email}.",
        )
    return redirect("grants:finance_detail", application_id=application.pk)
