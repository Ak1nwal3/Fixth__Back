from django.contrib import admin

from placements.models import Placement, SavedPlacement


@admin.register(Placement)
class PlacementAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "company",
        "discipline",
        "location",
        "work_style",
        "level",
        "status",
        "reports_count",
        "published_at",
    )
    list_filter = (
        "status",
        "work_style",
        "discipline",
        "location",
        "created_at",
    )
    search_fields = (
        "title",
        "company__name",
        "description",
        "requirements",
    )
    autocomplete_fields = ("company", "discipline", "created_by")
    readonly_fields = (
        "id",
        "slug",
        "reports_count",
        "created_at",
        "updated_at",
    )
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("company", "discipline", "created_by")
    list_per_page = 50


@admin.register(SavedPlacement)
class SavedPlacementAdmin(admin.ModelAdmin):
    list_display = ("student", "placement", "created_at")
    list_filter = ("created_at",)
    search_fields = (
        "student__first_name",
        "student__last_name",
        "student__user__email",
        "placement__title",
        "placement__company__name",
    )
    autocomplete_fields = ("student", "placement")
    readonly_fields = ("id", "created_at")
    ordering = ("-created_at",)
    list_select_related = ("student", "placement")
