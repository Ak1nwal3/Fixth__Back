from django.db import transaction
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import generics, permissions, status
from rest_framework.exceptions import (
    NotAuthenticated,
    PermissionDenied,
    ValidationError,
)
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User
from accounts.permissions import IsVerifiedCompany, IsVerifiedStudent
from accounts.serializers import DetailSerializer
from applications.models import Application
from common.models import VerificationStatus
from placements.models import Placement, SavedPlacement
from placements.serializers import (
    CompanyPlacementSerializer,
    PlacementDetailSerializer,
    PlacementFacetsSerializer,
    PlacementSerializer,
    PlacementWriteSerializer,
    SavedPlacementSerializer,
)

PUBLIC_FILTER_PARAMETERS = [
    OpenApiParameter(
        name="q",
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        description="Search placement title, company name, or discipline.",
    ),
    OpenApiParameter(
        name="discipline",
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        description="Discipline name or slug.",
    ),
    OpenApiParameter(
        name="location",
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        description="Partial location match.",
    ),
    OpenApiParameter(
        name="duration",
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        description="Duration value, e.g. 6 months.",
    ),
    OpenApiParameter(
        name="work_style",
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        description="Comma-separated work styles: Remote, Hybrid, On-site.",
    ),
    OpenApiParameter(
        name="saved",
        type=OpenApiTypes.BOOL,
        location=OpenApiParameter.QUERY,
        description="When true, return only the signed-in student's saved roles.",
    ),
    OpenApiParameter(
        name="sort",
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        description="newest (default), title, or match.",
    ),
]


def public_placements():
    """Active, open listings that are safe to show to students and visitors."""

    now = timezone.now()
    return (
        Placement.objects.filter(
            status=Placement.Status.ACTIVE,
            company__deleted_at__isnull=True,
            company__owner__is_active=True,
            company__owner__account_status=User.AccountStatus.ACTIVE,
        )
        .exclude(company__verification_status=VerificationStatus.REJECTED)
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        .select_related("company", "discipline")
    )


def company_for_request(request):
    """Return the caller's company or fail without leaking other companies."""

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


class PlacementPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


@extend_schema(
    tags=["placements"],
    summary="Discover active placements",
    description=(
        "Public placement discovery. Only active, open listings from active "
        "companies are returned. Signed-in students also receive match, saved, "
        "and application state."
    ),
    parameters=PUBLIC_FILTER_PARAMETERS,
)
class PlacementListView(generics.ListAPIView):
    permission_classes = [permissions.AllowAny]
    serializer_class = PlacementSerializer
    pagination_class = PlacementPagination

    def get_queryset(self):
        queryset = public_placements()
        params = self.request.query_params

        search = (params.get("q") or "").strip()
        if search:
            queryset = queryset.filter(
                Q(title__icontains=search)
                | Q(company__name__icontains=search)
                | Q(discipline__name__icontains=search)
            )

        discipline = (params.get("discipline") or "").strip()
        if discipline:
            queryset = queryset.filter(
                Q(discipline__slug__iexact=discipline)
                | Q(discipline__name__iexact=discipline)
            )

        location = (params.get("location") or "").strip()
        if location:
            queryset = queryset.filter(location__icontains=location)

        duration = (params.get("duration") or "").strip()
        if duration:
            queryset = queryset.filter(duration__iexact=duration)

        styles = []
        for key in ("work_style", "styles"):
            for raw in params.getlist(key):
                styles.extend(
                    value.strip() for value in raw.split(",") if value.strip()
                )
        if styles:
            queryset = queryset.filter(work_style__in=styles)

        if (params.get("saved") or "").lower() in {"1", "true", "yes"}:
            if not getattr(self.request.user, "is_authenticated", False):
                raise NotAuthenticated(
                    "Sign in as a student to view saved placements."
                )
            student = getattr(self.request.user, "student_profile", None)
            if student is None:
                raise PermissionDenied(
                    "A student account is required to view saved placements."
                )
            queryset = queryset.filter(saved_by__student=student)

        sort = (params.get("sort") or "newest").strip().lower()
        student = getattr(self.request.user, "student_profile", None)
        if sort == "title":
            return queryset.order_by("title", "-created_at")
        if sort == "match" and student and student.discipline_id:
            return queryset.annotate(
                match_score=Case(
                    When(
                        discipline_id=student.discipline_id,
                        then=Value(80),
                    ),
                    default=Value(50),
                    output_field=IntegerField(),
                )
                + Case(
                    When(level__lte=student.level, then=Value(20)),
                    default=Value(0),
                    output_field=IntegerField(),
                )
            ).order_by("-match_score", "-published_at", "-created_at")
        if sort not in {"", "newest", "match"}:
            raise ValidationError({"sort": "Sort must be newest, title, or match."})
        return queryset.order_by("-published_at", "-created_at")


