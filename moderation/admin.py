from django.contrib import admin

from moderation.models import PlacementReport


@admin.register(PlacementReport)
class PlacementReportAdmin(admin.ModelAdmin):
    list_display = (
        "placement",
        "reporter",
        "reason",
        "status",
        "created_at",
        "resolved_by",
        "resolved_at",
    )
    list_filter = ("reason", "status", "created_at")
    search_fields = (
        "placement__title",
        "placement__company__name",
        "reporter__email",
        "details",
    )
    autocomplete_fields = ("placement", "reporter", "resolved_by")
    readonly_fields = ("id", "created_at")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("placement", "reporter", "resolved_by")
    list_per_page = 50
