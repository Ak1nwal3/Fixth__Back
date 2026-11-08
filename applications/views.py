from uuid import UUID

from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
)
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsVerifiedCompany, IsVerifiedStudent
from accounts.serializers import DetailSerializer
from applications.models import Application
from applications.serializers import (
    ApplicationCreateSerializer,
    ApplicationEventSerializer,
    ApplicationSerializer,
    ApplicationStatusUpdateSerializer,
    ApplicationSummarySerializer,
    ContactDetailsSerializer,
)
from placements.models import Placement


def company_for_request(request):
    """Return the caller's company or fail without revealing other companies."""

    company = getattr(request.user, "company", None)
    if company is None or company.deleted_at is not None:
        raise PermissionDenied("An active company profile is required.")
    return company


def student_for_request(request):
    """Return the caller's active student profile or fail."""

    student = getattr(request.user, "student_profile", None)
    if student is None or student.deleted_at is not None:
        raise PermissionDenied("An active student profile is required.")
    return student


def student_applications(request):
    """Applications owned by the authenticated student."""

    student = student_for_request(request)
    return (
        Application.objects.filter(student=student)
        .select_related(
            "placement",
            "placement__company",
            "placement__company__logo",
            "placement__discipline",
            "student",
            "student__discipline",
            "student__photo",
            "resume",
            "decided_by",
        )
        .prefetch_related("student__skills", "events__actor")
    )


def company_applications(request):
    """Applications attached to placements owned by the calling company."""

    company = company_for_request(request)
    return (
        Application.objects.filter(placement__company=company)
        .select_related(
            "placement",
            "placement__company",
            "placement__company__logo",
            "placement__discipline",
            "student",
            "student__discipline",
            "student__photo",
            "resume",
            "decided_by",
        )
        .prefetch_related("student__skills", "events__actor")
    )


def filter_company_applications(
    queryset,
    request,
    *,
    allow_placement_filter=True,
):
    """Apply company applicant list filters and ordering."""

    params = request.query_params

    status_value = (params.get("status") or "").strip()
    if status_value and status_value.lower() != "all":
        status_match = next(
            (
                value
                for value in Application.Status.values
                if value.lower() == status_value.lower()
            ),
            None,
        )
        if status_match is None:
            raise ValidationError(
                {"status": "Select a valid application status."}
            )
        queryset = queryset.filter(status=status_match)

    if allow_placement_filter:
        placement_value = (params.get("placement") or "").strip()
        if placement_value:
            try:
                placement_id = UUID(placement_value)
            except (TypeError, ValueError):
                raise ValidationError(
                    {"placement": "Enter a valid placement UUID."}
                )
            queryset = queryset.filter(placement_id=placement_id)

    search = (params.get("q") or "").strip()
    if search:
        queryset = queryset.filter(
            Q(student__first_name__icontains=search)
            | Q(student__last_name__icontains=search)
            | Q(student__institution__icontains=search)
            | Q(student__skills__name__icontains=search)
            | Q(placement__title__icontains=search)
        ).distinct()

    discipline = (params.get("discipline") or "").strip()
    if discipline:
        queryset = queryset.filter(
            Q(student__discipline__slug__iexact=discipline)
            | Q(student__discipline__name__iexact=discipline)
        )

    sort = (params.get("sort") or "newest").strip().lower()
    if sort == "oldest":
        return queryset.order_by("applied_at")
    if sort == "name":
        return queryset.order_by(
            "student__first_name",
            "student__last_name",
            "-applied_at",
        )
    if sort not in {"", "newest"}:
        raise ValidationError(
            {"sort": "Sort must be newest, oldest, or name."}
        )
    return queryset.order_by("-applied_at")


class ApplicationPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


APPLICATION_LIST_PARAMETERS = [
    OpenApiParameter(
        name="placement",
        type=OpenApiTypes.UUID,
        location=OpenApiParameter.QUERY,
        description="Only return applications for this placement UUID.",
    ),
    OpenApiParameter(
        name="status",
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        description="Filter by application status, or All.",
    ),
    OpenApiParameter(
        name="q",
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        description=(
            "Search applicant name, institution, skills, or placement title."
        ),
    ),
    OpenApiParameter(
        name="discipline",
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        description="Applicant discipline name or slug.",
    ),
    OpenApiParameter(
        name="sort",
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        description="newest (default), oldest, or name.",
    ),
]

APPLICANT_LIST_PARAMETERS = [
    parameter
    for parameter in APPLICATION_LIST_PARAMETERS
    if parameter.name != "placement"
]


@extend_schema(
    tags=["applications"],
    summary="List applications across the company",
    description=(
        "Returns applications for all placements owned by the authenticated "
        "company. Company accounts can read applications and update their "
        "status, but cannot create or delete student applications."
    ),
    parameters=APPLICATION_LIST_PARAMETERS,
)
class CompanyApplicationListView(generics.ListAPIView):
    permission_classes = [IsVerifiedCompany]
    serializer_class = ApplicationSerializer
    pagination_class = ApplicationPagination

    def get_queryset(self):
        return filter_company_applications(
            company_applications(self.request),
            self.request,
        )


