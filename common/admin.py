from django.contrib import admin

from common.models import Document


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = (
        "original_name",
        "kind",
        "owner",
        "size",
        "is_private",
        "scan_status",
        "created_at",
    )
    list_display_links = ("original_name",)
    list_filter = ("kind", "is_private", "scan_status", "created_at")
    search_fields = ("original_name", "owner__email", "mime_type")
    autocomplete_fields = ("owner",)
    readonly_fields = (
        "id",
        "original_name",
        "mime_type",
        "size",
        "checksum",
        "created_at",
    )
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("owner",)
    list_per_page = 50
