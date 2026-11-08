from rest_framework import serializers

from common.models import Document, VerificationStatus
from common.serializers import DocumentSerializer
from companies.models import Company
from companies.serializers import CompanySummarySerializer
from profiles.models import StudentProfile
from profiles.serializers import StudentProfileSummarySerializer
from verification.models import VerificationRequest


class VerificationRequestSerializer(serializers.ModelSerializer):
    student = StudentProfileSummarySerializer(read_only=True)
    company = CompanySummarySerializer(read_only=True)
    subject_id = serializers.SerializerMethodField()
    subject_label = serializers.CharField(read_only=True)
    evidence = DocumentSerializer(many=True, read_only=True)
    decided_by_email = serializers.SerializerMethodField()

    class Meta:
        model = VerificationRequest
        fields = [
            "id",
            "kind",
            "student",
            "company",
            "subject_id",
            "subject_label",
            "status",
            "risk_level",
            "submitted_at",
            "decided_by_email",
            "decided_at",
            "decision_reason",
            "evidence",
            "updated_at",
        ]
        read_only_fields = fields

    def get_subject_id(self, obj) -> str:
        return str(obj.student_id or obj.company_id)

    def get_decided_by_email(self, obj) -> str | None:
        return obj.decided_by.email if obj.decided_by_id else None


class StudentVerificationSubmitSerializer(serializers.Serializer):
    """Submit the authenticated student's profile for trust-and-safety review.

    The product intentionally reviews the profile already collected during
    onboarding; no student-supplied target IDs or documents are accepted here.
    """

    def create(self, validated_data):
        student = self.context["student"]
        return VerificationRequest.objects.create(
            kind=VerificationRequest.Kind.STUDENT,
            student=student,
            status=VerificationStatus.PENDING,
            risk_level=student.risk_level,
        )


class VerificationRequestCreateSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=VerificationRequest.Kind.choices)
    student_id = serializers.PrimaryKeyRelatedField(
        source="student",
        queryset=StudentProfile.objects.all(),
        required=False,
        allow_null=True,
    )
    company_id = serializers.PrimaryKeyRelatedField(
        source="company",
        queryset=Company.objects.all(),
        required=False,
        allow_null=True,
    )
    evidence_ids = serializers.PrimaryKeyRelatedField(
        source="evidence",
        queryset=Document.objects.all(),
        many=True,
        required=False,
    )

    def validate(self, attrs):
        student = attrs.get("student")
        company = attrs.get("company")
        if bool(student) == bool(company):
            raise serializers.ValidationError(
                "Provide exactly one of student_id or company_id."
            )
        if student and attrs["kind"] != VerificationRequest.Kind.STUDENT:
            raise serializers.ValidationError(
                {"kind": "Student requests must use kind='student'."}
            )
        if company and attrs["kind"] != VerificationRequest.Kind.COMPANY:
            raise serializers.ValidationError(
                {"kind": "Company requests must use kind='company'."}
            )
        if student and VerificationRequest.objects.filter(
            student=student,
            status=VerificationStatus.PENDING,
        ).exists():
            raise serializers.ValidationError(
                "This student already has a pending verification request."
            )
        if company and VerificationRequest.objects.filter(
            company=company,
            status=VerificationStatus.PENDING,
        ).exists():
            raise serializers.ValidationError(
                "This company already has a pending verification request."
            )
        return attrs

    def create(self, validated_data):
        evidence = validated_data.pop("evidence", [])
        request = VerificationRequest.objects.create(**validated_data)
        if evidence:
            request.evidence.set(evidence)
        return request


class VerificationDecisionSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(
        choices=[
            ("approve", "Approve"),
            ("reject", "Reject"),
        ]
    )
    reason = serializers.CharField(min_length=5, max_length=2000)


class VerificationSummarySerializer(serializers.Serializer):
    pending_students = serializers.IntegerField()
    pending_companies = serializers.IntegerField()
    needs_attention = serializers.IntegerField()
    approved_today = serializers.IntegerField()
