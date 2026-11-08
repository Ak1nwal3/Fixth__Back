import uuid

from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from django.db.models import Q
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import generics, permissions, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)

from accounts.models import User
from accounts.permissions import IsVerifiedStudent
from accounts.serializers import DetailSerializer, StudentOTPVerifySerializer
from accounts.services import (
    EmailAlreadyRegistered,
    OTPVerificationError,
    verify_student_otp,
)
from applications.models import Application
from common.models import Document
from common.serializers import (
    DocumentSerializer,
    StudentPhotoUploadSerializer,
    StudentResumeUploadSerializer,
)
from notifications.models import Notification
from profiles.models import Discipline, Skill, StudentProfile
from profiles.normalization import normalize_term
from profiles.serializers import (
    DisciplineSerializer,
    SkillSerializer,
    StudentOnboardingResponseSerializer,
    StudentOnboardingSerializer,
    StudentProfileSerializer,
)
from verification.models import VerificationRequest

REFERENCE_RESULT_LIMIT = 50


def student_profile_for_request(request):
    """Return the caller's active student profile or fail."""

    profile = getattr(request.user, "student_profile", None)
    if profile is None or profile.deleted_at is not None:
        raise PermissionDenied("An active student profile is required.")
    return profile


def delete_unused_document(document_id, *, ignore_profile_id=None):
    """Delete a current-media Document after its replacement is committed.

    Applications intentionally keep the resume version submitted with them,
    so a resume document referenced by any application is preserved.
    """

    document = Document.objects.filter(id=document_id).first()
    if document is None:
        return

    profile_references = StudentProfile.objects.filter(
        Q(photo_id=document_id) | Q(resume_id=document_id)
    )
    if ignore_profile_id is not None:
        profile_references = profile_references.exclude(pk=ignore_profile_id)
    if profile_references.exists() or Application.objects.filter(
        resume_id=document_id
    ).exists():
        return

    file_name = document.file.name if document.file else None
    document.delete()
    if file_name:
        def delete_stored_file():
            try:
                default_storage.delete(file_name)
            except OSError:
                # A failed cleanup must not fail the successful API update.
                pass

        transaction.on_commit(delete_stored_file)


def _delete_owned_document_if_unused(document, *, profile_id):
    """Delete student media not retained by applications or verification."""

    if (
        Application.objects.filter(resume=document).exists()
        or VerificationRequest.objects.filter(evidence=document).exists()
        or StudentProfile.objects.filter(
            Q(photo=document) | Q(resume=document)
        ).exclude(pk=profile_id).exists()
    ):
        return False

    file_name = document.file.name if document.file else None
    document.delete()
    if file_name:
        def delete_stored_file():
            try:
                default_storage.delete(file_name)
            except OSError:
                pass

        transaction.on_commit(delete_stored_file)
    return True


def _blacklist_all_refresh_tokens(user):
    outstanding_tokens = list(
        OutstandingToken.objects.filter(user=user).values_list(
            "id",
            flat=True,
        )
    )
    for token_id in outstanding_tokens:
        BlacklistedToken.objects.get_or_create(token_id=token_id)


def _delete_student_account(user, profile):
    """Soft-delete/anonymize a student while preserving business records."""

    with transaction.atomic():
        profile = StudentProfile.objects.select_for_update().get(pk=profile.pk)
        if profile.deleted_at is not None:
            return

        profile.first_name = ""
        profile.last_name = ""
        profile.phone = ""
        profile.location = "Deleted account"
        profile.institution = ""
        profile.student_id = ""
        profile.start_date = None
        profile.duration = ""
        profile.work_styles = []
        profile.portfolio = ""
        profile.interests = ""
        profile.photo = None
        profile.resume = None
        profile.soft_delete()
        profile.save(
            update_fields=[
                "first_name",
                "last_name",
                "phone",
                "location",
                "institution",
                "student_id",
                "start_date",
                "duration",
                "work_styles",
                "portfolio",
                "interests",
                "photo",
                "resume",
            ]
        )
        profile.skills.clear()

        Application.objects.filter(student=profile).update(cover_note="")
        profile.saved_placements.all().delete()
        Notification.objects.filter(user=user).delete()
        user.otp_codes.all().delete()

        user.email = f"deleted-{uuid.uuid4().hex}@deleted.fixth.invalid"
        user.first_name = ""
        user.last_name = ""
        user.is_active = False
        user.account_status = User.AccountStatus.DELETED
        user.email_verified_at = None
        user.set_unusable_password()
        user.save(
            update_fields=[
                "email",
                "first_name",
                "last_name",
                "is_active",
                "account_status",
                "email_verified_at",
                "password",
            ]
        )
        _blacklist_all_refresh_tokens(user)

    # The user row is intentionally retained so applications remain valid.
    for document in Document.objects.filter(owner=user).iterator():
        _delete_owned_document_if_unused(document, profile_id=profile.id)


