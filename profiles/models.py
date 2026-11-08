from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from common.models import (
    RiskLevel,
    TimeStampedModel,
    UUIDModel,
    VerificationStatus,
    unique_slug,
)
from profiles.normalization import clean_display, normalize_term


class CanonicalTerm(UUIDModel, TimeStampedModel):
    """A user-created lookup term with a stable normalized lookup key."""

    name = models.CharField(max_length=120)
    normalized_name = models.CharField(max_length=255, unique=True, editable=False)
    slug = models.SlugField(max_length=140, unique=True, blank=True)

    class Meta:
        abstract = True
        ordering = ["name"]

    def save(self, *args, **kwargs):
        self.name = clean_display(self.name)
        self.normalized_name = normalize_term(self.name)
        if not self.slug:
            self.slug = unique_slug(self, self.name, max_length=140)
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class Discipline(CanonicalTerm):
    """Canonical discipline used by both student profiles and placements."""


class Skill(CanonicalTerm):
    """Canonical skill entry used by the student skills picker."""


class TermAlias(UUIDModel):
    """A normalized user input that resolves to an existing canonical term."""

    name = models.CharField(max_length=120)
    alias_key = models.CharField(max_length=255, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True
        ordering = ["alias_key"]

    def save(self, *args, **kwargs):
        self.name = clean_display(self.name)
        self.alias_key = normalize_term(self.name)
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class DisciplineAlias(TermAlias):
    discipline = models.ForeignKey(
        Discipline,
        on_delete=models.CASCADE,
        related_name="aliases",
    )

    def __str__(self) -> str:
        return f"{self.name} -> {self.discipline}"


class SkillAlias(TermAlias):
    skill = models.ForeignKey(
        Skill,
        on_delete=models.CASCADE,
        related_name="aliases",
    )

    def __str__(self) -> str:
        return f"{self.name} -> {self.skill}"


class StudentProfile(UUIDModel, TimeStampedModel):
    """A student's marketplace profile."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="student_profile",
    )
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    phone = models.CharField(max_length=30, blank=True)
    location = models.CharField(max_length=160)
    institution = models.CharField(max_length=200)
    discipline = models.ForeignKey(
        Discipline,
        on_delete=models.PROTECT,
        related_name="students",
    )
    level = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(100), MaxValueValidator(700)]
    )
    graduation_year = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(2000), MaxValueValidator(2100)],
    )
    student_id = models.CharField(max_length=80, blank=True)
    start_date = models.DateField(null=True, blank=True)
    duration = models.CharField(max_length=80, blank=True)
    work_styles = models.JSONField(default=list, blank=True)
    portfolio = models.URLField(max_length=300, blank=True)
    interests = models.TextField(blank=True)
    skills = models.ManyToManyField(
        Skill,
        through="StudentSkill",
        related_name="students",
        blank=True,
    )
    photo = models.ForeignKey(
        "common.Document",
        on_delete=models.SET_NULL,
        related_name="student_photos",
        null=True,
        blank=True,
    )
    resume = models.ForeignKey(
        "common.Document",
        on_delete=models.SET_NULL,
        related_name="student_resumes",
        null=True,
        blank=True,
    )
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
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["verification_status", "risk_level"],
                name="student_verif_risk_idx",
            ),
            models.Index(
                fields=["discipline", "level"],
                name="student_disc_level_idx",
            ),
        ]

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def email(self) -> str:
        return self.user.email

    @property
    def is_verified(self) -> bool:
        return self.verification_status == VerificationStatus.VERIFIED

    @property
    def profile_strength(self) -> int:
        """Mirror the frontend's ten-field profile completeness indicator."""
        fields = [
            self.first_name,
            self.last_name,
            self.user.email if self.user_id else "",
            self.phone,
            self.location,
            self.institution,
            self.discipline_id,
            self.skills.exists() if self.pk else False,
            self.resume_id,
            self.portfolio,
        ]
        completed = sum(1 for value in fields if value)
        return round(completed / len(fields) * 100)

    def soft_delete(self):
        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at", "updated_at"])
        return self

    def __str__(self) -> str:
        return self.full_name or self.user.email


class StudentSkill(UUIDModel):
    """Through model for a student's selected skills."""

    student = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        related_name="student_skills",
    )
    skill = models.ForeignKey(
        Skill,
        on_delete=models.CASCADE,
        related_name="student_skills",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "skill"],
                name="unique_student_skill",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.student} - {self.skill}"
