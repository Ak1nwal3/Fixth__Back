from pathlib import Path

from PIL import Image, UnidentifiedImageError
from django.urls import reverse
from django.utils.text import get_valid_filename
from rest_framework import serializers

from common.models import Document

MAX_PHOTO_BYTES = 2 * 1024 * 1024
MAX_PHOTO_DIMENSION = 4096
MAX_PHOTO_PIXELS = 16_777_216
PHOTO_FORMATS = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}
MAX_RESUME_BYTES = 5 * 1024 * 1024
RESUME_FORMATS = {
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
RESUME_SIGNATURES = {
    "pdf": b"%PDF-",
    "docx": b"PK\x03\x04",
    "doc": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
}


class DocumentSerializer(serializers.ModelSerializer):
    """Read representation of an uploaded document."""

    owner_id = serializers.UUIDField(read_only=True)
    file = serializers.SerializerMethodField()
    url = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = [
            "id",
            "owner_id",
            "kind",
            "file",
            "original_name",
            "mime_type",
            "size",
            "is_private",
            "scan_status",
            "created_at",
            "url",
        ]
        read_only_fields = fields

    def get_file(self, obj) -> str | None:
        """Return a storage path for private files and URL for public files."""

        if not obj.file:
            return None
        if obj.is_private:
            return obj.file.name
        try:
            url = obj.file.url
        except ValueError:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url

    def get_url(self, obj) -> str | None:
        """Return an authorized download URL for private documents."""

        if not obj.file:
            return None
        if obj.is_private:
            path = reverse("document-download", args=[obj.id])
            request = self.context.get("request")
            return request.build_absolute_uri(path) if request else path
        try:
            url = obj.file.url
        except ValueError:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url


class DocumentUploadSerializer(serializers.ModelSerializer):
    """Write representation used by upload endpoints."""

    class Meta:
        model = Document
        fields = [
            "id",
            "kind",
            "file",
            "original_name",
            "mime_type",
            "size",
            "is_private",
            "scan_status",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "original_name",
            "mime_type",
            "size",
            "scan_status",
            "created_at",
        ]

    def validate_file(self, value):
        kind = self.initial_data.get("kind")
        max_size = 2 * 1024 * 1024 if kind == Document.Kind.PROFILE_PHOTO else 5 * 1024 * 1024
        if value.size > max_size:
            limit = "2 MB" if kind == Document.Kind.PROFILE_PHOTO else "5 MB"
            raise serializers.ValidationError(f"Files must be {limit} or smaller.")
        return value


def _safe_original_name(value) -> str:
    return get_valid_filename(Path(value.name).name) or "file"


class StudentPhotoUploadSerializer(serializers.Serializer):
    """Validate and normalize a student profile photo upload."""

    file = serializers.FileField()

    def validate_file(self, value):
        value._fixth_original_name = _safe_original_name(value)
        if value.size > MAX_PHOTO_BYTES:
            raise serializers.ValidationError(
                "Photos must be 2 MB or smaller."
            )
        if value.size == 0:
            raise serializers.ValidationError(
                "Choose a photo containing content."
            )

        try:
            image = Image.open(value)
            width, height = image.size
            image_format = (image.format or "").upper()
        except (
            UnidentifiedImageError,
            OSError,
            ValueError,
            Image.DecompressionBombError,
        ):
            raise serializers.ValidationError(
                "Upload a JPEG, PNG, or WebP photo."
            )
        finally:
            value.seek(0)

        if (
            width < 1
            or height < 1
            or width > MAX_PHOTO_DIMENSION
            or height > MAX_PHOTO_DIMENSION
            or width * height > MAX_PHOTO_PIXELS
        ):
            raise serializers.ValidationError(
                "Photo dimensions are too large."
            )
        if image_format not in PHOTO_FORMATS:
            raise serializers.ValidationError(
                "Upload a JPEG, PNG, or WebP photo."
            )

        try:
            image.verify()
        except (OSError, ValueError, Image.DecompressionBombError):
            raise serializers.ValidationError(
                "Upload a valid JPEG, PNG, or WebP photo."
            )
        finally:
            value.seek(0)

        extension = PHOTO_FORMATS[image_format].split("/")[-1].replace("jpeg", "jpg")
        value.content_type = PHOTO_FORMATS[image_format]
        value.name = f"profile_photo.{extension}"
        return value

    def validate(self, attrs):
        attrs["original_name"] = attrs["file"]._fixth_original_name
        return attrs


class StudentResumeUploadSerializer(serializers.Serializer):
    """Validate and normalize a student resume upload."""

    file = serializers.FileField()

    def validate_file(self, value):
        value._fixth_original_name = _safe_original_name(value)
        if value.size > MAX_RESUME_BYTES:
            raise serializers.ValidationError(
                "Resumes must be 5 MB or smaller."
            )
        if value.size == 0:
            raise serializers.ValidationError(
                "Choose a resume containing content."
            )

        extension = Path(value.name).suffix.lower().lstrip(".")
        if extension not in RESUME_FORMATS:
            raise serializers.ValidationError(
                "Upload a PDF, DOC, or DOCX resume."
            )

        expected_signature = RESUME_SIGNATURES[extension]
        try:
            signature = value.read(len(expected_signature))
        except (OSError, ValueError):
            raise serializers.ValidationError(
                "Upload a valid PDF, DOC, or DOCX resume."
            )
        finally:
            value.seek(0)

        if not signature.startswith(expected_signature):
            raise serializers.ValidationError(
                "Upload a valid PDF, DOC, or DOCX resume."
            )

        value.content_type = RESUME_FORMATS[extension]
        value.name = f"resume.{extension}"
        return value

    def validate(self, attrs):
        attrs["original_name"] = attrs["file"]._fixth_original_name
        return attrs


class EmptySerializer(serializers.Serializer):
    """Use for action endpoints that take no request body."""
