from django.urls import path

from companies.views import (
    CompanyProfileView,
    CompanySummaryView,
    OrganizationOnboardingCompleteView,
    PublicCompanyDetailView,
)

urlpatterns = [
    path(
        "companies/me/",
        CompanyProfileView.as_view(),
        name="company-profile",
    ),
    path(
        "company/summary/",
        CompanySummaryView.as_view(),
        name="company-summary",
    ),
    path(
        "companies/<slug:slug>/",
        PublicCompanyDetailView.as_view(),
        name="public-company-detail",
    ),
    path(
        "onboarding/organizations/complete/",
        OrganizationOnboardingCompleteView.as_view(),
        name="organization-onboarding-complete",
    ),
]
