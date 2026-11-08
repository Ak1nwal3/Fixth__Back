from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from common.models import UUIDModel


class AuditLog(UUIDModel):
    """Append-only record of administrative and moderation decisions."""

    class Action(models.TextChoices):
        APPROVE_VERIFICATION = "approve_verification", "Approve verification"
        REJECT_VERIFICATION = "reject_verification", "Reject verification"
        UNPUBLISH_LISTING = "unpublish_listing", "Unpublish listing"
        RESTORE_LISTING = "restore_listing", "Restore listing"
        SUSPEND_ACCOUNT = "suspend_account", "Suspend account"
        RESTORE_ACCOUNT = "restore_account", "Restore account"
        APPLICATION_STATUS_CHANGE = (
            "application_status_change",
            "Application status change",
        )
        PUBLISH_LISTING = "publish_listing", "Publish listing"
        RESOLVE_REPORT = "resolve_report", "Resolve report"

    class SubjectType(models.TextChoices):
        STUDENT = "student", "Student"
        COMPANY = "company", "Company"
        PLACEMENT = "placement", "Placement"
        APPLICATION = "application", "Application"
        ACCOUNT = "account", "Account"
        REPORT = "report", "Report"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="audit_entries",
        null=True,
        blank=True,
    )
    action = models.CharField(max_length=40, choices=Action.choices)
    subject_type = models.CharField(max_length=30, choices=SubjectType.choices)
    subject_id = models.CharField(max_length=64)
    subject_label = models.CharField(max_length=255, blank=True)
    reason = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(
        protocol="both",
        unpack_ipv4=True,
        null=True,
        blank=True,
    )
    user_agent = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["subject_type", "subject_id"],
                name="audit_subject_idx",
            ),
            models.Index(fields=["action", "created_at"], name="audit_action_idx"),
            models.Index(fields=["actor", "created_at"], name="audit_actor_idx"),
        ]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Audit log entries are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Audit log entries cannot be deleted.")

    @classmethod
    def record(
        cls,
        *,
        actor,
        action: str,
        subject_type: str,
        subject_id,
        subject_label: str = "",
        reason: str = "",
        metadata: dict | None = None,
        request=None,
    ):
        ip_address = None
        user_agent = ""
        if request is not None:
            ip_address = request.META.get("REMOTE_ADDR")
            user_agent = request.META.get("HTTP_USER_AGENT", "")

        return cls.objects.create(
            actor=actor if getattr(actor, "is_authenticated", False) else None,
            action=action,
            subject_type=subject_type,
            subject_id=str(subject_id),
            subject_label=subject_label,
            reason=reason,
            metadata=metadata or {},
            ip_address=ip_address,
            user_agent=user_agent,
        )

    def __str__(self) -> str:
        return f"{self.get_action_display()}: {self.subject_label or self.subject_id}"
