from django.contrib import admin

from applications.models import Application, ApplicationEvent


class ApplicationEventInline(admin.TabularInline):
    model = ApplicationEvent
    fk_name = "application"
    fields = ("from_status", "to_status", "actor", "reason", "created_at")
    readonly_fields = fields
    autocomplete_fields = ("actor",)
    extra = 0
    can_delete = False


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = (
        "student",
        "placement",
        "status",
        "applied_at",
        "status_changed_at",
        "decided_by",
    )
    list_filter = ("status", "applied_at", "status_changed_at")
    search_fields = (
        "student__first_name",
        "student__last_name",
        "student__user__email",
        "placement__title",
        "placement__company__name",
    )
    autocomplete_fields = ("placement", "student", "resume", "decided_by")
    readonly_fields = ("id", "applied_at", "status_changed_at", "updated_at")
    inlines = (ApplicationEventInline,)
    date_hierarchy = "applied_at"
    ordering = ("-applied_at",)
    list_select_related = ("student", "placement", "decided_by")
    list_per_page = 50


@admin.register(ApplicationEvent)
class ApplicationEventAdmin(admin.ModelAdmin):
    list_display = (
        "application",
        "from_status",
        "to_status",
        "actor",
        "created_at",
    )
    list_filter = ("from_status", "to_status", "created_at")
    search_fields = (
        "application__student__first_name",
        "application__student__last_name",
        "application__placement__title",
        "reason",
    )
    autocomplete_fields = ("application", "actor")
    readonly_fields = (
        "id",
        "from_status",
        "to_status",
        "actor",
        "reason",
        "created_at",
    )
    ordering = ("-created_at",)
    list_select_related = ("application", "actor")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
