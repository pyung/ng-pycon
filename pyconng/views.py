from django.shortcuts import render, redirect
from django.http import Http404, HttpResponse
from django.conf import settings
from django.template.loader import get_template
from wagtail.models import Page, Site
from home.models import HomePage
from editions.current import current_year
import os

ARCHIVE_DIR = os.path.join(settings.BASE_DIR, 'archives')


def _try_serve_static_archive(year: int, path: str):
    """Return HttpResponse for a static archived HTML if present; else None.
    Looks for files in BASE_DIR/archives/<year>/<path or index>.html
    """
    # Normalize path -> file path
    rel_path = path.strip('/')
    if rel_path == '':
        rel_path = 'index'
    # ensure .html
    if not rel_path.endswith('.html'):
        rel_path = f"{rel_path}.html"

    candidate = os.path.join(ARCHIVE_DIR, str(year), rel_path)
    if os.path.isfile(candidate):
        try:
            with open(candidate, 'r', encoding='utf-8') as f:
                html = f.read()
            return HttpResponse(html)
        except Exception:
            return None
    return None


def year_page_serve(request, year, path=''):
    """
    Custom view to handle year-based routing for Wagtail pages.
    
    Priority order:
    1. If current year, redirect to root (current year should not be accessed via /year)
    2. Check for HomePage with slug matching the year OR conference_year field matching
    3. Check for static archive files
    4. Raise 404 (no fallback to current homepage for archived years)
    """
    # 1) Prevent accessing current year via /year URL - redirect to root
    if year == current_year():
        redirect_path = f"/{path}" if path else "/"
        return redirect(redirect_path, permanent=False)
    
    # 2) Try to find a HomePage for this year
    try:
        site = Site.find_for_request(request)
        if not site:
            raise Http404("No site configured for this request")
        
        # Look for HomePage instances with matching year
        # Could be direct children of root or nested under another HomePage
        year_homepage = (
            HomePage.objects
            .live()
            .filter(slug=str(year))
            .first()
        )
        
        # Also try matching by conference_year field
        if not year_homepage:
            year_homepage = (
                HomePage.objects
                .live()
                .filter(conference_year=year)
                .first()
            )
        
        if year_homepage:
            year_homepage = year_homepage.specific
            
            # If there's a path, try to route within the year homepage's children
            if path:
                # Try to route the path within the year homepage page
                path_parts = [p for p in path.split('/') if p]
                routed = year_homepage.route(request, path_parts)
                if routed:
                    page, args, kwargs = routed
                    if page.live:
                        return page.serve(request, *args, **kwargs)
                # If routing failed, raise 404
                raise Http404(f"Page not found under /{year}/{path}")
            else:
                # Serve the year homepage
                return year_homepage.serve(request)
    
    except Http404:
        raise
    except Exception as e:
        # If there was an error finding the year archive, continue to next option
        pass
    
    # 3) Try static archive files (read-only past events)
    archive_response = _try_serve_static_archive(year, path)
    if archive_response is not None:
        return archive_response
    
    # 4) No archive found - raise 404
    raise Http404(f"No archive found for year {year}. Please create a HomePage in Wagtail admin with slug '{year}' or conference_year={year}.") 
    