from django.conf import settings
from django.db import models
from django.utils import timezone

from common.models import (
    RiskLevel,
    TimeStampedModel,
    UUIDModel,
    VerificationStatus,
    unique_slug,
)


class Company(UUIDModel, TimeStampedModel):
    """An organization that publishes placements."""

    class Industry(models.TextChoices):
        TECHNOLOGY = "Technology", "Technology"
        ENGINEERING = "Engineering", "Engineering"
        CONSTRUCTION = "Construction", "Construction"
        MANUFACTURING = "Manufacturing", "Manufacturing"
        ENERGY = "Energy", "Energy"
        OTHER = "Other", "Other"

    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="company",
    )
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    website = models.CharField(max_length=300, blank=True)
    industry = models.CharField(max_length=40, choices=Industry.choices)
    description = models.TextField(max_length=500, blank=True)
    country = models.CharField(max_length=100)
    city = models.CharField(max_length=100)
    address = models.CharField(max_length=255, blank=True)
    logo = models.ForeignKey(
        "common.Document",
        on_delete=models.SET_NULL,
        related_name="company_logos",
        null=True,
        blank=True,
    )
    registration_number = models.CharField(max_length=80, blank=True)
    primary_contact_name = models.CharField(max_length=150, blank=True)
    primary_contact_title = models.CharField(max_length=150, blank=True)
    verification_status = models.CharField(
        max_length=20,
        choices=VerificationStatus.choices,
        default=VerificationStatus.PENDING,
    )
    risk_level = models.CharField(
        max_length=20,
        choices=RiskLevel.choices,
        default=RiskLevel.LOW,
    )
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(
                fields=["verification_status", "risk_level"],
                name="company_verif_risk_idx",
            ),
            models.Index(fields=["industry", "country"], name="company_ind_loc_idx"),
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(self, self.name, max_length=220)
        return super().save(*args, **kwargs)

    @property
    def is_verified(self) -> bool:
        return self.verification_status == VerificationStatus.VERIFIED

    def soft_delete(self):
        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at", "updated_at"])
        return self

    def __str__(self) -> str:
        return self.name
