from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from accounts.fields import StudentEmailField
from accounts.models import OTPCode, User
from accounts.validators import normalize_student_email


class UserSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "first_name",
            "last_name",
            "full_name",
            "role",
            "account_status",
            "email_verified_at",
            "is_active",
            "is_staff",
            "date_joined",
            "last_login",
        ]
        read_only_fields = fields

    def get_full_name(self, obj) -> str:
        return obj.get_full_name() or obj.email


class MeSerializer(UserSerializer):
    student_profile_id = serializers.SerializerMethodField()
    company_id = serializers.SerializerMethodField()

    class Meta(UserSerializer.Meta):
        fields = UserSerializer.Meta.fields + [
            "student_profile_id",
            "company_id",
        ]

    def get_student_profile_id(self, obj) -> str | None:
        profile = getattr(obj, "student_profile", None)
        return str(profile.id) if profile else None

    def get_company_id(self, obj) -> str | None:
        company = getattr(obj, "company", None)
        return str(company.id) if company else None


class AdminAccountSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    type = serializers.SerializerMethodField()
    verification_status = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "name",
            "email",
            "type",
            "role",
            "account_status",
            "verification_status",
            "is_active",
            "date_joined",
            "last_login",
        ]
        read_only_fields = fields

    def get_name(self, obj) -> str:
        student = getattr(obj, "student_profile", None)
        if student:
            return student.full_name
        company = getattr(obj, "company", None)
        if company:
            return company.name
        return obj.get_full_name() or obj.email

    def get_type(self, obj) -> str:
        return obj.role

    def get_verification_status(self, obj):
        student = getattr(obj, "student_profile", None)
        if student:
            return student.verification_status
        company = getattr(obj, "company", None)
        if company:
            return company.verification_status
        return None


class AdminAccountUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["role", "account_status", "is_active"]


class StudentOTPRequestSerializer(serializers.Serializer):
    email = StudentEmailField()


class StudentOTPVerifySerializer(serializers.Serializer):
    email = StudentEmailField()
    code = serializers.CharField(min_length=6, max_length=6, write_only=True)

    def validate_code(self, value):
        if not value.isdigit():
            raise serializers.ValidationError(
                "The verification code must contain digits only."
            )
        return value


class DetailSerializer(serializers.Serializer):
    detail = serializers.CharField(read_only=True)


class AuthTokensSerializer(serializers.Serializer):
    access = serializers.CharField(read_only=True)
    refresh = serializers.CharField(read_only=True)


class SignInResponseSerializer(AuthTokensSerializer):
    user = UserSerializer(read_only=True)


class OTPRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()
    track = serializers.ChoiceField(choices=OTPCode.Track.choices)

    def validate_email(self, value):
        return value.lower()

    def validate(self, attrs):
        if attrs["track"] == OTPCode.Track.STUDENT:
            try:
                attrs["email"] = normalize_student_email(attrs["email"])
            except DjangoValidationError as exc:
                raise serializers.ValidationError(
                    {"email": exc.messages}
                ) from exc
        return attrs


class OTPVerifySerializer(serializers.Serializer):
    email = serializers.EmailField()
    track = serializers.ChoiceField(choices=OTPCode.Track.choices)
    code = serializers.CharField(min_length=6, max_length=6)

    def validate_email(self, value):
        return value.lower()

    def validate_code(self, value):
        if not value.isdigit():
            raise serializers.ValidationError("The verification code must contain digits only.")
        return value

    def validate(self, attrs):
        if attrs["track"] == OTPCode.Track.STUDENT:
            try:
                attrs["email"] = normalize_student_email(attrs["email"])
            except DjangoValidationError as exc:
                raise serializers.ValidationError(
                    {"email": exc.messages}
                ) from exc
        return attrs


class OTPRequestResponseSerializer(serializers.Serializer):
    detail = serializers.CharField(read_only=True)
    expires_in = serializers.IntegerField(read_only=True)
    resend_after = serializers.IntegerField(read_only=True)


class OTPVerifyResponseSerializer(serializers.Serializer):
    verification_token = serializers.CharField(read_only=True)
    is_new_user = serializers.BooleanField(read_only=True)


class SignInSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_email(self, value):
        return value.lower()


class StudentSignInSerializer(SignInSerializer):
    email = StudentEmailField()


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return value.lower()


class StudentPasswordResetRequestSerializer(PasswordResetRequestSerializer):
    email = StudentEmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True, trim_whitespace=False)


class SignOutSerializer(serializers.Serializer):
    refresh = serializers.CharField(write_only=True)


class MeUpdateSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=150, required=False)
    last_name = serializers.CharField(max_length=150, required=False)
    email = serializers.EmailField(required=False)
    current_password = serializers.CharField(
        write_only=True,
        required=False,
        trim_whitespace=False,
    )
    new_password = serializers.CharField(
        write_only=True,
        required=False,
        trim_whitespace=False,
    )

    def validate_email(self, value):
        return value.lower()

    def validate(self, attrs):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        new_password = attrs.get("new_password")
        current_password = attrs.get("current_password")

        if new_password or current_password:
            if not current_password or not user.check_password(current_password):
                raise serializers.ValidationError(
                    {"current_password": "Current password is incorrect."}
                )
            if not new_password:
                raise serializers.ValidationError(
                    {"new_password": "Enter a new password."}
                )
            validate_password(new_password, user=user)

        return attrs
