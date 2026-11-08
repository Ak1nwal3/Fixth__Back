from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from common.models import TimeStampedModel, UUIDModel, unique_slug
from companies.models import Company
from profiles.models import Discipline, StudentProfile


class Placement(UUIDModel, TimeStampedModel):
    """An internship, SIWES, or industrial-training opportunity."""

    class Status(models.TextChoices):
        ACTIVE = "Active", "Active"
        DRAFT = "Draft", "Draft"
        CLOSED = "Closed", "Closed"
        EXPIRED = "Expired", "Expired"
        UNPUBLISHED = "Unpublished", "Unpublished"

    class Type(models.TextChoices):
        INTERNSHIP = "Internship", "Internship"
        SIWES = "SIWES", "SIWES"
        INDUSTRIAL_TRAINING = "Industrial Training", "Industrial Training"

    class WorkStyle(models.TextChoices):
        HYBRID = "Hybrid", "Hybrid"
        REMOTE = "Remote", "Remote"
        ON_SITE = "On-site", "On-site"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="placements",
    )
    slug = models.SlugField(max_length=240, unique=True, blank=True)
    title = models.CharField(max_length=200)
    location = models.CharField(max_length=160)
    work_style = models.CharField(max_length=20, choices=WorkStyle.choices)
    duration = models.CharField(max_length=50)
    discipline = models.ForeignKey(
        Discipline,
        on_delete=models.PROTECT,
        related_name="placements",
    )
    level = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(100), MaxValueValidator(700)]
    )
    type = models.CharField(
        max_length=30,
        choices=Type.choices,
        null=True,
        blank=True,
    )
    stipend_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    currency = models.CharField(max_length=3, default="NGN")
    description = models.TextField(blank=True)
    responsibilities = models.TextField(blank=True)
    requirements = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    reports_count = models.PositiveIntegerField(default=0)
    published_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_placements",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["status", "discipline", "location"],
                name="placement_status_disc_idx",
            ),
            models.Index(fields=["company", "status"], name="placement_company_idx"),
            models.Index(fields=["work_style", "location"], name="placement_style_loc_idx"),
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(self, self.title, max_length=240)
        if self.status == self.Status.ACTIVE and not self.published_at:
            self.published_at = timezone.now()
        if self.status == self.Status.CLOSED and not self.closed_at:
            self.closed_at = timezone.now()
        return super().save(*args, **kwargs)

    def set_status(self, status: str):
        if status not in self.Status.values:
            raise ValueError(f"Invalid placement status: {status}")
        self.status = status
        if status == self.Status.ACTIVE:
            if not self.published_at:
                self.published_at = timezone.now()
            self.closed_at = None
        elif status == self.Status.CLOSED:
            self.closed_at = timezone.now()
        self.save(
            update_fields=[
                "status",
                "published_at",
                "closed_at",
                "updated_at",
            ]
        )
        return self

    @property
    def is_open(self) -> bool:
        if self.status != self.Status.ACTIVE:
            return False
        return self.expires_at is None or self.expires_at > timezone.now()

    def __str__(self) -> str:
        return f"{self.title} at {self.company.name}"


class SavedPlacement(UUIDModel):
    """A placement bookmarked by a student."""

    student = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        related_name="saved_placements",
    )
    placement = models.ForeignKey(
        Placement,
        on_delete=models.CASCADE,
        related_name="saved_by",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "placement"],
                name="unique_saved_placement",
            ),
        ]
        indexes = [
            models.Index(
                fields=["student", "-created_at"],
                name="saved_student_created_idx",
            ),
            models.Index(fields=["placement"], name="saved_placement_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.student} saved {self.placement}"