SEARCH_PARAMETER = OpenApiParameter(
    name="search",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.QUERY,
    description="Case-insensitive name or alias search.",
)


class TermListView(generics.ListAPIView):
    """Read-only typeahead for a canonical term table."""

    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    pagination_class = None

    def get_search(self) -> str:
        return (self.request.query_params.get("search") or "").strip()

    def filter_terms(self, queryset, search: str):
        if not search:
            return queryset
        key = normalize_term(search)
        conditions = Q(name__icontains=search) | Q(aliases__name__icontains=search)
        if key:
            conditions |= Q(normalized_name__icontains=key)
            conditions |= Q(aliases__alias_key__icontains=key)
        return queryset.filter(conditions).distinct()


@extend_schema(
    tags=["reference"],
    summary="Search disciplines",
    parameters=[SEARCH_PARAMETER],
)
class DisciplineListView(TermListView):
    serializer_class = DisciplineSerializer

    def get_queryset(self):
        queryset = Discipline.objects.all()
        queryset = self.filter_terms(queryset, self.get_search())
        return queryset.order_by("name")[:REFERENCE_RESULT_LIMIT]


@extend_schema(
    tags=["reference"],
    summary="Search skills",
    parameters=[SEARCH_PARAMETER],
)
class SkillListView(TermListView):
    serializer_class = SkillSerializer

    def get_queryset(self):
        queryset = Skill.objects.all()
        queryset = self.filter_terms(queryset, self.get_search())
        return queryset.order_by("name")[:REFERENCE_RESULT_LIMIT]


