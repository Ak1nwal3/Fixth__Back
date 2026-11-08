from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from accounts.models import User
from accounts.permissions import (
    IsAdminAccount,
    IsVerifiedCompany,
    IsVerifiedStudent,
)
from accounts.serializers import (
    AuthTokensSerializer,
    DetailSerializer,
    MeSerializer,
    MeUpdateSerializer,
    OTPRequestResponseSerializer,
    OTPRequestSerializer,
    OTPVerifyResponseSerializer,
    OTPVerifySerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    SignInResponseSerializer,
    SignInSerializer,
    SignOutSerializer,
    StudentOTPRequestSerializer,
    StudentOTPVerifySerializer,
    StudentPasswordResetRequestSerializer,
    StudentSignInSerializer,
)
from accounts.services import (
    make_onboarding_verification_token,
    request_onboarding_otp,
    request_student_otp,
    send_password_reset_email,
    verify_onboarding_otp,
    verify_student_otp,
)


def _role_matches(user, expected_role: str) -> bool:
    if expected_role == User.Role.ADMIN:
        return bool(user.is_admin)
    return user.role == expected_role


def _can_authenticate(user, expected_role: str) -> bool:
    return bool(
        user
        and user.is_active
        and user.account_status == User.AccountStatus.ACTIVE
        and _role_matches(user, expected_role)
        and user.email_verified_at is not None
    )


def _refresh_user(refresh: RefreshToken):
    user_id = refresh.get(jwt_settings.USER_ID_CLAIM)
    if not user_id:
        return None
    return User.objects.filter(pk=user_id).first()


@extend_schema(
    tags=["onboarding"],
    summary="Request a student email verification code",
    description=(
        "Sends a one-time code to a valid institutional email ending in "
        ".edu.ng. The response is intentionally generic so it cannot be used "
        "to enumerate accounts."
    ),
    request=StudentOTPRequestSerializer,
    responses={
        202: DetailSerializer,
        400: DetailSerializer,
        429: DetailSerializer,
    },
)
class StudentOTPRequestView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = "onboarding_request"

    def post(self, request):
        serializer = StudentOTPRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        demo_code = request_student_otp(serializer.validated_data["email"], request=request)
        response = {
            "detail": (
                "If the address is eligible, a verification code has been sent."
            )
        }
        if demo_code is not None:
            response["otp_code"] = demo_code
        return Response(response, status=status.HTTP_202_ACCEPTED)


