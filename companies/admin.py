from django.contrib import admin

from companies.models import Company


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "owner",
        "industry",
        "country",
        "city",
        "verification_status",
        "risk_level",
        "created_at",
    )
    list_filter = (
        "industry",
        "verification_status",
        "risk_level",
        "country",
        "created_at",
    )
    search_fields = (
        "name",
        "owner__email",
        "registration_number",
        "city",
    )
    autocomplete_fields = ("owner", "logo")
    readonly_fields = ("id", "slug", "created_at", "updated_at")
    date_hierarchy = "created_at"
    ordering = ("name",)
    list_select_related = ("owner",)
    list_per_page = 50
