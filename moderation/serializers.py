from django.db.models import F
from rest_framework import serializers

from moderation.models import PlacementReport
from placements.models import Placement
from placements.serializers import PlacementSerializer


class PlacementReportSerializer(serializers.ModelSerializer):
    placement = PlacementSerializer(read_only=True)
    placement_id = serializers.UUIDField(read_only=True)
    reporter_email = serializers.SerializerMethodField()
    resolved_by_email = serializers.SerializerMethodField()

    class Meta:
        model = PlacementReport
        fields = [
            "id",
            "placement",
            "placement_id",
            "reporter_email",
            "reason",
            "details",
            "status",
            "created_at",
            "resolved_at",
            "resolved_by_email",
            "resolution_note",
        ]
        read_only_fields = fields

    def get_reporter_email(self, obj):
        return obj.reporter.email if obj.reporter_id else None

    def get_resolved_by_email(self, obj):
        return obj.resolved_by.email if obj.resolved_by_id else None


class PlacementReportCreateSerializer(serializers.Serializer):
    placement_id = serializers.PrimaryKeyRelatedField(
        source="placement",
        queryset=Placement.objects.all(),
    )
    reason = serializers.ChoiceField(choices=PlacementReport.Reason.choices)
    details = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user and getattr(user, "is_authenticated", False):
            exists = PlacementReport.objects.filter(
                placement=attrs["placement"],
                reporter=user,
                status=PlacementReport.Status.OPEN,
            ).exists()
            if exists:
                raise serializers.ValidationError(
                    "You already have an open report for this placement."
                )
        return attrs

    def create(self, validated_data):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        reporter = user if user and getattr(user, "is_authenticated", False) else None
        report = PlacementReport.objects.create(reporter=reporter, **validated_data)
        Placement.objects.filter(pk=report.placement_id).update(
            reports_count=F("reports_count") + 1
        )
        return report


class AdminActionSerializer(serializers.Serializer):
    reason = serializers.CharField(min_length=5, max_length=2000)


class AdminPlacementSerializer(PlacementSerializer):
    """Placement payload for the admin moderation table."""

    report_count = serializers.IntegerField(source="reports_count", read_only=True)

    class Meta(PlacementSerializer.Meta):
        fields = PlacementSerializer.Meta.fields + ["report_count"]
