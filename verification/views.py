from django.db import IntegrityError, transaction
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsVerifiedStudent
from accounts.serializers import DetailSerializer
from common.models import VerificationStatus
from common.serializers import EmptySerializer
from profiles.models import StudentProfile
from verification.models import VerificationRequest
from verification.serializers import (
    StudentVerificationSubmitSerializer,
    VerificationRequestSerializer,
)


def student_profile_for_request(request):
    """Return the caller's active student profile or fail."""

    profile = getattr(request.user, "student_profile", None)
    if profile is None or profile.deleted_at is not None:
        raise PermissionDenied("An active student profile is required.")
    return profile


@extend_schema(
    tags=["verification"],
    summary="Submit student verification for review",
    description=(
        "Submits the authenticated student's existing profile for review. "
        "No student ID or documents are accepted. A rejected student may "
        "submit again, but a pending or already-verified student cannot."
    ),
    request=EmptySerializer,
    responses={
        201: VerificationRequestSerializer,
        401: DetailSerializer,
        403: DetailSerializer,
        409: DetailSerializer,
    },
)
class StudentVerificationView(APIView):
    permission_classes = [IsAuthenticated, IsVerifiedStudent]

    def post(self, request):
        profile = student_profile_for_request(request)
        if profile.verification_status == VerificationStatus.VERIFIED:
            return Response(
                {"detail": "This student account is already verified."},
                status=status.HTTP_409_CONFLICT,
            )

        try:
            with transaction.atomic():
                profile = StudentProfile.objects.select_for_update().get(
                    pk=profile.pk
                )
                if profile.verification_status == VerificationStatus.VERIFIED:
                    return Response(
                        {"detail": "This student account is already verified."},
                        status=status.HTTP_409_CONFLICT,
                    )
                if VerificationRequest.objects.filter(
                    student=profile,
                    status=VerificationStatus.PENDING,
                ).exists():
                    return Response(
                        {
                            "detail": (
                                "A verification request is already being reviewed."
                            )
                        },
                        status=status.HTTP_409_CONFLICT,
                    )

                serializer = StudentVerificationSubmitSerializer(
                    data=request.data,
                    context={"request": request, "student": profile},
                )
                serializer.is_valid(raise_exception=True)
                verification = serializer.save()
                profile.verification_status = VerificationStatus.PENDING
                profile.save(
                    update_fields=["verification_status", "updated_at"]
                )
        except IntegrityError:
            return Response(
                {"detail": "A verification request is already being reviewed."},
                status=status.HTTP_409_CONFLICT,
            )

        return Response(
            VerificationRequestSerializer(
                verification,
                context={"request": request},
            ).data,
            status=status.HTTP_201_CREATED,
        )