@extend_schema(
    tags=["onboarding"],
    summary="Complete student onboarding",
    description=(
        "Consumes a valid .edu.ng email verification code and atomically "
        "creates the student user and profile. The code is single-use."
    ),
    request=StudentOnboardingSerializer,
    responses={
        201: StudentOnboardingResponseSerializer,
        400: DetailSerializer,
        409: DetailSerializer,
        429: DetailSerializer,
    },
)
class StudentOnboardingCompleteView(APIView):
    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_scope = "onboarding_complete"

    def post(self, request):
        otp_serializer = StudentOTPVerifySerializer(data=request.data)
        otp_serializer.is_valid(raise_exception=True)
        otp = verify_student_otp(
            otp_serializer.validated_data["email"],
            otp_serializer.validated_data["code"],
        )
        if otp is None:
            return Response(
                {"detail": "Invalid or expired verification code."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = StudentOnboardingSerializer(
            data=request.data,
            context={"request": request, "otp_id": otp.pk},
        )
        serializer.is_valid(raise_exception=True)
        try:
            profile = serializer.save()
        except OTPVerificationError:
            return Response(
                {"detail": "Invalid or expired verification code."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except EmailAlreadyRegistered:
            return Response(
                {"detail": "This email cannot be used for onboarding."},
                status=status.HTTP_409_CONFLICT,
            )

        refresh = RefreshToken.for_user(profile.user)
        payload = {
            "user": profile.user,
            "student": profile,
            "access": str(refresh.access_token),
            "refresh": str(refresh),
        }
        return Response(
            StudentOnboardingResponseSerializer(
                payload,
                context={"request": request},
            ).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema_view(
    get=extend_schema(
        tags=["profiles"],
        summary="Get the authenticated student's profile",
        responses={
            200: StudentProfileSerializer,
            401: DetailSerializer,
            403: DetailSerializer,
            404: DetailSerializer,
        },
    ),
    post=extend_schema(
        tags=["profiles"],
        summary="Create the authenticated student's profile",
        description=(
            "Creates a profile for the authenticated verified student. Email "
            "address changes are not accepted because they require "
            "re-verification."
        ),
        request=StudentProfileSerializer,
        responses={
            201: StudentProfileSerializer,
            400: DetailSerializer,
            401: DetailSerializer,
            403: DetailSerializer,
            409: DetailSerializer,
        },
    ),
    patch=extend_schema(
        tags=["profiles"],
        summary="Update the authenticated student's profile",
        description=(
            "Updates only the authenticated student's own profile. Email "
            "address changes are not accepted because they require "
            "re-verification."
        ),
        request=StudentProfileSerializer,
        responses={
            200: StudentProfileSerializer,
            400: DetailSerializer,
            401: DetailSerializer,
            403: DetailSerializer,
            404: DetailSerializer,
        },
    ),
    delete=extend_schema(
        tags=["profiles"],
        summary="Delete the authenticated student account",
        description=(
            "Soft-deletes and anonymizes the authenticated student's account. "
            "Applications and their resume versions are retained for companies; "
            "unreferenced personal media is removed. Refresh tokens are revoked."
        ),
        request={"type": "object", "properties": {"confirm": {"type": "boolean"}}},
        responses={
            204: OpenApiResponse(description="Account deleted."),
            400: DetailSerializer,
            401: DetailSerializer,
            403: DetailSerializer,
            409: DetailSerializer,
        },
    ),
)
class StudentMeView(APIView):
    permission_classes = [IsVerifiedStudent]

    def get(self, request):
        profile = getattr(request.user, "student_profile", None)
        if profile is None:
            return Response(
                {"detail": "Student profile not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            StudentProfileSerializer(profile, context={"request": request}).data
        )

    def post(self, request):
        if getattr(request.user, "student_profile", None) is not None:
            return Response(
                {"detail": "A student profile already exists for this account."},
                status=status.HTTP_409_CONFLICT,
            )

        serializer = StudentProfileSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        try:
            profile = serializer.save()
        except IntegrityError:
            return Response(
                {"detail": "A student profile already exists for this account."},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(
            StudentProfileSerializer(profile, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    def patch(self, request):
        profile = getattr(request.user, "student_profile", None)
        if profile is None:
            return Response(
                {"detail": "Student profile not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = StudentProfileSerializer(
            profile,
            data=request.data,
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        profile = serializer.save()
        return Response(
            StudentProfileSerializer(profile, context={"request": request}).data
        )

    def delete(self, request):
        profile = getattr(request.user, "student_profile", None)
        if profile is None:
            return Response(
                {"detail": "Student profile not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if profile.deleted_at is not None:
            return Response(
                {"detail": "This account is already deleted."},
                status=status.HTTP_409_CONFLICT,
            )
        if request.data.get("confirm") is not True:
            return Response(
                {"detail": "Set confirm=true to delete this account."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = User.objects.get(pk=request.user.pk)
        _delete_student_account(user, profile)
        return Response(status=status.HTTP_204_NO_CONTENT)


def _replace_media(profile, *, field, document_kind, upload):
    """Attach a new media document and clean up the replaced document."""

    old_document_id = getattr(profile, f"{field}_id")
    new_document = None
    try:
        with transaction.atomic():
            new_document = Document.objects.create(
                owner=profile.user,
                kind=document_kind,
                file=upload["file"],
                original_name=upload["original_name"],
                mime_type=upload["file"].content_type,
                size=upload["file"].size,
                is_private=document_kind == Document.Kind.RESUME,
            )
            setattr(profile, field, new_document)
            profile.save(update_fields=[field, "updated_at"])
    except Exception:
        if new_document is not None and new_document.file:
            new_document.file.delete(save=False)
        raise

    if old_document_id:
        delete_unused_document(old_document_id, ignore_profile_id=profile.id)
    return profile


@extend_schema(
    tags=["profiles"],
    summary="Upload or replace the student profile photo",
    description=(
        "Uploads a new profile photo for the authenticated student. Accepted "
        "formats are JPEG, PNG, and WebP; maximum size is 2 MB. The student "
        "is derived from the access token."
    ),
    request=StudentPhotoUploadSerializer,
    responses={
        200: StudentProfileSerializer,
        400: DetailSerializer,
        401: DetailSerializer,
        403: DetailSerializer,
        413: DetailSerializer,
        415: DetailSerializer,
    },
)
class StudentPhotoView(APIView):
    permission_classes = [IsVerifiedStudent]

    def post(self, request):
        profile = student_profile_for_request(request)
        serializer = StudentPhotoUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profile = _replace_media(
            profile,
            field="photo",
            document_kind=Document.Kind.PROFILE_PHOTO,
            upload=serializer.validated_data,
        )
        return Response(
            StudentProfileSerializer(
                profile,
                context={"request": request},
            ).data
        )

    @extend_schema(
        summary="Delete the student profile photo",
        responses={
            204: OpenApiResponse(description="Profile photo deleted."),
            401: DetailSerializer,
            403: DetailSerializer,
            404: DetailSerializer,
        },
    )
    def delete(self, request):
        profile = student_profile_for_request(request)
        old_document_id = profile.photo_id
        if old_document_id is None:
            return Response(status=status.HTTP_204_NO_CONTENT)

        with transaction.atomic():
            profile.photo = None
            profile.save(update_fields=["photo", "updated_at"])
        delete_unused_document(old_document_id, ignore_profile_id=profile.id)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema_view(
    get=extend_schema(
        tags=["profiles"],
        summary="Get the current student resume",
        description=(
            "Returns metadata and authorized download URL for the "
            "authenticated student's active resume. Returns 404 when none exists."
        ),
        responses={
            200: DocumentSerializer,
            401: DetailSerializer,
            403: DetailSerializer,
            404: DetailSerializer,
        },
    ),
    post=extend_schema(
        tags=["profiles"],
        summary="Upload or replace the student resume",
        description=(
            "Uploads a new active resume for the authenticated student. "
            "Accepted formats are PDF, DOC, and DOCX; maximum size is 5 MB. "
            "Applications keep the resume version submitted with them."
        ),
        request=StudentResumeUploadSerializer,
        responses={
            201: DocumentSerializer,
            400: DetailSerializer,
            401: DetailSerializer,
            403: DetailSerializer,
            413: DetailSerializer,
            415: DetailSerializer,
        },
    ),
    delete=extend_schema(
        tags=["profiles"],
        summary="Delete the current student resume",
        responses={
            204: OpenApiResponse(description="Resume deleted."),
            401: DetailSerializer,
            403: DetailSerializer,
            404: DetailSerializer,
        },
    ),
)
class StudentResumeView(APIView):
    permission_classes = [IsVerifiedStudent]

    def get(self, request):
        profile = student_profile_for_request(request)
        if profile.resume is None:
            return Response(
                {"detail": "No active resume found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            DocumentSerializer(
                profile.resume,
                context={"request": request},
            ).data
        )

    def post(self, request):
        profile = student_profile_for_request(request)
        serializer = StudentResumeUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profile = _replace_media(
            profile,
            field="resume",
            document_kind=Document.Kind.RESUME,
            upload=serializer.validated_data,
        )
        return Response(
            DocumentSerializer(
                profile.resume,
                context={"request": request},
            ).data,
            status=status.HTTP_201_CREATED,
        )

    def delete(self, request):
        profile = student_profile_for_request(request)
        old_document_id = profile.resume_id
        if old_document_id is None:
            return Response(status=status.HTTP_204_NO_CONTENT)

        with transaction.atomic():
            profile.resume = None
            profile.save(update_fields=["resume", "updated_at"])
        delete_unused_document(old_document_id, ignore_profile_id=profile.id)
        return Response(status=status.HTTP_204_NO_CONTENT)
