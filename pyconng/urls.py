from django.conf import settings
from django.urls import include, path
from django.contrib import admin
from django.contrib.auth import views as auth_views

from wagtail.admin import urls as wagtailadmin_urls
from wagtail import urls as wagtail_urls
from wagtail.documents import urls as wagtaildocs_urls

from search import views as search_views
from pyconng.views import year_page_serve
from home.views import NewsletterSignupView, SignupView, LoginView, LogoutView

ADMIN_PREFIX = getattr(settings, 'ADMIN_URL_PREFIX', 'admin')

urlpatterns = [
    path(f"{ADMIN_PREFIX}-djadmin/", admin.site.urls),
    path(f"{ADMIN_PREFIX}/", include(wagtailadmin_urls)),
    path("documents/", include(wagtaildocs_urls)),
    path("search/", search_views.search, name="search"),
    path("newsletter/signup/", NewsletterSignupView.as_view(), name="newsletter_signup"),
    
    # Authentication URLs
    path("accounts/signup/", SignupView.as_view(), name="signup"),
    path("accounts/login/", LoginView.as_view(), name="login"),
    path("accounts/logout/", LogoutView.as_view(), name="logout"),

    # Password reset (uses Django's built-in views)
    path(
        "accounts/password_reset/",
        auth_views.PasswordResetView.as_view(
            email_template_name="registration/password_reset_email.txt",
            html_email_template_name="registration/password_reset_email.html",
            subject_template_name="registration/password_reset_subject.txt",
        ),
        name="password_reset",
    ),
    path(
        "accounts/password_reset/done/",
        auth_views.PasswordResetDoneView.as_view(),
        name="password_reset_done",
    ),
    path(
        "accounts/reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "accounts/reset/done/",
        auth_views.PasswordResetCompleteView.as_view(),
        name="password_reset_complete",
    ),

    # Tickets (must be before Wagtail catch-all)
    path("tickets/", include("tickets.urls")),
    
    # CFP (must be before Wagtail catch-all)
    path("cfp/", include("cfp.urls")),

    # Travel Grants (must be before Wagtail catch-all)
    path("grants/", include("grants.urls")),

    # Dashboard (login required, current year only)
    path("dashboard/", include("dashboard.urls")),

    # Programme actions (must be before Wagtail catch-all)
    path("programme/", include("program.urls")),
    
    # Year-specific routing using custom view. Past years are read-only snapshots.
    path("<int:year>/search/", search_views.search, name="year_search"),
    path("<int:year>/", year_page_serve, {'path': ''}, name="year_home"),
    path("<int:year>/<path:path>/", year_page_serve, name="year_page"),
    
    # Root URL serves current year content directly (no redirect)
    path("", include(wagtail_urls)),
]

from django.urls import re_path
from django.views.static import serve

# Serve media files in all environments (Whitenoise handles static files)
urlpatterns += [
    re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT}),
]

if settings.DEBUG:
    from django.conf.urls.static import static
    from django.contrib.staticfiles.urls import staticfiles_urlpatterns
    urlpatterns += staticfiles_urlpatterns()
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
