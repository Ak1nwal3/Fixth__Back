from django.urls import path

from accounts.views import (
    MeView,
    OTPRequestView,
    OTPVerifyView,
    OrganizationAuthMeView,
    OrganizationPasswordResetConfirmView,
    OrganizationPasswordResetRequestView,
    OrganizationSignInView,
    OrganizationSignOutView,
    OrganizationTokenRefreshView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    SignInView,
    SignOutView,
    StudentAuthMeView,
    StudentOTPRequestView,
    StudentOTPVerifyView,
    StudentPasswordResetConfirmView,
    StudentPasswordResetRequestView,
    StudentSignInView,
    StudentSignOutView,
    StudentTokenRefreshView,
    ThrottledTokenRefreshView,
)

urlpatterns = [
    # Administrator/staff authentication (generic auth routes).
    path("auth/sign-in/", SignInView.as_view(), name="auth-sign-in"),
    path(
        "auth/refresh/",
        ThrottledTokenRefreshView.as_view(),
        name="auth-refresh",
    ),
    path("auth/sign-out/", SignOutView.as_view(), name="auth-sign-out"),
    path(
        "auth/password-reset/",
        PasswordResetRequestView.as_view(),
        name="auth-password-reset",
    ),
    path(
        "auth/password-reset/confirm/",
        PasswordResetConfirmView.as_view(),
        name="auth-password-reset-confirm",
    ),
    path("auth/me/", MeView.as_view(), name="auth-me"),
    # Student authentication.
    path(
        "auth/student/sign-in/",
        StudentSignInView.as_view(),
        name="student-auth-sign-in",
    ),
    path(
        "auth/student/refresh/",
        StudentTokenRefreshView.as_view(),
        name="student-auth-refresh",
    ),
    path(
        "auth/student/sign-out/",
        StudentSignOutView.as_view(),
        name="student-auth-sign-out",
    ),
    path(
        "auth/student/password-reset/",
        StudentPasswordResetRequestView.as_view(),
        name="student-auth-password-reset",
    ),
    path(
        "auth/student/password-reset/confirm/",
        StudentPasswordResetConfirmView.as_view(),
        name="student-auth-password-reset-confirm",
    ),
    path(
        "auth/student/me/",
        StudentAuthMeView.as_view(),
        name="student-auth-me",
    ),
    # Organization authentication.
    path(
        "auth/organization/sign-in/",
        OrganizationSignInView.as_view(),
        name="organization-auth-sign-in",
    ),
    path(
        "auth/organization/refresh/",
        OrganizationTokenRefreshView.as_view(),
        name="organization-auth-refresh",
    ),
    path(
        "auth/organization/sign-out/",
        OrganizationSignOutView.as_view(),
        name="organization-auth-sign-out",
    ),
    path(
        "auth/organization/password-reset/",
        OrganizationPasswordResetRequestView.as_view(),
        name="organization-auth-password-reset",
    ),
    path(
        "auth/organization/password-reset/confirm/",
        OrganizationPasswordResetConfirmView.as_view(),
        name="organization-auth-password-reset-confirm",
    ),
    path(
        "auth/organization/me/",
        OrganizationAuthMeView.as_view(),
        name="organization-auth-me",
    ),
    # Shared onboarding code endpoints.
    path("auth/otp/request/", OTPRequestView.as_view(), name="auth-otp-request"),
    path("auth/otp/verify/", OTPVerifyView.as_view(), name="auth-otp-verify"),
    path(
        "onboarding/students/request-otp/",
        StudentOTPRequestView.as_view(),
        name="student-onboarding-request-otp",
    ),
    path(
        "onboarding/students/verify-otp/",
        StudentOTPVerifyView.as_view(),
        name="student-onboarding-verify-otp",
    ),
]
