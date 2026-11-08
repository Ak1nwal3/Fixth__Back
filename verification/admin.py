from django.contrib import admin
from django.utils import timezone

from common.models import VerificationStatus
from verification.models import VerificationRequest


@admin.register(VerificationRequest)
class VerificationRequestAdmin(admin.ModelAdmin):
    list_display = (
        "kind",
        "subject_label",
        "status",
        "risk_level",
        "submitted_at",
        "decided_by",
        "decided_at",
    )
    list_filter = ("kind", "status", "risk_level", "submitted_at")
    search_fields = (
        "student__first_name",
        "student__last_name",
        "student__user__email",
        "company__name",
        "decision_reason",
    )
    autocomplete_fields = ("student", "company", "decided_by")
    filter_horizontal = ("evidence",)
    readonly_fields = (
        "id",
        "subject_id",
        "subject_label",
        "submitted_at",
        "updated_at",
    )
    date_hierarchy = "submitted_at"
    ordering = ("-submitted_at",)
    list_select_related = ("student", "company", "decided_by")
    list_per_page = 50

    def save_model(self, request, obj, form, change):
        if obj.status == VerificationStatus.PENDING:
            if change and "status" in form.changed_data:
                obj.decided_by = None
                obj.decided_at = None
                obj.decision_reason = ""
        else:
            if obj.decided_by_id is None:
                obj.decided_by = request.user
            if obj.decided_at is None:
                obj.decided_at = timezone.now()
        super().save_model(request, obj, form, change)

        subject = obj.student or obj.company
        if subject is not None:
            subject.verification_status = obj.status
            subject.save(update_fields=["verification_status", "updated_at"])
