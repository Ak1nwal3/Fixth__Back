from django.contrib import admin

from notifications.models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "kind",
        "title",
        "read_at",
        "created_at",
    )
    list_filter = ("kind", "read_at", "created_at")
    search_fields = ("user__email", "title", "body")
    autocomplete_fields = ("user",)
    readonly_fields = ("id", "created_at")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("user",)
    list_per_page = 50
