from django.db import IntegrityError
from django.db.models import Count, Q
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from accounts.permissions import IsVerifiedCompany
from accounts.serializers import DetailSerializer
from accounts.services import EmailAlreadyRegistered, OTPVerificationError
from applications.models import Application
from common.models import VerificationStatus
from companies.models import Company
from placements.models import Placement
from companies.serializers import (
    CompanyMetricsSerializer,
    CompanyOnboardingResponseSerializer,
    CompanyOnboardingSerializer,
    CompanyPublicSerializer,
    CompanySerializer,
    CompanyUpdateSerializer,
)

MAX_ONBOARDING_REQUEST_BYTES = 6 * 1024 * 1024


def company_for_request(request):
    """Return the caller's company or fail without leaking other companies."""

    company = getattr(request.user, "company", None)
    if company is None or company.deleted_at is not None:
        raise PermissionDenied("An active company profile is required.")
    return company


def active_company_profiles():
    """Companies that may be displayed publicly through discovery/profiles."""

    return (
        Company.objects.exclude(verification_status=VerificationStatus.REJECTED)
        .filter(
            deleted_at__isnull=True,
            owner__is_active=True,
            owner__account_status=User.AccountStatus.ACTIVE,
        )
        .select_related("logo")
    )


@extend_schema(
    tags=["onboarding"],
    summary="Complete organization onboarding",
    description=(
        "Consumes a valid organization email verification token and atomically "
        "creates the company account, company profile, optional logo, and a "
        "pending verification request. The token is single-use."
    ),
    request=CompanyOnboardingSerializer,
    responses={
        201: CompanyOnboardingResponseSerializer,
        400: DetailSerializer,
        409: DetailSerializer,
        413: DetailSerializer,
        415: DetailSerializer,
        429: DetailSerializer,
    },
)
class OrganizationOnboardingCompleteView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    parser_classes = [MultiPartParser]
    throttle_scope = "onboarding_complete"

    def post(self, request):
        raw_content_length = request.META.get("CONTENT_LENGTH")
        try:
            content_length = int(raw_content_length)
        except (TypeError, ValueError):
            content_length = 0
        if content_length > MAX_ONBOARDING_REQUEST_BYTES:
            return Response(
                {"detail": "The onboarding request is too large."},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )

        serializer = CompanyOnboardingSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        try:
            company = serializer.save()
        except OTPVerificationError:
            return Response(
                {"detail": "Invalid or expired verification token."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except EmailAlreadyRegistered:
            return Response(
                {"detail": "This email cannot be used for onboarding."},
                status=status.HTTP_409_CONFLICT,
            )
        except IntegrityError:
            return Response(
                {"detail": "This email cannot be used for onboarding."},
                status=status.HTTP_409_CONFLICT,
            )

        refresh = RefreshToken.for_user(company.owner)
        payload = {
            "user": company.owner,
            "company": company,
            "access": str(refresh.access_token),
            "refresh": str(refresh),
        }
        return Response(
            CompanyOnboardingResponseSerializer(
                payload,
                context={"request": request},
            ).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema(
    tags=["companies"],
    summary="Get the authenticated company profile",
    description=(
        "Returns the profile for the authenticated organization account. "
        "Company ownership is resolved from the access token."
    ),
    responses={
        200: CompanySerializer,
        401: DetailSerializer,
        403: DetailSerializer,
    },
)
class CompanyProfileView(APIView):
    permission_classes = [IsVerifiedCompany]

    def get(self, request):
        company = company_for_request(request)
        return Response(
            CompanySerializer(company, context={"request": request}).data
        )

    @extend_schema(
        request=CompanyUpdateSerializer,
        responses={
            200: CompanySerializer,
            400: DetailSerializer,
            401: DetailSerializer,
            403: DetailSerializer,
            413: DetailSerializer,
            415: DetailSerializer,
        },
    )
    def patch(self, request):
        company = company_for_request(request)
        serializer = CompanyUpdateSerializer(
            company,
            data=request.data,
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        company = serializer.save()
        return Response(
            CompanySerializer(company, context={"request": request}).data
        )


@extend_schema(
    tags=["companies"],
    summary="Get a public company profile",
    description=(
        "Returns public company information by slug. Rejected, deleted, and "
        "inactive companies are not exposed."
    ),
    responses={
        200: CompanyPublicSerializer,
        404: DetailSerializer,
    },
)
class PublicCompanyDetailView(generics.RetrieveAPIView):
    permission_classes = [AllowAny]
    serializer_class = CompanyPublicSerializer
    lookup_field = "slug"
    lookup_url_kwarg = "slug"

    def get_queryset(self):
        return active_company_profiles()


@extend_schema(
    tags=["companies"],
    summary="Get company dashboard metrics",
    description=(
        "Returns live placement and application metrics for the authenticated "
        "company. Metrics are scoped to placements owned by that company."
    ),
    responses={
        200: CompanyMetricsSerializer,
        401: DetailSerializer,
        403: DetailSerializer,
    },
)
class CompanySummaryView(APIView):
    permission_classes = [IsVerifiedCompany]

    def get(self, request):
        company = company_for_request(request)
        now = timezone.now()
        metrics = company.placements.aggregate(
            active_placements=Count(
                "id",
                filter=Q(status=Placement.Status.ACTIVE)
                & (Q(expires_at__isnull=True) | Q(expires_at__gt=now)),
                distinct=True,
            ),
            applicants=Count("applications", distinct=True),
            shortlisted=Count(
                "applications",
                filter=Q(
                    applications__status=Application.Status.SHORTLISTED
                ),
                distinct=True,
            ),
            hired=Count(
                "applications",
                filter=Q(applications__status=Application.Status.ACCEPTED),
                distinct=True,
            ),
        )
        return Response(CompanyMetricsSerializer(metrics).data)
