from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import APIException

from applications.models import Application, ApplicationEvent
from common.models import Document
from common.serializers import DocumentSerializer
from placements.models import Placement
from placements.serializers import PlacementSerializer
from profiles.serializers import StudentProfileSummarySerializer


class ApplicationEventSerializer(serializers.ModelSerializer):
    actor_email = serializers.SerializerMethodField()

    class Meta:
        model = ApplicationEvent
        fields = [
            "id",
            "from_status",
            "to_status",
            "actor_email",
            "reason",
            "created_at",
        ]
        read_only_fields = fields

    def get_actor_email(self, obj) -> str | None:
        return obj.actor.email if obj.actor_id else None


class ApplicationSerializer(serializers.ModelSerializer):
    placement = PlacementSerializer(read_only=True)
    placement_id = serializers.UUIDField(read_only=True)
    student = StudentProfileSummarySerializer(read_only=True)
    student_id = serializers.UUIDField(read_only=True)
    resume = DocumentSerializer(read_only=True)
    decided_by_email = serializers.SerializerMethodField()
    events = ApplicationEventSerializer(many=True, read_only=True)

    class Meta:
        model = Application
        fields = [
            "id",
            "placement",
            "placement_id",
            "student",
            "student_id",
            "resume",
            "status",
            "applied_at",
            "status_changed_at",
            "decided_by_email",
            "rejection_reason",
            "cover_note",
            "events",
            "updated_at",
        ]
        read_only_fields = fields

    def get_decided_by_email(self, obj) -> str | None:
        return obj.decided_by.email if obj.decided_by_id else None


class ApplicationCreateSerializer(serializers.Serializer):
    placement_id = serializers.PrimaryKeyRelatedField(
        source="placement",
        queryset=Placement.objects.all(),
    )
    resume_id = serializers.PrimaryKeyRelatedField(
        source="resume",
        queryset=Document.objects.all(),
        required=False,
        allow_null=True,
    )

    def validate(self, attrs):
        request = self.context.get("request")
        student = getattr(
            getattr(request, "user", None),
            "student_profile",
            None,
        )
        if not student:
            raise serializers.ValidationError(
                "Only students with a profile can apply to placements."
            )
        if student.deleted_at:
            raise serializers.ValidationError("This student profile is no longer active.")

        placement = attrs["placement"]
        if placement.status != Placement.Status.ACTIVE or not placement.is_open:
            raise serializers.ValidationError("This placement is not accepting applications.")
        if student.discipline_id != placement.discipline_id:
            raise serializers.ValidationError(
                "This placement is not open to your discipline."
            )
        if student.level < placement.level:
            raise serializers.ValidationError(
                f"This placement requires level {placement.level} or above."
            )
        if Application.objects.filter(placement=placement, student=student).exists():
            raise DuplicateApplication

        resume = attrs.get("resume") or student.resume
        if resume is None:
            raise serializers.ValidationError(
                {"resume_id": "Upload a resume before applying."}
            )
        if resume.owner_id != student.user_id:
            raise serializers.ValidationError(
                {"resume_id": "You can only attach your own resume."}
            )
        if resume.kind != Document.Kind.RESUME:
            raise serializers.ValidationError(
                {"resume_id": "The selected document is not a resume."}
            )
        attrs["resume"] = resume
        return attrs

    def create(self, validated_data):
        request = self.context["request"]
        student = request.user.student_profile
        application = Application.objects.create(student=student, **validated_data)
        ApplicationEvent.objects.create(
            application=application,
            from_status="",
            to_status=Application.Status.SUBMITTED,
            actor=request.user,
            reason="Application submitted.",
        )
        return application


class InvalidApplicationStatus(APIException):
    status_code = 409
    default_detail = "This application status change is not allowed."
    default_code = "invalid_status_transition"


class DuplicateApplication(APIException):
    status_code = 409
    default_detail = "You have already applied to this placement."
    default_code = "duplicate_application"


class ApplicationStatusUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Application.Status.choices)
    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=2000,
    )

    def validate_status(self, value):
        if value == Application.Status.SUBMITTED:
            raise InvalidApplicationStatus(
                "Applications cannot be moved back to Submitted."
            )
        if value == Application.Status.WITHDRAWN:
            raise InvalidApplicationStatus(
                "Only students can withdraw applications."
            )
        if self.instance and value == self.instance.status:
            raise InvalidApplicationStatus(
                "The application is already in this status."
            )
        if self.instance and self.instance.status == Application.Status.WITHDRAWN:
            raise InvalidApplicationStatus(
                "A withdrawn application cannot be changed."
            )
        return value

    def update(self, instance, validated_data):
        request = self.context.get("request")
        actor = getattr(request, "user", None)
        if actor is not None and not getattr(actor, "is_authenticated", False):
            actor = None

        with transaction.atomic():
            instance.set_status(
                validated_data["status"],
                actor=actor,
                reason=validated_data.get("reason", ""),
            )
        return instance


class ApplicationSummarySerializer(serializers.Serializer):
    total = serializers.IntegerField()
    pending = serializers.IntegerField()
    under_review = serializers.IntegerField()
    shortlisted = serializers.IntegerField()
    accepted = serializers.IntegerField()
    rejected = serializers.IntegerField()
    withdrawn = serializers.IntegerField()


class ContactDetailsSerializer(serializers.Serializer):
    email = serializers.EmailField()
    phone = serializers.CharField(allow_blank=True)
