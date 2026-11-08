import ipaddress
import json
import logging
import secrets
from datetime import timedelta
from urllib import error as urllib_error
from urllib import request as urllib_request

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core import signing
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.html import escape
from django.utils.http import urlsafe_base64_encode

from accounts.models import OTPCode, User

logger = logging.getLogger(__name__)

OTP_RESEND_COOLDOWN = timedelta(seconds=60)
ONBOARDING_TOKEN_SALT = "accounts.onboarding-verification"
ONBOARDING_TOKEN_MAX_AGE = 10 * 60


class OTPVerificationError(Exception):
    """The supplied OTP is missing, expired, consumed, or incorrect."""


class EmailAlreadyRegistered(Exception):
    """The verified email already belongs to an account."""


class EmailDeliveryError(Exception):
    """A transactional email could not be handed off to the provider."""


def generate_otp_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def client_ip(request) -> str | None:
    if request is None:
        return None
    raw = request.META.get("REMOTE_ADDR")
    if not raw:
        return None
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return None


def _send_resend_email(
    *,
    to_email: str,
    subject: str,
    text: str,
    html: str,
) -> None:
    """Send one email through Resend's HTTP API."""
    payload = json.dumps(
        {
            "from": settings.RESEND_FROM_EMAIL,
            "to": [to_email],
            "subject": subject,
            "text": text,
            "html": html,
        }
    ).encode("utf-8")
    request = urllib_request.Request(
        settings.RESEND_API_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {settings.RESEND_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": settings.RESEND_USER_AGENT,
        },
        method="POST",
    )

    try:
        with urllib_request.urlopen(
            request,
            timeout=settings.RESEND_TIMEOUT,
        ) as response:
            response.read()
    except urllib_error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        logger.error(
            "Resend rejected an email: status=%s body=%s",
            exc.code,
            detail,
        )
        raise EmailDeliveryError("Resend rejected the email.") from exc
    except (urllib_error.URLError, OSError) as exc:
        logger.error(
            "Resend request failed: %s",
            exc.__class__.__name__,
        )
        raise EmailDeliveryError("Resend request failed.") from exc


def _delivery_recipient(to_email: str) -> str:
    """Redirect local test mail to the Resend account owner."""
    if not settings.RESEND_USE_TEST_SENDER:
        return to_email

    if not settings.EMAIL_REDIRECT_TO:
        logger.warning(
            "RESEND_USE_TEST_SENDER is enabled without EMAIL_REDIRECT_TO. "
            "Resend will reject recipients other than the account owner.",
        )
        return to_email

    return settings.EMAIL_REDIRECT_TO


def _send_transactional_email(
    *,
    to_email: str,
    subject: str,
    text: str,
    html: str,
) -> None:
    """Send through Resend when configured, otherwise through Django SMTP.

    The Django path honors EMAIL_BACKEND/EMAIL_* settings: the console backend
    locally (default) or a real SMTP relay such as Gmail when configured.
    """
    recipient = _delivery_recipient(to_email)
    if recipient != to_email:
        subject = f"[TEST: {to_email}] {subject}"
        text = (
            f"{text}\n\n"
            f"[Local test redirect: originally addressed to {to_email}]"
        )
        html = (
            f"{html}"
            '<p style="color:#888;font-size:12px">'
            f"Local test redirect: originally addressed to {escape(to_email)}"
            "</p>"
        )

    if settings.RESEND_API_KEY:
        _send_resend_email(
            to_email=recipient,
            subject=subject,
            text=text,
            html=html,
        )
        return

    try:
        send_mail(
            subject=subject,
            message=text,
            from_email=None,
            recipient_list=[recipient],
            html_message=html,
            fail_silently=False,
        )
    except Exception as exc:
        raise EmailDeliveryError("Django email delivery failed.") from exc


def _send_otp_email(email: str, code: str, track: str) -> None:
    label = "organization" if track == OTPCode.Track.ORGANIZATION else "student"
    subject = f"Your Fixth {label} verification code"
    text = (
        f"Your Fixth {label} verification code is:\n\n"
        f"{code}\n\n"
        "This code expires in 10 minutes. If you did not request it, "
        "you can ignore this email."
    )
    html = (
        '<div style="font-family:Arial,sans-serif;max-width:480px;'
        'margin:0 auto;padding:24px;color:#111">'
        '<h1 style="font-size:20px;margin:0 0 16px">'
        "Verify your Fixth email</h1>"
        f"<p>Use this code to continue your {label} onboarding:</p>"
        '<p style="font-size:32px;font-weight:700;letter-spacing:8px;'
        f'margin:24px 0">{code}</p>'
        "<p>This code expires in 10 minutes.</p>"
        '<p style="color:#555">If you did not request this code, you can '
        "ignore this email.</p>"
        "</div>"
    )
    _send_transactional_email(
        to_email=email,
        subject=subject,
        text=text,
        html=html,
    )


def enqueue_otp_email(email: str, code: str, track: str) -> None:
    """Queue OTP delivery, falling back to synchronous sending if needed."""
    try:
        from accounts.tasks import send_otp_email_task
    except ImportError:
        logger.warning(
            "Celery is unavailable; sending the OTP email synchronously.",
        )
    else:
        try:
            send_otp_email_task.delay(email, code, track)
            return
        except Exception:
            logger.exception(
                "Unable to queue the OTP email; sending it synchronously.",
            )

    try:
        _send_otp_email(email, code, track)
    except Exception:
        # Never log the OTP or include provider exception details in the API
        # response. Delivery failures are operational and must be monitored.
        logger.exception("Unable to send OTP email synchronously.")
        if settings.CELERY_TASK_ALWAYS_EAGER:
            # Eager mode is a development convenience: re-raise so delivery
            # failures are visible on the console instead of masking them
            # behind the generic 202 response.
            raise


