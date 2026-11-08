import re
from urllib.parse import urlsplit

from PIL import Image, UnidentifiedImageError
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import APIException

from accounts.models import OTPCode, User
from accounts.serializers import UserSerializer
from accounts.services import (
    EmailAlreadyRegistered,
    consume_onboarding_verification_token,
)
from common.models import Document
from common.serializers import DocumentSerializer
from companies.models import Company
from verification.models import VerificationRequest

MAX_LOGO_BYTES = 5 * 1024 * 1024
MAX_LOGO_DIMENSION = 4096
MAX_LOGO_PIXELS = 16_777_216
LOGO_FORMATS = {
    "JPEG": ("image/jpeg", "jpg"),
    "PNG": ("image/png", "png"),
    "WEBP": ("image/webp", "webp"),
}
WEBSITE_HOST_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)


class LogoTooLarge(APIException):
    status_code = 413
    default_detail = "The logo must be 5 MB or smaller."
    default_code = "logo_too_large"


class UnsupportedLogoType(APIException):
    status_code = 415
    default_detail = "Upload a JPEG, PNG, or WebP image."
    default_code = "unsupported_logo_type"


class CompanyWebsiteField(serializers.CharField):
    """Accept a company website with or without an explicit scheme."""

    default_error_messages = {
        "invalid": "Enter a valid company website.",
    }

    def to_internal_value(self, data):
        value = super().to_internal_value(data).strip()
        if not value:
            return ""
        if len(value) > 300 or not value.isascii():
            self.fail("invalid")

        candidate = value if "://" in value else f"https://{value}"
        try:
            parsed = urlsplit(candidate)
            port = parsed.port
        except ValueError:
            self.fail("invalid")

        if parsed.scheme.lower() not in {"http", "https"}:
            self.fail("invalid")
        if parsed.username or parsed.password or port is not None:
            self.fail("invalid")
        if parsed.fragment:
            self.fail("invalid")

        host = (parsed.hostname or "").lower()
        if not WEBSITE_HOST_RE.fullmatch(host):
            self.fail("invalid")

        normalized = f"{parsed.scheme.lower()}://{host}"
        if parsed.path and parsed.path != "/":
            normalized += parsed.path.rstrip("/")
        if parsed.query:
            normalized += f"?{parsed.query}"
        if len(normalized) > 300:
            self.fail("invalid")
        return normalized


def validate_logo_file(value):
    """Validate and normalize an uploaded company logo."""

    if value is None:
        return None
    if value.size > MAX_LOGO_BYTES:
        raise LogoTooLarge
    if value.size == 0:
        raise UnsupportedLogoType

    try:
        image = Image.open(value)
        width, height = image.size
        image_format = (image.format or "").upper()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise UnsupportedLogoType
    finally:
        value.seek(0)

    if (
        width < 1
        or height < 1
        or width > MAX_LOGO_DIMENSION
        or height > MAX_LOGO_DIMENSION
        or width * height > MAX_LOGO_PIXELS
    ):
        raise LogoTooLarge("The logo dimensions are too large.")
    if image_format not in LOGO_FORMATS:
        raise UnsupportedLogoType

    try:
        image.verify()
    except (OSError, ValueError, Image.DecompressionBombError):
        raise UnsupportedLogoType
    finally:
        value.seek(0)

    mime_type, extension = LOGO_FORMATS[image_format]
    value.content_type = mime_type
    value.name = f"logo.{extension}"
    return value


class CompanySerializer(serializers.ModelSerializer):
    owner_id = serializers.UUIDField(read_only=True)
    logo = DocumentSerializer(read_only=True)

    class Meta:
        model = Company
        fields = [
            "id",
            "owner_id",
            "name",
            "slug",
            "website",
            "industry",
            "description",
            "country",
            "city",
            "address",
            "logo",
            "registration_number",
            "primary_contact_name",
            "primary_contact_title",
            "verification_status",
            "risk_level",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "owner_id",
            "slug",
            "logo",
            "verification_status",
            "risk_level",
            "created_at",
            "updated_at",
        ]


class CompanySummarySerializer(serializers.ModelSerializer):
    logo_url = serializers.SerializerMethodField()

    class Meta:
        model = Company
        fields = [
            "id",
            "name",
            "slug",
            "logo_url",
            "verification_status",
        ]
        read_only_fields = fields

    def get_logo_url(self, obj) -> str | None:
        if not obj.logo or not obj.logo.file:
            return None
        try:
            url = obj.logo.file.url
        except ValueError:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url


class CompanyPublicSerializer(serializers.ModelSerializer):
    """Fields that are safe to expose on a public company profile."""

    logo_url = serializers.SerializerMethodField()

    class Meta:
        model = Company
        fields = [
            "id",
            "name",
            "slug",
            "website",
            "industry",
            "description",
            "country",
            "city",
            "address",
            "logo_url",
            "verification_status",
        ]
        read_only_fields = fields

    def get_logo_url(self, obj) -> str | None:
        if not obj.logo or not obj.logo.file:
            return None
        try:
            url = obj.logo.file.url
        except ValueError:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url


