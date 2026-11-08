from django.contrib.auth.password_validation import validate_password
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import serializers

from accounts.fields import StudentEmailField
from accounts.models import User
from accounts.serializers import UserSerializer
from accounts.services import (
    EmailAlreadyRegistered,
    OTPVerificationError,
    consume_student_otp,
)
from common.models import VerificationStatus
from common.serializers import DocumentSerializer
from profiles.fields import DisciplineField, SkillNamesField
from profiles.models import Discipline, Skill, StudentProfile, StudentSkill


class DisciplineSerializer(serializers.ModelSerializer):
    class Meta:
        model = Discipline
        fields = ["id", "name", "slug"]
        read_only_fields = fields


class SkillSerializer(serializers.ModelSerializer):
    class Meta:
        model = Skill
        fields = ["id", "name", "slug"]
        read_only_fields = fields


class StudentSkillSerializer(serializers.ModelSerializer):
    skill = SkillSerializer(read_only=True)
    skill_id = serializers.PrimaryKeyRelatedField(
        source="skill",
        queryset=Skill.objects.all(),
        write_only=True,
    )

    class Meta:
        model = StudentSkill
        fields = ["id", "skill", "skill_id", "created_at"]
        read_only_fields = ["id", "skill", "created_at"]


class StudentProfileSerializer(serializers.ModelSerializer):
    """Profile payload used by the student's own profile screens."""

    user_id = serializers.UUIDField(read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    discipline = DisciplineField()
    skills = SkillNamesField(required=False)
    work_styles = serializers.ListField(
        child=serializers.CharField(),
        required=False,
    )
    photo = DocumentSerializer(read_only=True)
    resume = DocumentSerializer(read_only=True)
    profile_strength = serializers.IntegerField(read_only=True)

    class Meta:
        model = StudentProfile
        fields = [
            "id",
            "user_id",
            "email",
            "first_name",
            "last_name",
            "phone",
            "location",
            "institution",
            "discipline",
            "level",
            "graduation_year",
            "student_id",
            "start_date",
            "duration",
            "work_styles",
            "portfolio",
            "interests",
            "skills",
            "photo",
            "resume",
            "verification_status",
            "risk_level",
            "profile_strength",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "user_id",
            "photo",
            "resume",
            "verification_status",
            "risk_level",
            "profile_strength",
            "deleted_at",
            "created_at",
            "updated_at",
        ]

    def validate_level(self, value):
        if value < 100 or value > 700:
            raise serializers.ValidationError("Level must be between 100 and 700.")
        return value

    def validate_graduation_year(self, value):
        if value is not None and (value < 2000 or value > 2100):
            raise serializers.ValidationError("Enter a valid graduation year.")
        return value

    def create(self, validated_data):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            raise serializers.ValidationError(
                "An authenticated user is required to create a student profile."
            )

        skills = validated_data.pop("skills", [])
        with transaction.atomic():
            profile = StudentProfile.objects.create(user=user, **validated_data)
            if skills:
                profile.skills.set(skills)
        return profile

    def update(self, instance, validated_data):
        skills = validated_data.pop("skills", None)
        with transaction.atomic():
            instance = super().update(instance, validated_data)
            if skills is not None:
                instance.skills.set(skills)
        return instance


class StudentProfileSummarySerializer(serializers.ModelSerializer):
    """Privacy-limited student payload for companies and admin queues."""

    user_id = serializers.UUIDField(read_only=True)
    full_name = serializers.CharField(read_only=True)
    discipline = serializers.CharField(source="discipline.name", read_only=True)
    skills = serializers.SerializerMethodField()
    photo_url = serializers.SerializerMethodField()

    class Meta:
        model = StudentProfile
        fields = [
            "id",
            "user_id",
            "first_name",
            "last_name",
            "full_name",
            "institution",
            "discipline",
            "level",
            "location",
            "skills",
            "photo_url",
            "verification_status",
            "risk_level",
        ]
        read_only_fields = fields

    def get_skills(self, obj) -> list[str]:
        return [skill.name for skill in obj.skills.all()]

    def get_photo_url(self, obj) -> str | None:
        if not obj.photo or not obj.photo.file:
            return None
        try:
            url = obj.photo.file.url
        except ValueError:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url


class StudentOnboardingSerializer(serializers.Serializer):
    """Create a student user and profile after OTP verification."""

    email = StudentEmailField()
    code = serializers.CharField(min_length=6, max_length=6, write_only=True)
    password = serializers.CharField(
        min_length=8,
        required=False,
        write_only=True,
        trim_whitespace=False,
    )
    first_name = serializers.CharField(max_length=100)
    last_name = serializers.CharField(max_length=100)
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True)
    location = serializers.CharField(max_length=160)
    institution = serializers.CharField(max_length=200)
    discipline = DisciplineField()
    level = serializers.IntegerField(min_value=100, max_value=700)
    graduation_year = serializers.IntegerField(
        min_value=2000,
        max_value=2100,
        required=False,
        allow_null=True,
    )
    student_id = serializers.CharField(max_length=80, required=False, allow_blank=True)
    start_date = serializers.DateField(required=False, allow_null=True)
    duration = serializers.CharField(max_length=80, required=False, allow_blank=True)
    work_styles = serializers.ListField(
        child=serializers.CharField(),
        required=False,
    )
    skills = SkillNamesField(required=False)
    portfolio = serializers.URLField(required=False, allow_blank=True)
    interests = serializers.CharField(required=False, allow_blank=True)

    def validate_code(self, value):
        if not value.isdigit():
            raise serializers.ValidationError(
                "The verification code must contain digits only."
            )
        return value

    def validate(self, attrs):
        password = attrs.get("password")
        if password:
            candidate = User(
                email=attrs.get("email", ""),
                first_name=attrs.get("first_name", ""),
                last_name=attrs.get("last_name", ""),
            )
            validate_password(password, user=candidate)
        return attrs

    def create(self, validated_data):
        email = validated_data.pop("email")
        code = validated_data.pop("code")
        password = validated_data.pop("password", None)
        skills = validated_data.pop("skills", [])
        otp_id = self.context.get("otp_id")
        if not otp_id:
            raise OTPVerificationError

        try:
            with transaction.atomic():
                otp = consume_student_otp(otp_id, email, code)
                if User.objects.filter(email__iexact=email).exists():
                    raise EmailAlreadyRegistered

                user = User.objects.create_user(
                    email=email,
                    password=password,
                    role=User.Role.STUDENT,
                    first_name=validated_data.get("first_name", ""),
                    last_name=validated_data.get("last_name", ""),
                    email_verified_at=timezone.now(),
                )
                # Product decision: students are considered trusted at
                # sign-up, so a fresh profile starts Verified instead of
                # queuing for admin verification review.
                profile = StudentProfile.objects.create(
                    user=user,
                    verification_status=VerificationStatus.VERIFIED,
                    **validated_data,
                )
                if skills:
                    profile.skills.set(skills)

                otp.user = user
                otp.mark_consumed(save=False)
                otp.save(update_fields=["user", "consumed_at"])
                return profile
        except IntegrityError as exc:
            raise EmailAlreadyRegistered from exc


class StudentOnboardingResponseSerializer(serializers.Serializer):
    user = UserSerializer(read_only=True)
    student = StudentProfileSerializer(read_only=True)
    access = serializers.CharField(read_only=True)
    refresh = serializers.CharField(read_only=True)
