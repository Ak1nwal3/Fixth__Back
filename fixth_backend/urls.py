"""
URL configuration for fixth_backend project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)
from common.debug_views import SmtpDebugView

urlpatterns = [
    path('admin/', admin.site.urls),

    # OpenAPI schema and interactive documentation.
    path('api/v1/schema/', SpectacularAPIView.as_view(), name='schema'),
    path(
        'api/v1/docs/',
        SpectacularSwaggerView.as_view(url_name='schema'),
        name='swagger-ui',
    ),
    path(
        'api/v1/redoc/',
        SpectacularRedocView.as_view(url_name='schema'),
        name='redoc',
    ),

    # Canonical versioned API: auth, onboarding, profiles, placements, and applications.
    path('api/v1/', include('accounts.urls')),
    path('api/v1/', include('profiles.urls')),
    path('api/v1/', include('companies.urls')),
    path('api/v1/', include('placements.urls')),
    path('api/v1/', include('applications.urls')),
    path('api/v1/', include('common.urls')),

    # Temporary diagnostic: outbound SMTP/HTTPS connectivity probe. Token-gated.
    path('api/v1/debug-smtp/', SmtpDebugView.as_view(), name='debug-smtp'),

    # Convenience: send the project root to the Swagger UI.
    path('', RedirectView.as_view(url='/api/v1/docs/', permanent=False)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
