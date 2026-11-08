from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from common.models import (
    RiskLevel,
    UUIDModel,
    VerificationStatus,
)
from companies.models import Company
from profiles.models import StudentProfile


class VerificationRequest(UUIDModel):
    """A trust-and-safety review of a student or company."""

    class Kind(models.TextChoices):
        STUDENT = "student", "Student"
        COMPANY = "company", "Company"

    kind = models.CharField(max_length=20, choices=Kind.choices)
    student = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        related_name="verification_requests",
        null=True,
        blank=True,
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="verification_requests",
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=20,
        choices=VerificationStatus.choices,
        default=VerificationStatus.PENDING,
    )
    risk_level = models.CharField(
        max_length=20,
        choices=RiskLevel.choices,
        default=RiskLevel.LOW,
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="verification_decisions",
        null=True,
        blank=True,
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_reason = models.TextField(blank=True)
    evidence = models.ManyToManyField(
        "common.Document",
        related_name="verification_requests",
        blank=True,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-submitted_at"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(student__isnull=False, company__isnull=True)
                    | models.Q(student__isnull=True, company__isnull=False)
                ),
                name="verif_one_subject",
            ),
            models.UniqueConstraint(
                fields=["student"],
                condition=models.Q(
                    status=VerificationStatus.PENDING,
                    student__isnull=False,
                ),
                name="uniq_pending_student_verif",
            ),
            models.UniqueConstraint(
                fields=["company"],
                condition=models.Q(
                    status=VerificationStatus.PENDING,
                    company__isnull=False,
                ),
                name="uniq_pending_company_verif",
            ),
        ]
        indexes = [
            models.Index(
                fields=["status", "kind", "submitted_at"],
                name="verification_queue_idx",
            ),
            models.Index(
                fields=["risk_level", "status"],
                name="verification_risk_idx",
            ),
        ]

    def clean(self):
        subject_count = int(self.student_id is not None) + int(self.company_id is not None)
        if subject_count != 1:
            raise ValidationError("A verification request must reference exactly one subject.")
        if self.student_id and self.kind != self.Kind.STUDENT:
            raise ValidationError("Student verification requests must use kind='student'.")
        if self.company_id and self.kind != self.Kind.COMPANY:
            raise ValidationError("Company verification requests must use kind='company'.")

    @property
    def subject_id(self):
        return self.student_id or self.company_id

    @property
    def subject_label(self) -> str:
        if self.student_id and self.student:
            return self.student.full_name
        if self.company_id and self.company:
            return self.company.name
        return "Unknown subject"

    def decide(self, decision: str, *, actor, reason: str = ""):
        """Approve or reject the request and update the subject's status."""
        if self.status != VerificationStatus.PENDING:
            raise ValueError("This verification request has already been decided.")
        if decision not in {"approve", "reject"}:
            raise ValueError("Decision must be 'approve' or 'reject'.")

        self.status = (
            VerificationStatus.VERIFIED
            if decision == "approve"
            else VerificationStatus.REJECTED
        )
        self.decided_by = actor
        self.decided_at = timezone.now()
        self.decision_reason = reason
        self.save(
            update_fields=[
                "status",
                "decided_by",
                "decided_at",
                "decision_reason",
                "updated_at",
            ]
        )

        subject = self.student or self.company
        if subject is not None:
            subject.verification_status = self.status
            subject.save(update_fields=["verification_status", "updated_at"])
        return self

    def __str__(self) -> str:
        return f"{self.kind}: {self.subject_label} ({self.status})"
