from django.contrib import admin

from profiles.models import (
    Discipline,
    DisciplineAlias,
    Skill,
    SkillAlias,
    StudentProfile,
    StudentSkill,
)


@admin.register(Discipline)
class DisciplineAdmin(admin.ModelAdmin):
    list_display = ("name", "normalized_name", "slug", "created_at")
    search_fields = ("name", "normalized_name")
    ordering = ("name",)
    readonly_fields = ("id", "normalized_name", "created_at", "updated_at")


@admin.register(Skill)
class SkillAdmin(admin.ModelAdmin):
    list_display = ("name", "normalized_name", "slug", "created_at")
    search_fields = ("name", "normalized_name")
    ordering = ("name",)
    readonly_fields = ("id", "normalized_name", "created_at", "updated_at")


@admin.register(DisciplineAlias)
class DisciplineAliasAdmin(admin.ModelAdmin):
    list_display = ("name", "alias_key", "discipline", "created_at")
    search_fields = ("name", "alias_key", "discipline__name")
    ordering = ("alias_key",)
    list_select_related = ("discipline",)
    readonly_fields = ("id", "name", "alias_key", "discipline", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SkillAlias)
class SkillAliasAdmin(admin.ModelAdmin):
    list_display = ("name", "alias_key", "skill", "created_at")
    search_fields = ("name", "alias_key", "skill__name")
    ordering = ("alias_key",)
    list_select_related = ("skill",)
    readonly_fields = ("id", "name", "alias_key", "skill", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class StudentSkillInline(admin.TabularInline):
    model = StudentSkill
    fk_name = "student"
    fields = ("skill", "created_at")
    readonly_fields = ("created_at",)
    autocomplete_fields = ("skill",)
    extra = 0


@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
    list_display = (
        "first_name",
        "last_name",
        "user",
        "institution",
        "discipline",
        "level",
        "verification_status",
        "risk_level",
        "created_at",
    )
    list_filter = (
        "verification_status",
        "risk_level",
        "discipline",
        "level",
        "created_at",
    )
    search_fields = (
        "first_name",
        "last_name",
        "user__email",
        "institution",
        "student_id",
    )
    autocomplete_fields = ("user", "discipline", "photo", "resume")
    readonly_fields = ("id", "profile_strength", "created_at", "updated_at")
    inlines = (StudentSkillInline,)
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("user", "discipline")
    list_per_page = 50


@admin.register(StudentSkill)
class StudentSkillAdmin(admin.ModelAdmin):
    list_display = ("student", "skill", "created_at")
    list_filter = ("skill", "created_at")
    search_fields = (
        "student__first_name",
        "student__last_name",
        "student__user__email",
        "skill__name",
    )
    autocomplete_fields = ("student", "skill")
    readonly_fields = ("id", "created_at")
    ordering = ("-created_at",)
    list_select_related = ("student", "skill")
