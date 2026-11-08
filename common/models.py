import os
import uuid
from pathlib import Path

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify


def unique_slug(instance, value: str, max_length: int = 60) -> str:
    """Return a unique slug for *instance* based on *value*.

    Slugs are generated server-side and are intentionally stable. Callers should
    only set ``slug`` when they want a specific value (for example fixtures).
    """
    base = slugify(value)[:max_length] or "item"
    model = instance.__class__
    queryset = model.objects.all()
    if instance.pk:
        queryset = queryset.exclude(pk=instance.pk)

    slug = base
    while queryset.filter(slug=slug).exists():
        suffix = uuid.uuid4().hex[:6]
        slug = f"{base[: max_length - 7]}-{suffix}"
    return slug


class VerificationStatus(models.TextChoices):
    PENDING = "Pending", "Pending"
    VERIFIED = "Verified", "Verified"
    REJECTED = "Rejected", "Rejected"


class RiskLevel(models.TextChoices):
    LOW = "Low", "Low"
    MEDIUM = "Medium", "Medium"
    REVIEW = "Review", "Review"


class UUIDModel(models.Model):
    """Abstract base model with a UUID primary key."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TimeStampedModel(models.Model):
    """Abstract base model with creation and update timestamps."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


def document_upload_path(instance, filename: str) -> str:
    """Build a private, non-guessable storage path for an uploaded document."""
    extension = Path(filename).suffix.lower()
    kind = instance.kind or "other"
    stamp = timezone.now().strftime("%Y/%m")
    return f"documents/{kind}/{stamp}/{uuid.uuid4().hex}{extension}"


class Document(UUIDModel):
    """A file owned by a user.

    Resumes, verification evidence, and other sensitive files are private by
    default. Public images (profile photos, company logos) can opt out by
    setting ``is_private=False``.
    """

    class Kind(models.TextChoices):
        PROFILE_PHOTO = "profile_photo", "Profile photo"
        RESUME = "resume", "Resume"
        LOGO = "logo", "Company logo"
        VERIFICATION_EVIDENCE = "verification_evidence", "Verification evidence"
        PLACEMENT_ATTACHMENT = "placement_attachment", "Placement attachment"

    class ScanStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        CLEAN = "clean", "Clean"
        INFECTED = "infected", "Infected"
        SKIPPED = "skipped", "Skipped"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="documents",
    )
    kind = models.CharField(max_length=40, choices=Kind.choices)
    file = models.FileField(upload_to=document_upload_path)
    original_name = models.CharField(max_length=255, blank=True)
    mime_type = models.CharField(max_length=100, blank=True)
    size = models.PositiveBigIntegerField(default=0)
    checksum = models.CharField(max_length=64, blank=True)
    is_private = models.BooleanField(default=True)
    scan_status = models.CharField(
        max_length=20,
        choices=ScanStatus.choices,
        default=ScanStatus.PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["owner", "kind"], name="doc_owner_kind_idx"),
            models.Index(fields=["kind", "created_at"], name="doc_kind_created_idx"),
        ]

    def save(self, *args, **kwargs):
        if self.file:
            if not self.original_name:
                self.original_name = os.path.basename(self.file.name)
            if not self.size:
                try:
                    self.size = self.file.size
                except (OSError, ValueError):
                    self.size = 0
            if not self.mime_type:
                self.mime_type = getattr(self.file, "content_type", "") or ""
        return super().save(*args, **kwargs)

    @property
    def extension(self) -> str:
        return Path(self.file.name).suffix.lower() if self.file else ""

    def __str__(self) -> str:
        return self.original_name or self.file.name or str(self.id)
