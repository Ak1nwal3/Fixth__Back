from django.conf import settings
from django.db import models
from django.utils import timezone

from common.models import UUIDModel
from placements.models import Placement
from profiles.models import StudentProfile


class Application(UUIDModel):
    """A student's application to a placement."""

    class Status(models.TextChoices):
        SUBMITTED = "Submitted", "Submitted"
        VIEWED = "Viewed", "Viewed"
        SHORTLISTED = "Shortlisted", "Shortlisted"
        REJECTED = "Rejected", "Rejected"
        ACCEPTED = "Accepted", "Accepted"
        WITHDRAWN = "Withdrawn", "Withdrawn"

    placement = models.ForeignKey(
        Placement,
        on_delete=models.PROTECT,
        related_name="applications",
    )
    student = models.ForeignKey(
        StudentProfile,
        on_delete=models.PROTECT,
        related_name="applications",
    )
    resume = models.ForeignKey(
        "common.Document",
        on_delete=models.SET_NULL,
        related_name="applications",
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.SUBMITTED,
    )
    applied_at = models.DateTimeField(auto_now_add=True)
    status_changed_at = models.DateTimeField(default=timezone.now)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="decided_applications",
        null=True,
        blank=True,
    )
    rejection_reason = models.TextField(blank=True)
    cover_note = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-applied_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["placement", "student"],
                name="uniq_placement_student_app",
            ),
        ]
        indexes = [
            models.Index(fields=["student", "status"], name="app_student_status_idx"),
            models.Index(fields=["placement", "status"], name="app_placement_status_idx"),
        ]

    def set_status(self, new_status: str, *, actor=None, reason: str = ""):
        """Update the application status and append a timeline event."""
        if new_status not in self.Status.values:
            raise ValueError(f"Invalid application status: {new_status}")
        if new_status == self.status:
            return None

        previous = self.status
        self.status = new_status
        self.status_changed_at = timezone.now()
        if actor is not None:
            self.decided_by = actor
        if new_status == self.Status.REJECTED and reason:
            self.rejection_reason = reason
        self.save(
            update_fields=[
                "status",
                "status_changed_at",
                "decided_by",
                "rejection_reason",
                "updated_at",
            ]
        )

        return ApplicationEvent.objects.create(
            application=self,
            from_status=previous,
            to_status=new_status,
            actor=actor,
            reason=reason,
        )

    def __str__(self) -> str:
        return f"{self.student} -> {self.placement} ({self.status})"


class ApplicationEvent(UUIDModel):
    """Immutable timeline entry for an application status change."""

    application = models.ForeignKey(
        Application,
        on_delete=models.CASCADE,
        related_name="events",
    )
    from_status = models.CharField(
        max_length=20,
        choices=Application.Status.choices,
        blank=True,
    )
    to_status = models.CharField(
        max_length=20,
        choices=Application.Status.choices,
        blank=True,
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="application_events",
        null=True,
        blank=True,
    )
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["application", "created_at"], name="app_event_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.application_id}: {self.from_status or '—'} -> {self.to_status}"