@extend_schema(
    tags=["placements"],
    summary="Get an active placement by slug",
    responses={
        200: PlacementDetailSerializer,
        404: DetailSerializer,
    },
)
class PlacementDetailView(generics.RetrieveAPIView):
    permission_classes = [permissions.AllowAny]
    serializer_class = PlacementDetailSerializer
    lookup_field = "slug"
    lookup_url_kwarg = "slug"

    def get_queryset(self):
        return public_placements()


@extend_schema(
    tags=["placements"],
    summary="Get an active placement by id",
    responses={
        200: PlacementDetailSerializer,
        404: DetailSerializer,
    },
)
class PlacementByIdView(PlacementDetailView):
    lookup_field = "id"
    lookup_url_kwarg = "placement_id"


@extend_schema(
    tags=["placements"],
    summary="Save a placement",
    description=(
        "Saves an active, public placement for the authenticated student. "
        "The student is derived from the access token and duplicate saves "
        "return 409."
    ),
    request=None,
    responses={
        201: SavedPlacementSerializer,
        401: DetailSerializer,
        403: DetailSerializer,
        404: DetailSerializer,
        409: DetailSerializer,
    },
)
class PlacementSaveView(APIView):
    permission_classes = [IsVerifiedStudent]

    def post(self, request, placement_id):
        student = student_for_request(request)
        placement = get_object_or_404(public_placements(), pk=placement_id)

        with transaction.atomic():
            saved, created = SavedPlacement.objects.get_or_create(
                student=student,
                placement=placement,
            )
        if not created:
            return Response(
                {"detail": "This placement is already saved."},
                status=status.HTTP_409_CONFLICT,
            )

        return Response(
            SavedPlacementSerializer(
                saved,
                context={"request": request},
            ).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        summary="Remove a saved placement",
        description=(
            "Removes the authenticated student's own saved record for the "
            "placement. Records belonging to another student return 404."
        ),
        responses={
            204: OpenApiResponse(description="Saved placement removed."),
            401: DetailSerializer,
            403: DetailSerializer,
            404: DetailSerializer,
        },
    )
    def delete(self, request, placement_id):
        student = student_for_request(request)
        saved = get_object_or_404(
            SavedPlacement,
            student=student,
            placement_id=placement_id,
        )
        saved.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(
    tags=["placements"],
    summary="List the student's saved placements",
    description=(
        "Returns placements saved by the authenticated student, newest save "
        "first. Placement status reflects the current database state."
    ),
    responses={
        200: SavedPlacementSerializer(many=True),
        401: DetailSerializer,
        403: DetailSerializer,
    },
)
class SavedPlacementListView(generics.ListAPIView):
    permission_classes = [IsVerifiedStudent]
    serializer_class = SavedPlacementSerializer
    pagination_class = PlacementPagination

    def get_queryset(self):
        student = student_for_request(self.request)
        return (
            SavedPlacement.objects.filter(student=student)
            .select_related(
                "placement",
                "placement__company",
                "placement__company__logo",
                "placement__discipline",
            )
            .order_by("-created_at")
        )