def send_password_reset_email(user: User) -> None:
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    reset_url = settings.PASSWORD_RESET_URL_TEMPLATE.format(uid=uid, token=token)
    text = (
        "Use the link below to reset your Fixth password:\n\n"
        f"{reset_url}\n\n"
        "This link expires in 1 hour. If you did not request it, you "
        "can ignore this email."
    )
    html = (
        '<div style="font-family:Arial,sans-serif;max-width:480px;'
        'margin:0 auto;padding:24px;color:#111">'
        '<h1 style="font-size:20px;margin:0 0 16px">'
        "Reset your Fixth password</h1>"
        "<p>Use the link below to reset your password:</p>"
        f'<p><a href="{escape(reset_url)}">Reset your Fixth password</a></p>'
        "<p>This link expires in 1 hour.</p>"
        '<p style="color:#555">If you did not request it, you can ignore '
        "this email.</p>"
        "</div>"
    )
    try:
        _send_transactional_email(
            to_email=user.email,
            subject="Reset your Fixth password",
            text=text,
            html=html,
        )
    except Exception:
        logger.exception("Unable to send password reset email.")


def request_onboarding_otp(email: str, track: str, *, request=None) -> None:
    """Create and deliver a fresh onboarding OTP for a supported track.

    Existing accounts still receive the same generic response, but no onboarding
    code is sent to them; this prevents account enumeration and nuisance emails.
    """
    now = timezone.now()

    with transaction.atomic():
        latest = (
            OTPCode.objects.filter(
                email__iexact=email,
                track=track,
                purpose=OTPCode.Purpose.ONBOARDING,
            )
            .order_by("-created_at")
            .first()
        )
        if latest is not None and latest.created_at > now - OTP_RESEND_COOLDOWN:
            return

        # Existing accounts receive the same generic response but no code.
        # They must sign in instead of completing onboarding again.
        if User.objects.filter(email__iexact=email).exists():
            return

        OTPCode.objects.filter(
            email__iexact=email,
            track=track,
            purpose=OTPCode.Purpose.ONBOARDING,
            consumed_at__isnull=True,
        ).update(consumed_at=now)

        code = generate_otp_code()
        otp = OTPCode(
            email=email,
            track=track,
            purpose=OTPCode.Purpose.ONBOARDING,
            ip_address=client_ip(request),
        )
        otp.set_code(code)
        otp.save()

    enqueue_otp_email(email, code, track)


def request_student_otp(email: str, *, request=None) -> None:
    """Backward-compatible wrapper for the student onboarding track."""
    request_onboarding_otp(email, OTPCode.Track.STUDENT, request=request)


def verify_onboarding_otp(email: str, track: str, code: str) -> OTPCode | None:
    """Check a code without consuming it. Failed attempts are persisted."""
    otp = (
        OTPCode.objects.filter(
            email__iexact=email,
            track=track,
            purpose=OTPCode.Purpose.ONBOARDING,
            consumed_at__isnull=True,
            expires_at__gt=timezone.now(),
            attempts__lt=5,
        )
        .order_by("-created_at")
        .first()
    )
    if otp is None:
        return None
    if not otp.check_code(code):
        otp.register_failed_attempt()
        return None
    return otp


def verify_student_otp(email: str, code: str) -> OTPCode | None:
    """Backward-compatible wrapper for the student onboarding track."""
    return verify_onboarding_otp(email, OTPCode.Track.STUDENT, code)


def consume_onboarding_otp(
    otp_id,
    email: str,
    code: str,
    track: str,
) -> OTPCode:
    """Lock and validate an OTP for final consumption inside a transaction."""
    try:
        otp = OTPCode.objects.select_for_update().get(pk=otp_id)
    except OTPCode.DoesNotExist as exc:
        raise OTPVerificationError from exc

    if (
        otp.email.casefold() != email.casefold()
        or otp.track != track
        or otp.purpose != OTPCode.Purpose.ONBOARDING
        or not otp.is_usable
        or not otp.check_code(code)
    ):
        raise OTPVerificationError

    return otp


def consume_student_otp(otp_id, email: str, code: str) -> OTPCode:
    """Backward-compatible wrapper for the student onboarding track."""
    return consume_onboarding_otp(
        otp_id,
        email,
        code,
        OTPCode.Track.STUDENT,
    )


def make_onboarding_verification_token(otp: OTPCode) -> str:
    """Return a short-lived signed proof that an onboarding email was verified."""
    return signing.dumps(
        {
            "otp_id": str(otp.pk),
            "email": otp.email.casefold(),
            "track": otp.track,
        },
        salt=ONBOARDING_TOKEN_SALT,
        compress=True,
    )


def consume_onboarding_verification_token(
    token: str,
    *,
    track: str,
    email: str,
) -> OTPCode:
    """Lock and validate a signed verification token inside a transaction."""
    try:
        payload = signing.loads(
            token,
            salt=ONBOARDING_TOKEN_SALT,
            max_age=ONBOARDING_TOKEN_MAX_AGE,
        )
        otp = OTPCode.objects.select_for_update().get(
            pk=payload.get("otp_id"),
        )
    except (
        signing.BadSignature,
        signing.SignatureExpired,
        OTPCode.DoesNotExist,
        TypeError,
        ValueError,
    ) as exc:
        raise OTPVerificationError from exc

    if (
        payload.get("email") != email.casefold()
        or payload.get("track") != track
        or otp.email.casefold() != email.casefold()
        or otp.track != track
        or otp.purpose != OTPCode.Purpose.ONBOARDING
        or not otp.is_usable
    ):
        raise OTPVerificationError

    return otp
