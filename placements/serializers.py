from rest_framework import serializers
from rest_framework.exceptions import APIException

from common.models import VerificationStatus
from placements.models import Placement, SavedPlacement
from profiles.fields import DisciplineField


class InvalidPlacementStatus(APIException):
    status_code = 409
    default_detail = "This placement status change is not allowed."
    default_code = "invalid_status_transition"


def _student_for_request(request):
    if not request or not getattr(request.user, "is_authenticated", False):
        return None
    return getattr(request.user, "student_profile", None)


class PlacementSerializer(serializers.ModelSerializer):
    """Read representation shared by discovery and detail screens."""

    company = serializers.CharField(source="company.name", read_only=True)
    company_id = serializers.UUIDField(read_only=True)
    company_slug = serializers.CharField(source="company.slug", read_only=True)
    company_logo_url = serializers.SerializerMethodField()
    company_verified = serializers.SerializerMethodField()
    discipline = serializers.CharField(source="discipline.name", read_only=True)
    stipend = serializers.SerializerMethodField()
    reports = serializers.IntegerField(source="reports_count", read_only=True)
    match = serializers.SerializerMethodField()
    is_saved = serializers.SerializerMethodField()
    has_applied = serializers.SerializerMethodField()

    class Meta:
        model = Placement
        fields = [
            "id",
            "slug",
            "title",
            "company",
            "company_id",
            "company_slug",
            "company_logo_url",
            "company_verified",
            "location",
            "work_style",
            "duration",
            "discipline",
            "level",
            "type",
            "stipend",
            "stipend_amount",
            "currency",
            "description",
            "responsibilities",
            "requirements",
            "status",
            "reports",
            "expires_at",
            "match",
            "is_saved",
            "has_applied",
            "published_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_company_logo_url(self, obj) -> str | None:
        logo = getattr(obj.company, "logo", None)
        if not logo or not logo.file:
            return None
        try:
            url = logo.file.url
        except ValueError:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url

    def get_company_verified(self, obj) -> bool:
        return obj.company.verification_status == VerificationStatus.VERIFIED

    def get_stipend(self, obj) -> str | None:
        if obj.stipend_amount is None:
            return None
        symbol = "₦" if obj.currency == "NGN" else f"{obj.currency} "
        return f"{symbol}{obj.stipend_amount:,.0f}"

    def get_match(self, obj) -> int | None:
        """Placeholder score until the matching rules are confirmed.

        It rewards discipline alignment and level eligibility, which mirrors
        the only matching logic the current frontend exposes.
        """
        student = _student_for_request(self.context.get("request"))
        if not student or not student.discipline_id:
            return None
        score = 50
        if student.discipline_id == obj.discipline_id:
            score += 30
        if student.level >= obj.level:
            score += 20
        return min(score, 100)

    def get_is_saved(self, obj) -> bool:
        student = _student_for_request(self.context.get("request"))
        if not student:
            return False
        return obj.saved_by.filter(student=student).exists()

    def get_has_applied(self, obj) -> bool:
        student = _student_for_request(self.context.get("request"))
        if not student:
            return False
        return obj.applications.filter(student=student).exists()


class PlacementDetailSerializer(PlacementSerializer):
    eligibility = serializers.SerializerMethodField()
    can_apply = serializers.SerializerMethodField()

    class Meta(PlacementSerializer.Meta):
        fields = PlacementSerializer.Meta.fields + ["eligibility", "can_apply"]

    def get_eligibility(self, obj) -> dict:
        student = _student_for_request(self.context.get("request"))
        required_discipline = obj.discipline.name if obj.discipline else None
        base = {
            "eligible": False,
            "reason": "Sign in as a student to check eligibility.",
            "required_level": obj.level,
            "required_discipline": required_discipline,
        }
        if not student:
            return base

        reasons = []
        if student.deleted_at:
            reasons.append("This student profile is no longer active.")
        if student.discipline_id != obj.discipline_id:
            reasons.append(f"This placement is open to {required_discipline} students.")
        if student.level < obj.level:
            reasons.append(f"This placement requires level {obj.level} or above.")

        return {
            "eligible": not reasons,
            "reason": " ".join(reasons) if reasons else "You meet the eligibility requirements.",
            "required_level": obj.level,
            "required_discipline": required_discipline,
        }

    def get_can_apply(self, obj) -> bool:
        student = _student_for_request(self.context.get("request"))
        if not student:
            return False
        eligibility = self.get_eligibility(obj)
        return bool(
            eligibility["eligible"]
            and obj.status == Placement.Status.ACTIVE
            and obj.is_open
            and student.resume_id
            and not student.deleted_at
            and not obj.applications.filter(student=student).exists()
        )


class CompanyPlacementSerializer(PlacementSerializer):
    """Company listing representation with per-placement application metrics."""

    applicants_count = serializers.IntegerField(read_only=True)
    new_applicants_count = serializers.IntegerField(read_only=True)
    shortlisted_count = serializers.IntegerField(read_only=True)

    class Meta(PlacementSerializer.Meta):
        fields = PlacementSerializer.Meta.fields + [
            "applicants_count",
            "new_applicants_count",
            "shortlisted_count",
        ]


class PlacementWriteSerializer(serializers.ModelSerializer):
    discipline = DisciplineField()

    # Companies may only move listings through these states. Admin-only states
    # (Expired, Unpublished) are not writable through the company API.
    STATUS_TRANSITIONS = {
        Placement.Status.DRAFT: {
            Placement.Status.DRAFT,
            Placement.Status.ACTIVE,
        },
        Placement.Status.ACTIVE: {
            Placement.Status.ACTIVE,
            Placement.Status.CLOSED,
        },
        Placement.Status.CLOSED: {
            Placement.Status.CLOSED,
            Placement.Status.ACTIVE,
        },
        Placement.Status.EXPIRED: {Placement.Status.EXPIRED},
        Placement.Status.UNPUBLISHED: {Placement.Status.UNPUBLISHED},
    }

    class Meta:
        model = Placement
        fields = [
            "title",
            "location",
            "work_style",
            "duration",
            "discipline",
            "level",
            "type",
            "stipend_amount",
            "currency",
            "description",
            "responsibilities",
            "requirements",
            "status",
            "expires_at",
        ]

    def validate_type(self, value):
        return value or None

    def validate_level(self, value):
        if value < 100 or value > 700:
            raise serializers.ValidationError("Level must be between 100 and 700.")
        return value

    def validate_stipend_amount(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError("Stipend cannot be negative.")
        return value

    def validate_currency(self, value):
        value = (value or "NGN").upper()
        if len(value) != 3 or not value.isalpha():
            raise serializers.ValidationError(
                "Currency must be a three-letter ISO code."
            )
        return value

    def validate_status(self, value):
        if self.instance is None:
            if value not in (Placement.Status.DRAFT, Placement.Status.ACTIVE):
                raise serializers.ValidationError(
                    "New placements may only be saved as a draft or published."
                )
            return value

        allowed = self.STATUS_TRANSITIONS.get(
            self.instance.status,
            {self.instance.status},
        )
        if value not in allowed:
            raise InvalidPlacementStatus(
                f"A {self.instance.status.lower()} placement cannot be changed "
                f"to {value.lower()}."
            )
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        status = attrs.get(
            "status",
            self.instance.status if self.instance else Placement.Status.DRAFT,
        )
        if status != Placement.Status.ACTIVE:
            return attrs

        required_fields = [
            "title",
            "location",
            "work_style",
            "duration",
            "discipline",
            "level",
            "description",
            "responsibilities",
            "requirements",
        ]
        errors = {}
        for field in required_fields:
            value = attrs.get(
                field,
                getattr(self.instance, field, None) if self.instance else None,
            )
            if value in (None, "", []):
                errors[field] = "This field is required to publish a placement."
        stipend = attrs.get(
            "stipend_amount",
            getattr(self.instance, "stipend_amount", None) if self.instance else None,
        )
        # The monthly stipend is optional: an unpaid internship publishes with
        # stipend_amount null. Only a negative amount is invalid.
        if stipend is not None and stipend < 0:
            errors["stipend_amount"] = "The monthly stipend cannot be negative."
        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    def create(self, validated_data):
        company = self.context.get("company")
        if company is None:
            raise serializers.ValidationError(
                {"detail": "An active company profile is required."}
            )
        validated_data["company"] = company
        request = self.context.get("request")
        if request and getattr(request.user, "is_authenticated", False):
            validated_data["created_by"] = request.user
        return super().create(validated_data)

    def update(self, instance, validated_data):
        requested_status = validated_data.pop("status", instance.status)
        instance = super().update(instance, validated_data)
        if requested_status != instance.status:
            instance.set_status(requested_status)
        return instance


class SavedPlacementSerializer(serializers.ModelSerializer):
    placement = PlacementSerializer(read_only=True)
    placement_id = serializers.PrimaryKeyRelatedField(
        source="placement",
        queryset=Placement.objects.all(),
        write_only=True,
    )

    class Meta:
        model = SavedPlacement
        fields = ["id", "placement", "placement_id", "created_at"]
        read_only_fields = ["id", "placement", "created_at"]


class PlacementFacetsSerializer(serializers.Serializer):
    disciplines = serializers.ListField(child=serializers.CharField())
    locations = serializers.ListField(child=serializers.CharField())
    durations = serializers.ListField(child=serializers.CharField())
    work_styles = serializers.ListField(child=serializers.CharField())
