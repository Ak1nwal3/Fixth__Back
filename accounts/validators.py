import re

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_LOCAL_RE = re.compile(r"^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+$", re.IGNORECASE)
_STUDENT_SUFFIX = ".edu.ng"


def normalize_student_email(value: str) -> str:
    """Normalize and strictly validate a Nigerian institutional email address.

    The domain must be an ASCII DNS name that ends with ``.edu.ng`` at a real
    label boundary. Unicode lookalikes, trailing dots, empty labels, multiple
    ``@`` signs, and host-suffix tricks are rejected.
    """
    if not isinstance(value, str):
        raise ValidationError("Enter a valid student email address.")

    value = value.strip()
    if not value or not value.isascii():
        raise ValidationError("Enter a valid student email address.")

    try:
        validate_email(value)
    except ValidationError as exc:
        raise ValidationError("Enter a valid student email address.") from exc

    if value.count("@") != 1:
        raise ValidationError("Enter a valid student email address.")

    local, domain = value.rsplit("@", 1)
    local = local.strip().lower()
    domain = domain.strip().lower()

    if not local or not domain or len(local) > 64 or len(domain) > 253:
        raise ValidationError("Enter a valid student email address.")

    if local.startswith(".") or local.endswith(".") or ".." in local:
        raise ValidationError("Enter a valid student email address.")
    if not _LOCAL_RE.fullmatch(local):
        raise ValidationError("Enter a valid student email address.")

    if domain.endswith(".") or ".." in domain:
        raise ValidationError("Enter a valid student email address.")

    labels = domain.split(".")
    if any(not label or not _LABEL_RE.fullmatch(label) for label in labels):
        raise ValidationError("Enter a valid student email address.")

    if not domain.endswith(_STUDENT_SUFFIX):
        raise ValidationError(
            "Use your institutional student email ending in .edu.ng."
        )

    institution = domain[: -len(_STUDENT_SUFFIX)]
    if not institution or institution.endswith("."):
        raise ValidationError(
            "Use your institutional student email ending in .edu.ng."
        )

    return f"{local}@{domain}"
