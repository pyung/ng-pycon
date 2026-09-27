from django.http import JsonResponse, HttpResponseForbidden
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.views import View
from django.views.generic import FormView
from django.contrib.auth import login, logout
from django.contrib.auth.views import LoginView as DjangoLoginView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import TemplateView
from editions.current import current_year
import json
from .models import NewsletterSubscriber
from .forms import SignupForm


@method_decorator(csrf_exempt, name='dispatch')
class NewsletterSignupView(View):
    """Handle newsletter signup via AJAX."""
    
    def post(self, request):
        try:
            data = json.loads(request.body)
            email = data.get('email', '').strip().lower()
            
            if not email:
                return JsonResponse({
                    'success': False,
                    'message': 'Email is required.'
                }, status=400)
            
            # Check if already subscribed
            subscriber, created = NewsletterSubscriber.objects.get_or_create(
                email=email,
                defaults={'is_active': True}
            )
            
            if not created:
                if subscriber.is_active:
                    return JsonResponse({
                        'success': False,
                        'message': 'This email is already subscribed.'
                    }, status=400)
                else:
                    # Reactivate
                    subscriber.is_active = True
                    subscriber.save()
                    return JsonResponse({
                        'success': True,
                        'message': 'Successfully resubscribed to newsletter!'
                    })
            
            return JsonResponse({
                'success': True,
                'message': 'Successfully subscribed to newsletter!'
            })
            
        except json.JSONDecodeError:
            return JsonResponse({
                'success': False,
                'message': 'Invalid request data.'
            }, status=400)
        except Exception as e:
            return JsonResponse({
                'success': False,
                'message': 'An error occurred. Please try again.'
            }, status=500)


class SignupView(FormView):
    """User signup view. Only works for current year."""
    template_name = 'home/signup.html'
    form_class = SignupForm
    success_url = '/'
    
    def dispatch(self, request, *args, **kwargs):
        """Block signup for archived years."""
        # Check if we're on an archived year URL
        path = request.path
        if path.startswith('/') and len(path) > 1:
            parts = path.strip('/').split('/')
            if parts[0].isdigit() and int(parts[0]) != current_year():
                return HttpResponseForbidden("Authentication is only available for the current conference year.")
        return super().dispatch(request, *args, **kwargs)
    
    def form_valid(self, form):
        user = form.save()
        login(self.request, user)
        return redirect(self.success_url)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['page_title'] = 'Sign Up'
        return context


class LoginView(DjangoLoginView):
    """User login view. Only works for current year."""
    template_name = 'home/login.html'
    redirect_authenticated_user = True
    
    def dispatch(self, request, *args, **kwargs):
        """Block login for archived years."""
        # Check if we're on an archived year URL
        path = request.path
        if path.startswith('/') and len(path) > 1:
            parts = path.strip('/').split('/')
            if parts[0].isdigit() and int(parts[0]) != current_year():
                return HttpResponseForbidden("Authentication is only available for the current conference year.")
        return super().dispatch(request, *args, **kwargs)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['page_title'] = 'Sign In'
        # Django's LoginView puts the *host name* in `site_name`, and its view
        # context beats our site_context processor. The navigation brand and the
        # page title both read that key, so the sign-in page was headed
        # "testserver" locally and the bare hostname in production.
        context['site_name'] = 'PyCon Nigeria'
        # Customize form field labels
        if 'form' in context:
            context['form'].fields['username'].label = 'Email'
            context['form'].fields['username'].widget.attrs.update({
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-theme-primary focus:border-transparent',
                'placeholder': 'your.email@example.com',
                'type': 'email'
            })
            context['form'].fields['password'].widget.attrs.update({
                'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-theme-primary focus:border-transparent',
                'placeholder': 'Enter your password'
            })
        return context


class LogoutView(LoginRequiredMixin, TemplateView):
    """User logout view with confirmation. Only works for current year."""
    template_name = 'home/logout.html'
    
    def dispatch(self, request, *args, **kwargs):
        """Block logout page for archived years."""
        # Check if we're on an archived year URL
        path = request.path
        if path.startswith('/') and len(path) > 1:
            parts = path.strip('/').split('/')
            if parts[0].isdigit() and int(parts[0]) != current_year():
                return HttpResponseForbidden("Authentication is only available for the current conference year.")
        return super().dispatch(request, *args, **kwargs)
    
    def post(self, request):
        """Handle logout confirmation."""
        logout(request)
        return redirect('/')
    
    def get(self, request):
        """Show logout confirmation page."""
        return super().get(request)
