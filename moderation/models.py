from django.conf import settings
from django.db import models
from django.utils import timezone

from common.models import UUIDModel
from placements.models import Placement


class PlacementReport(UUIDModel):
    """A student or anonymous visitor report against a placement."""

    class Reason(models.TextChoices):
        SPAM = "spam", "Spam"
        MISLEADING = "misleading", "Misleading"
        DISCRIMINATION = "discrimination", "Discrimination"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        RESOLVED = "resolved", "Resolved"

    placement = models.ForeignKey(
        Placement,
        on_delete=models.CASCADE,
        related_name="reports",
    )
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="placement_reports",
        null=True,
        blank=True,
    )
    reason = models.CharField(max_length=30, choices=Reason.choices)
    details = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="resolved_placement_reports",
        null=True,
        blank=True,
    )
    resolution_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["placement", "status"],
                name="report_placement_status_idx",
            ),
            models.Index(fields=["status", "created_at"], name="report_status_idx"),
        ]

    def resolve(self, *, actor, note: str = ""):
        if self.status == self.Status.RESOLVED:
            return self

        self.status = self.Status.RESOLVED
        self.resolved_at = timezone.now()
        self.resolved_by = actor
        self.resolution_note = note
        self.save(
            update_fields=[
                "status",
                "resolved_at",
                "resolved_by",
                "resolution_note",
            ]
        )

        open_reports = self.placement.reports.filter(status=self.Status.OPEN).count()
        Placement.objects.filter(pk=self.placement_id).update(
            reports_count=open_reports
        )
        return self

    def __str__(self) -> str:
        return f"{self.placement} reported: {self.reason}"