class CompanyUpdateSerializer(serializers.ModelSerializer):
    """Write representation for a company's own profile."""

    website = CompanyWebsiteField(
        max_length=300,
        required=False,
        allow_blank=True,
    )
    description = serializers.CharField(
        max_length=500,
        required=False,
        allow_blank=True,
    )
    address = serializers.CharField(max_length=255, required=False, allow_blank=True)
    logo = serializers.FileField(required=False, allow_null=True, write_only=True)

    class Meta:
        model = Company
        fields = [
            "name",
            "website",
            "industry",
            "description",
            "country",
            "city",
            "address",
            "registration_number",
            "primary_contact_name",
            "primary_contact_title",
            "logo",
        ]

    def validate_logo(self, value):
        return validate_logo_file(value)

    def update(self, instance, validated_data):
        logo = validated_data.pop("logo", None)
        logo_was_submitted = "logo" in self.initial_data
        owner = self.context.get("request").user
        logo_document = None

        try:
            with transaction.atomic():
                if logo is not None:
                    logo_document = Document.objects.create(
                        owner=owner,
                        kind=Document.Kind.LOGO,
                        file=logo,
                        mime_type=logo.content_type,
                        size=logo.size,
                        is_private=False,
                    )
                    instance.logo = logo_document
                elif logo_was_submitted and logo is None:
                    instance.logo = None

                return super().update(instance, validated_data)
        except Exception:
            # File storage is not rolled back with the database transaction.
            if logo_document is not None and logo_document.file:
                logo_document.file.delete(save=False)
            raise


class CompanyMetricsSerializer(serializers.Serializer):
    active_placements = serializers.IntegerField(read_only=True)
    applicants = serializers.IntegerField(read_only=True)
    shortlisted = serializers.IntegerField(read_only=True)
    hired = serializers.IntegerField(read_only=True)


class CompanyOnboardingSerializer(serializers.Serializer):
    """Create a company user and company from a verified onboarding token."""

    verification_token = serializers.CharField(write_only=True)
    organization_name = serializers.CharField(max_length=200)
    website = CompanyWebsiteField(
        max_length=300,
        required=False,
        allow_blank=True,
    )
    industry = serializers.ChoiceField(choices=Company.Industry.choices)
    description = serializers.CharField(
        max_length=500,
        required=False,
        allow_blank=True,
    )
    country = serializers.CharField(max_length=100)
    city = serializers.CharField(max_length=100)
    address = serializers.CharField(max_length=255, required=False, allow_blank=True)
    work_email = serializers.EmailField(max_length=254)
    password = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
    )
    logo = serializers.FileField(required=False, allow_null=True)

    def validate_work_email(self, value):
        return value.lower()

    def validate_logo(self, value):
        return validate_logo_file(value)

    def validate(self, attrs):
        password = attrs.get("password")
        if password:
            validate_password(password, user=User(email=attrs.get("work_email", "")))
        return attrs

    def create(self, validated_data):
        token = validated_data.pop("verification_token")
        work_email = validated_data.pop("work_email")
        organization_name = validated_data.pop("organization_name")
        password = validated_data.pop("password", None)
        logo = validated_data.pop("logo", None)
        logo_document = None

        try:
            with transaction.atomic():
                otp = consume_onboarding_verification_token(
                    token,
                    track=OTPCode.Track.ORGANIZATION,
                    email=work_email,
                )
                if User.objects.filter(email__iexact=work_email).exists():
                    raise EmailAlreadyRegistered

                # An organization without a password stays passwordless and
                # sets one later through the password-reset flow.
                user = User.objects.create_user(
                    email=work_email,
                    password=password or None,
                    role=User.Role.COMPANY,
                    email_verified_at=timezone.now(),
                )
                if logo is not None:
                    logo_document = Document.objects.create(
                        owner=user,
                        kind=Document.Kind.LOGO,
                        file=logo,
                        mime_type=logo.content_type,
                        size=logo.size,
                        is_private=False,
                    )

                company = Company.objects.create(
                    owner=user,
                    name=organization_name,
                    logo=logo_document,
                    **validated_data,
                )
                VerificationRequest.objects.create(
                    kind=VerificationRequest.Kind.COMPANY,
                    company=company,
                    risk_level=company.risk_level,
                )

                otp.user = user
                otp.mark_consumed(save=False)
                otp.save(update_fields=["user", "consumed_at"])
                return company
        except Exception:
            # File storage is not rolled back with the database transaction.
            if logo_document is not None and logo_document.file:
                logo_document.file.delete(save=False)
            raise


class CompanyOnboardingResponseSerializer(serializers.Serializer):
    user = UserSerializer(read_only=True)
    company = CompanySerializer(read_only=True)
    access = serializers.CharField(read_only=True)
    refresh = serializers.CharField(read_only=True)