@extend_schema(
    tags=["applications"],
    summary="List applicants for one placement",
    description=(
        "Returns applications for one of the company's own placements. "
        "Supports the same search, status, discipline, and sort filters as the "
        "company-wide application list."
    ),
    parameters=APPLICANT_LIST_PARAMETERS,
)
class CompanyPlacementApplicantsView(generics.ListAPIView):
    permission_classes = [IsVerifiedCompany]
    serializer_class = ApplicationSerializer
    pagination_class = ApplicationPagination

    def get_queryset(self):
        company = company_for_request(self.request)
        placement = get_object_or_404(
            Placement,
            pk=self.kwargs["placement_id"],
            company=company,
        )
        return filter_company_applications(
            company_applications(self.request).filter(placement=placement),
            self.request,
            allow_placement_filter=False,
        )


@extend_schema_view(
    get=extend_schema(
        tags=["applications"],
        summary="Get one of the company's applications",
        description=(
            "Returns the full application review payload, including the "
            "limited student profile, resume metadata, status timeline, and "
            "the student's cover note."
        ),
        responses={
            200: ApplicationSerializer,
            401: DetailSerializer,
            403: DetailSerializer,
            404: DetailSerializer,
        },
    ),
    patch=extend_schema(
        tags=["applications"],
        summary="Update an application status",
        description=(
            "Moves an application to Viewed, Shortlisted, Accepted, or "
            "Rejected. Submitted is create-only and cannot be set here. Each "
            "change records an ApplicationEvent and sets the deciding user."
        ),
        request=ApplicationStatusUpdateSerializer,
        responses={
            200: ApplicationSerializer,
            400: DetailSerializer,
            401: DetailSerializer,
            403: DetailSerializer,
            404: DetailSerializer,
            409: DetailSerializer,
        },
    ),
)
class CompanyApplicationDetailView(generics.GenericAPIView):
    permission_classes = [IsVerifiedCompany]
    http_method_names = ["get", "patch", "head", "options"]

    def get_queryset(self):
        return company_applications(self.request)

    def get_serializer_class(self):
        if self.request.method == "PATCH":
            return ApplicationStatusUpdateSerializer
        return ApplicationSerializer

    def get(self, request, pk):
        application = self.get_object()
        return Response(self.get_serializer(application).data)

    def patch(self, request, pk):
        application = self.get_object()
        serializer = self.get_serializer(application, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        # Re-fetch so the response includes the newly created timeline event.
        application = self.get_queryset().get(pk=application.pk)
        return Response(
            ApplicationSerializer(
                application,
                context=self.get_serializer_context(),
            ).data
        )


@extend_schema(
    tags=["applications"],
    summary="Get gated applicant contact details",
    description=(
        "Returns applicant contact details only after an application has been "
        "shortlisted or accepted. Cross-company records return 404."
    ),
    responses={
        200: ContactDetailsSerializer,
        401: DetailSerializer,
        403: DetailSerializer,
        404: DetailSerializer,
    },
)
class CompanyApplicationContactView(APIView):
    permission_classes = [IsVerifiedCompany]

    def get(self, request, pk):
        company = company_for_request(request)
        application = get_object_or_404(
            Application.objects.select_related(
                "placement__company",
                "student",
                "student__user",
            ),
            pk=pk,
            placement__company=company,
        )
        if application.status not in {
            Application.Status.SHORTLISTED,
            Application.Status.ACCEPTED,
        }:
            return Response(
                {
                    "detail": (
                        "Contact details are available once an applicant has "
                        "been shortlisted or accepted."
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        student = application.student
        return Response(
            ContactDetailsSerializer(
                {
                    "email": student.user.email,
                    "phone": student.phone,
                }
            ).data
        )


STUDENT_APPLICATION_LIST_PARAMETERS = [
    OpenApiParameter(
        name="status",
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        description="Filter by application status, or All.",
    ),
]


@extend_schema_view(
    list=extend_schema(
        tags=["applications"],
        summary="List the student's applications",
        description=(
            "Returns applications owned by the authenticated student, newest "
            "first. Supports filtering by application status."
        ),
        parameters=STUDENT_APPLICATION_LIST_PARAMETERS,
    ),
    create=extend_schema(
        tags=["applications"],
        summary="Apply to a placement",
        description=(
            "Creates a one-click application for the authenticated student. "
            "The placement must be active and open, and the student must meet "
            "its discipline, level, and resume requirements."
        ),
        request=ApplicationCreateSerializer,
        responses={
            201: ApplicationSerializer,
            400: DetailSerializer,
            401: DetailSerializer,
            403: DetailSerializer,
            404: DetailSerializer,
            409: DetailSerializer,
        },
    ),
)
class StudentApplicationListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsVerifiedStudent]
    pagination_class = ApplicationPagination

    def get_serializer_class(self):
        if self.request.method == "POST":
            return ApplicationCreateSerializer
        return ApplicationSerializer

    def get_queryset(self):
        queryset = student_applications(self.request)
        params = self.request.query_params

        status_value = (params.get("status") or "").strip()
        if status_value and status_value.lower() != "all":
            status_match = next(
                (
                    value
                    for value in Application.Status.values
                    if value.lower() == status_value.lower()
                ),
                None,
            )
            if status_match is None:
                raise ValidationError(
                    {"status": "Select a valid application status."}
                )
            return queryset.filter(status=status_match)
        return queryset.order_by("-applied_at")

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        application = serializer.save()
        output = ApplicationSerializer(
            application,
            context=self.get_serializer_context(),
        )
        return Response(output.data, status=status.HTTP_201_CREATED)


@extend_schema(
    tags=["applications"],
    summary="Get a student application",
    description=(
        "Returns one of the authenticated student's applications, including "
        "placement/company details, status, resume metadata, cover note, "
        "and timeline."
    ),
    responses={
        200: ApplicationSerializer,
        401: DetailSerializer,
        403: DetailSerializer,
        404: DetailSerializer,
    },
)
class StudentApplicationDetailView(generics.RetrieveAPIView):
    permission_classes = [IsVerifiedStudent]
    serializer_class = ApplicationSerializer

    def get_queryset(self):
        return student_applications(self.request)


@extend_schema(
    tags=["applications"],
    summary="Get student application metrics",
    description=(
        "Returns aggregate counts for the authenticated student's "
        "applications. `pending` counts Submitted applications and "
        "`under_review` counts Viewed applications."
    ),
    responses={
        200: ApplicationSummarySerializer,
        401: DetailSerializer,
        403: DetailSerializer,
    },
)
class StudentApplicationSummaryView(APIView):
    permission_classes = [IsVerifiedStudent]

    def get(self, request):
        metrics = student_applications(request).aggregate(
            total=Count("id", distinct=True),
            pending=Count(
                "id",
                filter=Q(status=Application.Status.SUBMITTED),
                distinct=True,
            ),
            under_review=Count(
                "id",
                filter=Q(status=Application.Status.VIEWED),
                distinct=True,
            ),
            shortlisted=Count(
                "id",
                filter=Q(status=Application.Status.SHORTLISTED),
                distinct=True,
            ),
            accepted=Count(
                "id",
                filter=Q(status=Application.Status.ACCEPTED),
                distinct=True,
            ),
            rejected=Count(
                "id",
                filter=Q(status=Application.Status.REJECTED),
                distinct=True,
            ),
            withdrawn=Count(
                "id",
                filter=Q(status=Application.Status.WITHDRAWN),
                distinct=True,
            ),
        )
        return Response(ApplicationSummarySerializer(metrics).data)


@extend_schema(
    tags=["applications"],
    summary="Get application status events",
    description=(
        "Returns the authenticated student's application timeline in "
        "chronological order."
    ),
    responses={
        200: ApplicationEventSerializer(many=True),
        401: DetailSerializer,
        403: DetailSerializer,
        404: DetailSerializer,
    },
)
class StudentApplicationEventsView(APIView):
    permission_classes = [IsVerifiedStudent]

    def get(self, request, pk):
        application = get_object_or_404(
            student_applications(request),
            pk=pk,
        )
        events = application.events.select_related("actor").order_by("created_at")
        return Response(
            ApplicationEventSerializer(events, many=True).data
        )


@extend_schema(
    tags=["applications"],
    summary="Withdraw an application",
    description=(
        "Allows the student to withdraw an application that has not yet been "
        "accepted or rejected. The withdrawal is recorded on the timeline."
    ),
    request=None,
    responses={
        200: ApplicationSerializer,
        401: DetailSerializer,
        403: DetailSerializer,
        404: DetailSerializer,
        409: DetailSerializer,
    },
)
class StudentApplicationWithdrawView(APIView):
    permission_classes = [IsVerifiedStudent]

    def post(self, request, pk):
        application = get_object_or_404(
            student_applications(request),
            pk=pk,
        )
        if application.status not in {
            Application.Status.SUBMITTED,
            Application.Status.VIEWED,
            Application.Status.SHORTLISTED,
        }:
            return Response(
                {
                    "detail": (
                        "Only pending or under-review applications can be "
                        "withdrawn."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        with transaction.atomic():
            application.set_status(
                Application.Status.WITHDRAWN,
                actor=request.user,
                reason="Application withdrawn by student.",
            )

        application = student_applications(request).get(pk=application.pk)
        return Response(
            ApplicationSerializer(
                application,
                context={"request": request},
            ).data
        )