COMPANY_LIST_PARAMETERS = [
    OpenApiParameter(
        name="status",
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        description="Filter by placement status, or All.",
    ),
    OpenApiParameter(
        name="q",
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        description="Search placement title or discipline.",
    ),
    OpenApiParameter(
        name="sort",
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        description="newest (default), oldest, or title.",
    ),
]


@extend_schema_view(
    list=extend_schema(
        tags=["placements"],
        summary="List the company's placements",
        description="Returns draft, active, closed, expired, and unpublished listings.",
        parameters=COMPANY_LIST_PARAMETERS,
    ),
    create=extend_schema(
        tags=["placements"],
        summary="Create a placement",
        description=(
            "Creates a placement for the authenticated company. Save as a "
            "draft with status=Draft, or publish immediately with status=Active. "
            "Company, slug, timestamps, and moderation fields are server-owned."
        ),
        request=PlacementWriteSerializer,
        responses={
            201: PlacementSerializer,
            400: DetailSerializer,
            401: DetailSerializer,
            403: DetailSerializer,
        },
    ),
)
class CompanyPlacementListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsVerifiedCompany]
    pagination_class = PlacementPagination

    def get_serializer_class(self):
        if self.request.method == "POST":
            return PlacementWriteSerializer
        return CompanyPlacementSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        request = getattr(self, "request", None)
        if request is not None and request.method == "POST":
            company = getattr(request.user, "company", None)
            if company is not None:
                context["company"] = company
        return context

    def get_queryset(self):
        queryset = (
            Placement.objects.filter(company=company_for_request(self.request))
            .select_related("company", "company__logo", "discipline")
            .annotate(
                applicants_count=Count(
                    "applications",
                    distinct=True,
                ),
                new_applicants_count=Count(
                    "applications",
                    filter=Q(
                        applications__status=Application.Status.SUBMITTED
                    ),
                    distinct=True,
                ),
                shortlisted_count=Count(
                    "applications",
                    filter=Q(
                        applications__status=Application.Status.SHORTLISTED
                    ),
                    distinct=True,
                ),
            )
        )
        params = self.request.query_params

        status_filter = (params.get("status") or "").strip()
        if status_filter and status_filter.lower() != "all":
            status_match = next(
                (
                    value
                    for value in Placement.Status.values
                    if value.lower() == status_filter.lower()
                ),
                None,
            )
            if status_match is None:
                raise ValidationError(
                    {"status": "Select a valid placement status."}
                )

            now = timezone.now()
            if status_match == Placement.Status.ACTIVE:
                queryset = queryset.filter(
                    status=Placement.Status.ACTIVE
                ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
            elif status_match == Placement.Status.EXPIRED:
                queryset = queryset.filter(
                    Q(status=Placement.Status.EXPIRED)
                    | Q(
                        status=Placement.Status.ACTIVE,
                        expires_at__lte=now,
                    )
                )
            else:
                queryset = queryset.filter(status=status_match)

        search = (params.get("q") or "").strip()
        if search:
            queryset = queryset.filter(
                Q(title__icontains=search) | Q(discipline__name__icontains=search)
            )

        sort = (params.get("sort") or "newest").strip().lower()
        if sort == "title":
            return queryset.order_by("title", "-created_at")
        if sort == "oldest":
            return queryset.order_by("created_at")
        if sort not in {"", "newest"}:
            raise ValidationError({"sort": "Sort must be newest, oldest, or title."})
        return queryset.order_by("-created_at")

    def create(self, request, *args, **kwargs):
        company_for_request(request)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        placement = serializer.save()
        output = PlacementSerializer(
            placement,
            context=self.get_serializer_context(),
        )
        return Response(output.data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    get=extend_schema(
        tags=["placements"],
        summary="Get one of the company's placements",
        description=(
            "Returns the company's own listing in any state. This endpoint "
            "powers draft preview before publishing."
        ),
        responses={
            200: PlacementSerializer,
            401: DetailSerializer,
            403: DetailSerializer,
            404: DetailSerializer,
        },
    ),
    patch=extend_schema(
        tags=["placements"],
        summary="Update or publish a placement",
        description=(
            "Partially updates the company's own listing. Allowed status "
            "transitions are Draft to Active, Active to Closed, and Closed to "
            "Active. Publishing requires complete listing content."
        ),
        request=PlacementWriteSerializer,
        responses={
            200: PlacementSerializer,
            400: DetailSerializer,
            401: DetailSerializer,
            403: DetailSerializer,
            404: DetailSerializer,
            409: DetailSerializer,
        },
    ),
)
class CompanyPlacementDetailView(generics.GenericAPIView):
    permission_classes = [IsVerifiedCompany]
    lookup_field = "pk"
    http_method_names = ["get", "patch", "head", "options"]

    def get_queryset(self):
        return (
            Placement.objects.filter(company=company_for_request(self.request))
            .select_related("company", "discipline")
        )

    def get_serializer_class(self):
        if self.request.method == "PATCH":
            return PlacementWriteSerializer
        return PlacementSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        request = getattr(self, "request", None)
        if request is not None and getattr(request.user, "is_authenticated", False):
            company = getattr(request.user, "company", None)
            if company is not None:
                context["company"] = company
        return context

    def get(self, request, *args, **kwargs):
        placement = self.get_object()
        return Response(
            PlacementSerializer(
                placement,
                context={"request": request},
            ).data
        )

    def patch(self, request, *args, **kwargs):
        placement = self.get_object()
        serializer = PlacementWriteSerializer(
            placement,
            data=request.data,
            partial=True,
            context=self.get_serializer_context(),
        )
        serializer.is_valid(raise_exception=True)
        placement = serializer.save()
        return Response(
            PlacementSerializer(
                placement,
                context={"request": request},
            ).data
        )


@extend_schema(
    tags=["placements"],
    summary="Publish a placement",
    description=(
        "Convenience action for moving a draft or closed placement to Active. "
        "The same validation as PATCH status=Active applies."
    ),
    request=None,
    responses={
        200: PlacementSerializer,
        400: DetailSerializer,
        401: DetailSerializer,
        403: DetailSerializer,
        404: DetailSerializer,
        409: DetailSerializer,
    },
)
class CompanyPlacementPublishView(APIView):
    permission_classes = [IsVerifiedCompany]

    def post(self, request, pk):
        company = company_for_request(request)
        placement = get_object_or_404(Placement, pk=pk, company=company)
        if placement.status not in {
            Placement.Status.DRAFT,
            Placement.Status.CLOSED,
        }:
            return Response(
                {"detail": "Only draft or closed placements can be published."},
                status=status.HTTP_409_CONFLICT,
            )

        serializer = PlacementWriteSerializer(
            placement,
            data={"status": Placement.Status.ACTIVE},
            partial=True,
            context={"request": request, "company": company},
        )
        serializer.is_valid(raise_exception=True)
        placement = serializer.save()
        return Response(
            PlacementSerializer(
                placement,
                context={"request": request},
            ).data
        )


@extend_schema(
    tags=["placements"],
    summary="Get placement discovery facets",
    description=(
        "Dynamic filter options derived from the active placements currently "
        "available in public discovery."
    ),
    responses={
        200: PlacementFacetsSerializer,
    },
)
class PlacementFacetsView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        placements = public_placements()
        facets = {
            "disciplines": list(
                placements.order_by()
                .values_list("discipline__name", flat=True)
                .distinct()
                .order_by("discipline__name")
            ),
            "locations": list(
                placements.order_by()
                .values_list("location", flat=True)
                .distinct()
                .order_by("location")
            ),
            "durations": list(
                placements.order_by()
                .values_list("duration", flat=True)
                .distinct()
                .order_by("duration")
            ),
            "work_styles": list(
                placements.order_by()
                .values_list("work_style", flat=True)
                .distinct()
                .order_by("work_style")
            ),
        }
        return Response(PlacementFacetsSerializer(facets).data)
