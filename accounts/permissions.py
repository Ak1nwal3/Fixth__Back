from rest_framework.permissions import BasePermission

from accounts.models import User


class IsVerifiedStudent(BasePermission):
    """Allow only active students whose institutional email is verified."""

    message = "A verified student account is required."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        return bool(
            user
            and user.is_authenticated
            and user.is_active
            and user.role == User.Role.STUDENT
            and user.account_status == User.AccountStatus.ACTIVE
            and user.email_verified_at is not None
        )


class IsVerifiedCompany(BasePermission):
    """Allow only active company accounts whose work email is verified."""

    message = "A verified organization account is required."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        return bool(
            user
            and user.is_authenticated
            and user.is_active
            and user.role == User.Role.COMPANY
            and user.account_status == User.AccountStatus.ACTIVE
            and user.email_verified_at is not None
        )


class IsAdminAccount(BasePermission):
    """Allow only active staff or superuser accounts."""

    message = "An active administrator account is required."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        return bool(
            user
            and user.is_authenticated
            and user.is_active
            and user.account_status == User.AccountStatus.ACTIVE
            and user.is_admin
            and user.email_verified_at is not None
        )
