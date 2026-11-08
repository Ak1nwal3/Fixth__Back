from django.conf import settings
from django.db import models
from django.utils import timezone

from common.models import UUIDModel


class Notification(UUIDModel):
    """In-app notification for a user."""

    class Kind(models.TextChoices):
        APPLICATION_STATUS = "application_status", "Application status"
        PLACEMENT_ALERT = "placement_alert", "Placement alert"
        VERIFICATION = "verification", "Verification"
        SYSTEM = "system", "System"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    kind = models.CharField(max_length=40, choices=Kind.choices)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    data = models.JSONField(default=dict, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "read_at"], name="notification_unread_idx"),
            models.Index(fields=["user", "created_at"], name="notification_user_idx"),
        ]

    @property
    def is_read(self) -> bool:
        return self.read_at is not None

    def mark_read(self, *, save: bool = True):
        if self.read_at is None:
            self.read_at = timezone.now()
            if save:
                self.save(update_fields=["read_at"])
        return self

    def __str__(self) -> str:
        return f"{self.title} ({self.user})"