@extend_schema(
    tags=["onboarding"],
    summary="Verify a student email verification code",
    description=(
        "Checks a code without consuming it. The code is consumed only when "
        "student onboarding is completed."
    ),
    request=StudentOTPVerifySerializer,
    responses={
        200: DetailSerializer,
        400: DetailSerializer,
        429: DetailSerializer,
    },
)
class StudentOTPVerifyView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = "onboarding_verify"

    def post(self, request):
        serializer = StudentOTPVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        otp = verify_student_otp(
            serializer.validated_data["email"],
            serializer.validated_data["code"],
        )
        if otp is None:
            return Response(
                {"detail": "Invalid or expired verification code."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {"detail": "Email verified. Complete your profile to continue."},
            status=status.HTTP_200_OK,
        )


@extend_schema(
    tags=["auth"],
    summary="Request an onboarding verification code",
    description=(
        "Sends a one-time code for the student or organization onboarding "
        "track. The response is intentionally generic so it cannot be used to "
        "enumerate accounts."
    ),
    request=OTPRequestSerializer,
    responses={
        202: OTPRequestResponseSerializer,
        400: DetailSerializer,
        429: DetailSerializer,
    },
)
class OTPRequestView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = "onboarding_request"

    def post(self, request):
        serializer = OTPRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        demo_code = request_onboarding_otp(
            serializer.validated_data["email"],
            serializer.validated_data["track"],
            request=request,
        )
        response = {
            "detail": (
                "If the address is eligible, a verification code has been sent."
            ),
            "expires_in": 600,
            "resend_after": 60,
        }
        if demo_code is not None:
            response["otp_code"] = demo_code
        return Response(response, status=status.HTTP_202_ACCEPTED)


@extend_schema(
    tags=["auth"],
    summary="Verify an onboarding email code",
    description=(
        "Checks a one-time code and returns a short-lived signed verification "
        "token. The token proves email ownership without exposing the code and "
        "is consumed when onboarding is completed."
    ),
    request=OTPVerifySerializer,
    responses={
        200: OTPVerifyResponseSerializer,
        400: DetailSerializer,
        429: DetailSerializer,
    },
)
class OTPVerifyView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = "onboarding_verify"

    def post(self, request):
        serializer = OTPVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        otp = verify_onboarding_otp(
            serializer.validated_data["email"],
            serializer.validated_data["track"],
            serializer.validated_data["code"],
        )
        if otp is None:
            return Response(
                {"detail": "Invalid or expired verification code."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {
                "verification_token": make_onboarding_verification_token(otp),
                "is_new_user": not User.objects.filter(
                    email__iexact=serializer.validated_data["email"]
                ).exists(),
            },
            status=status.HTTP_200_OK,
        )


class RoleSignInView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = "auth"
    expected_role = None
    serializer_class = SignInSerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(
            request,
            username=serializer.validated_data["email"],
            password=serializer.validated_data["password"],
        )
        if (
            user is None
            or not user.is_active
            or user.account_status != User.AccountStatus.ACTIVE
            or not _role_matches(user, self.expected_role)
        ):
            return Response(
                {"detail": "Invalid email or password."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        if user.email_verified_at is None:
            return Response(
                {"detail": "Email verification is required before signing in."},
                status=status.HTTP_403_FORBIDDEN,
            )

        refresh = RefreshToken.for_user(user)
        payload = {
            "access": str(refresh.access_token),
            "refresh": str(refresh),
            "user": user,
        }
        return Response(
            SignInResponseSerializer(payload, context={"request": request}).data
        )


@extend_schema(
    tags=["student-auth"],
    summary="Sign in as a student",
    request=StudentSignInSerializer,
    responses={
        200: SignInResponseSerializer,
        401: DetailSerializer,
        403: DetailSerializer,
        429: DetailSerializer,
    },
)
class StudentSignInView(RoleSignInView):
    expected_role = User.Role.STUDENT
    serializer_class = StudentSignInSerializer


@extend_schema(
    tags=["organization-auth"],
    summary="Sign in as an organization",
    request=SignInSerializer,
    responses={
        200: SignInResponseSerializer,
        401: DetailSerializer,
        403: DetailSerializer,
        429: DetailSerializer,
    },
)
class OrganizationSignInView(RoleSignInView):
    expected_role = User.Role.COMPANY


@extend_schema(
    tags=["admin-auth"],
    summary="Sign in as an administrator",
    request=SignInSerializer,
    responses={
        200: SignInResponseSerializer,
        401: DetailSerializer,
        403: DetailSerializer,
        429: DetailSerializer,
    },
)
class SignInView(RoleSignInView):
    expected_role = User.Role.ADMIN


class RoleTokenRefreshView(TokenRefreshView):
    authentication_classes = []
    throttle_scope = "auth"
    expected_role = None

    def post(self, request, *args, **kwargs):
        raw_refresh = request.data.get("refresh")
        if isinstance(raw_refresh, str):
            try:
                refresh = RefreshToken(raw_refresh)
            except TokenError:
                return Response(
                    {"detail": "Invalid or expired refresh token."},
                    status=status.HTTP_401_UNAUTHORIZED,
                )
            if not _can_authenticate(
                _refresh_user(refresh),
                self.expected_role,
            ):
                return Response(
                    {"detail": "Invalid or expired refresh token."},
                    status=status.HTTP_401_UNAUTHORIZED,
                )
        return super().post(request, *args, **kwargs)


@extend_schema(
    tags=["student-auth"],
    summary="Refresh a student access token",
    description=(
        "Rotates a student refresh token and returns a new access/refresh pair. "
        "The previous refresh token is blacklisted."
    ),
    responses={200: AuthTokensSerializer, 401: DetailSerializer, 429: DetailSerializer},
)
class StudentTokenRefreshView(RoleTokenRefreshView):
    expected_role = User.Role.STUDENT


@extend_schema(
    tags=["organization-auth"],
    summary="Refresh an organization access token",
    description=(
        "Rotates an organization refresh token and returns a new access/refresh "
        "pair. The previous refresh token is blacklisted."
    ),
    responses={200: AuthTokensSerializer, 401: DetailSerializer, 429: DetailSerializer},
)
class OrganizationTokenRefreshView(RoleTokenRefreshView):
    expected_role = User.Role.COMPANY


@extend_schema(
    tags=["admin-auth"],
    summary="Refresh an administrator access token",
    description=(
        "Rotates an administrator refresh token and returns a new access/refresh "
        "pair. The previous refresh token is blacklisted."
    ),
    responses={200: AuthTokensSerializer, 401: DetailSerializer, 429: DetailSerializer},
)
class ThrottledTokenRefreshView(RoleTokenRefreshView):
    expected_role = User.Role.ADMIN


class RoleSignOutView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = "auth"
    expected_role = None

    def post(self, request):
        serializer = SignOutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            refresh = RefreshToken(serializer.validated_data["refresh"])
            if not _can_authenticate(
                _refresh_user(refresh),
                self.expected_role,
            ):
                raise TokenError
            refresh.blacklist()
        except TokenError:
            return Response(
                {"detail": "Invalid or expired refresh token."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(
    tags=["student-auth"],
    summary="Sign out a student",
    request=SignOutSerializer,
    responses={204: None, 401: DetailSerializer, 429: DetailSerializer},
)
class StudentSignOutView(RoleSignOutView):
    expected_role = User.Role.STUDENT


@extend_schema(
    tags=["organization-auth"],
    summary="Sign out an organization",
    request=SignOutSerializer,
    responses={204: None, 401: DetailSerializer, 429: DetailSerializer},
)
class OrganizationSignOutView(RoleSignOutView):
    expected_role = User.Role.COMPANY


@extend_schema(
    tags=["admin-auth"],
    summary="Sign out an administrator",
    request=SignOutSerializer,
    responses={204: None, 401: DetailSerializer, 429: DetailSerializer},
)
class SignOutView(RoleSignOutView):
    expected_role = User.Role.ADMIN


class RolePasswordResetRequestView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = "auth"
    expected_role = None
    serializer_class = PasswordResetRequestSerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        if self.expected_role == User.Role.ADMIN:
            role_filter = Q(role=User.Role.ADMIN) | Q(is_superuser=True)
        else:
            role_filter = Q(role=self.expected_role)
        user = (
            User.objects.filter(
                role_filter,
                email__iexact=serializer.validated_data["email"],
                is_active=True,
                account_status=User.AccountStatus.ACTIVE,
            )
            .only("id", "email", "password", "last_login", "is_active")
            .first()
        )
        if user is not None:
            send_password_reset_email(user)
        return Response(
            {"detail": "If an account exists, a password reset link has been sent."},
            status=status.HTTP_202_ACCEPTED,
        )


@extend_schema(
    tags=["student-auth"],
    summary="Request a student password reset",
    request=StudentPasswordResetRequestSerializer,
    responses={202: DetailSerializer, 429: DetailSerializer},
)
class StudentPasswordResetRequestView(RolePasswordResetRequestView):
    expected_role = User.Role.STUDENT
    serializer_class = StudentPasswordResetRequestSerializer


@extend_schema(
    tags=["organization-auth"],
    summary="Request an organization password reset",
    request=PasswordResetRequestSerializer,
    responses={202: DetailSerializer, 429: DetailSerializer},
)
class OrganizationPasswordResetRequestView(RolePasswordResetRequestView):
    expected_role = User.Role.COMPANY


@extend_schema(
    tags=["admin-auth"],
    summary="Request an administrator password reset",
    request=PasswordResetRequestSerializer,
    responses={202: DetailSerializer, 429: DetailSerializer},
)
class PasswordResetRequestView(RolePasswordResetRequestView):
    expected_role = User.Role.ADMIN


class RolePasswordResetConfirmView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = "auth"
    expected_role = None

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            user_id = force_str(
                urlsafe_base64_decode(serializer.validated_data["uid"])
            )
            user = User.objects.get(pk=user_id)
        except (
            TypeError,
            ValueError,
            OverflowError,
            UnicodeDecodeError,
            DjangoValidationError,
            User.DoesNotExist,
        ):
            return Response(
                {"detail": "Invalid or expired password reset token."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if (
            not _can_authenticate(user, self.expected_role)
            or not default_token_generator.check_token(
                user,
                serializer.validated_data["token"],
            )
        ):
            return Response(
                {"detail": "Invalid or expired password reset token."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        new_password = serializer.validated_data["new_password"]
        try:
            validate_password(new_password, user=user)
        except DjangoValidationError as exc:
            return Response(
                {
                    "detail": "Password does not meet requirements.",
                    "errors": {"new_password": exc.messages},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(new_password)
        user.save(update_fields=["password"])
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(
    tags=["student-auth"],
    summary="Confirm a student password reset",
    request=PasswordResetConfirmSerializer,
    responses={204: None, 400: DetailSerializer, 429: DetailSerializer},
)
class StudentPasswordResetConfirmView(RolePasswordResetConfirmView):
    expected_role = User.Role.STUDENT


@extend_schema(
    tags=["organization-auth"],
    summary="Confirm an organization password reset",
    request=PasswordResetConfirmSerializer,
    responses={204: None, 400: DetailSerializer, 429: DetailSerializer},
)
class OrganizationPasswordResetConfirmView(RolePasswordResetConfirmView):
    expected_role = User.Role.COMPANY


@extend_schema(
    tags=["admin-auth"],
    summary="Confirm an administrator password reset",
    request=PasswordResetConfirmSerializer,
    responses={204: None, 400: DetailSerializer, 429: DetailSerializer},
)
class PasswordResetConfirmView(RolePasswordResetConfirmView):
    expected_role = User.Role.ADMIN


class RoleMeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["auth"],
        summary="Get the authenticated account",
        responses={
            200: MeSerializer,
            401: DetailSerializer,
            403: DetailSerializer,
        },
    )
    def get(self, request):
        return Response(MeSerializer(request.user, context={"request": request}).data)

    @extend_schema(
        tags=["auth"],
        summary="Update the authenticated account",
        description=(
            "Updates the authenticated user's name or password. Email changes "
            "are rejected because they require a dedicated re-verification flow."
        ),
        request=MeUpdateSerializer,
        responses={
            200: MeSerializer,
            400: DetailSerializer,
            401: DetailSerializer,
            403: DetailSerializer,
            409: DetailSerializer,
        },
    )
    def patch(self, request):
        serializer = MeUpdateSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        user = request.user
        data = serializer.validated_data

        new_email = data.get("email")
        if new_email and new_email != user.email:
            if (
                User.objects.filter(email__iexact=new_email)
                .exclude(pk=user.pk)
                .exists()
            ):
                return Response(
                    {"detail": "This email is already in use."},
                    status=status.HTTP_409_CONFLICT,
                )
            return Response(
                {"detail": "Email changes require re-verification."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        update_fields = []
        for field in ("first_name", "last_name"):
            if field in data:
                setattr(user, field, data[field])
                update_fields.append(field)

        new_password = data.get("new_password")
        if new_password:
            user.set_password(new_password)
            update_fields.append("password")

        if update_fields:
            user.save(update_fields=update_fields)

        return Response(MeSerializer(user, context={"request": request}).data)


class StudentAuthMeView(RoleMeView):
    permission_classes = [IsVerifiedStudent]


class OrganizationAuthMeView(RoleMeView):
    permission_classes = [IsVerifiedCompany]


class MeView(RoleMeView):
    permission_classes = [IsAdminAccount]
