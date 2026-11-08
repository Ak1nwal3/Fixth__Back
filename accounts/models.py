import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone


class UserManager(BaseUserManager):
    """Manager for a user model that uses email instead of username."""

    use_in_migrations = True

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Users must have an email address.")

        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", User.Role.ADMIN)
        extra_fields.setdefault("account_status", User.AccountStatus.ACTIVE)
        extra_fields.setdefault("email_verified_at", timezone.now())

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superusers must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superusers must have is_superuser=True.")

        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
    """Application user with a single marketplace role."""

    class Role(models.TextChoices):
        STUDENT = "student", "Student"
        COMPANY = "company", "Company"
        ADMIN = "admin", "Admin"

    class AccountStatus(models.TextChoices):
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        DELETED = "deleted", "Deleted"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = None
    email = models.EmailField(unique=True)
    role = models.CharField(max_length=20, choices=Role.choices)
    account_status = models.CharField(
        max_length=20,
        choices=AccountStatus.choices,
        default=AccountStatus.ACTIVE,
    )
    email_verified_at = models.DateTimeField(null=True, blank=True)
    suspended_at = models.DateTimeField(null=True, blank=True)
    suspension_reason = models.TextField(blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        ordering = ["email"]
        constraints = [
            models.UniqueConstraint(
                Lower("email"),
                name="accounts_user_email_ci",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.strip().lower()
        return super().save(*args, **kwargs)

    @property
    def is_student(self) -> bool:
        return self.role == self.Role.STUDENT

    @property
    def is_company(self) -> bool:
        return self.role == self.Role.COMPANY

    @property
    def is_admin(self) -> bool:
        return self.role == self.Role.ADMIN or self.is_superuser

    @property
    def is_email_verified(self) -> bool:
        return self.email_verified_at is not None

    def suspend(self, reason: str = "", *, save: bool = True):
        self.account_status = self.AccountStatus.SUSPENDED
        self.is_active = False
        self.suspended_at = timezone.now()
        self.suspension_reason = reason
        if save:
            self.save(
                update_fields=[
                    "account_status",
                    "is_active",
                    "suspended_at",
                    "suspension_reason",
                ]
            )
        return self

    def restore(self, *, save: bool = True):
        self.account_status = self.AccountStatus.ACTIVE
        self.is_active = True
        self.suspended_at = None
        self.suspension_reason = ""
        if save:
            self.save(
                update_fields=[
                    "account_status",
                    "is_active",
                    "suspended_at",
                    "suspension_reason",
                ]
            )
        return self

    def __str__(self) -> str:
        return self.email


def otp_expiry():
    return timezone.now() + timedelta(minutes=10)


class OTPCode(models.Model):
    """Hashed one-time code used for onboarding, login, and password reset."""

    class Track(models.TextChoices):
        STUDENT = "student", "Student"
        ORGANIZATION = "organization", "Organization"

    class Purpose(models.TextChoices):
        ONBOARDING = "onboarding", "Onboarding"
        PASSWORD_RESET = "password_reset", "Password reset"
        LOGIN = "login", "Login"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="otp_codes",
        null=True,
        blank=True,
    )
    email = models.EmailField()
    track = models.CharField(max_length=20, choices=Track.choices)
    purpose = models.CharField(
        max_length=30,
        choices=Purpose.choices,
        default=Purpose.ONBOARDING,
    )
    code_hash = models.CharField(max_length=255)
    expires_at = models.DateTimeField(default=otp_expiry)
    consumed_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    ip_address = models.GenericIPAddressField(
        protocol="both",
        unpack_ipv4=True,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["email", "track", "purpose", "created_at"],
                name="otp_lookup_idx",
            ),
            models.Index(fields=["expires_at"], name="otp_expiry_idx"),
        ]

    def set_code(self, raw_code: str):
        self.code_hash = make_password(raw_code)

    def check_code(self, raw_code: str) -> bool:
        return check_password(raw_code, self.code_hash)

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= timezone.now()

    @property
    def is_usable(self) -> bool:
        return self.consumed_at is None and not self.is_expired and self.attempts < 5

    def mark_consumed(self, *, save: bool = True):
        self.consumed_at = timezone.now()
        if save:
            self.save(update_fields=["consumed_at"])
        return self

    def register_failed_attempt(self, *, save: bool = True):
        self.attempts += 1
        if save:
            self.save(update_fields=["attempts"])
        return self

    def __str__(self) -> str:
        return f"{self.email} ({self.purpose})"
