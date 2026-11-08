from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from accounts.validators import normalize_student_email


class StudentEmailField(serializers.EmailField):
    """Email field restricted to normalized, ASCII ``*.edu.ng`` addresses."""

    default_error_messages = {
        "invalid": "Use your institutional student email ending in .edu.ng.",
    }

    def __init__(self, **kwargs):
        kwargs.setdefault(
            "help_text",
            "Institutional student email ending in .edu.ng.",
        )
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        try:
            return normalize_student_email(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                exc.messages[0] if exc.messages else self.error_messages["invalid"]
            ) from exc
