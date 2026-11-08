from django.contrib import admin

from audit.models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = (
        "action",
        "subject_type",
        "subject_label",
        "actor",
        "created_at",
    )
    list_filter = ("action", "subject_type", "created_at")
    search_fields = (
        "subject_label",
        "subject_id",
        "actor__email",
        "reason",
    )
    autocomplete_fields = ("actor",)
    readonly_fields = [field.name for field in AuditLog._meta.fields]
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("actor",)
    list_per_page = 50

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
