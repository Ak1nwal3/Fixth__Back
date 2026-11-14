import ipaddress
import logging
import secrets
from datetime import timedelta

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


def _send_transactional_email(
    *,
    to_email: str,
    subject: str,
    text: str,
    html: str,
) -> None:
    """Send one transactional email through Django's SMTP mailer."""
    try:
        send_mail(
            subject=subject,
            message=text,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[to_email],
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


def enqueue_otp_email(email: str, code: str, track: str) -> bool:
    """Queue OTP delivery, falling back to synchronous sending if needed.

    Returns True when the email was queued on a real worker or sent
    synchronously, and False when delivery failed. With
    OTP_RETURN_ON_FAILURE enabled, delivery failures are logged instead of
    raised so the API response can carry the code to the client.
    """
    try:
        from accounts.tasks import send_otp_email_task
    except ImportError:
        logger.warning(
            "Celery is unavailable; sending the OTP email synchronously.",
        )
    else:
        try:
            send_otp_email_task.delay(email, code, track)
            return True
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
        if settings.OTP_RETURN_ON_FAILURE:
            return False
        if settings.CELERY_TASK_ALWAYS_EAGER:
            # Eager mode is a development convenience: re-raise so delivery
            # failures are visible on the console instead of masking them
            # behind the generic 202 response.
            raise
    return True


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


def request_onboarding_otp(email: str, track: str, *, request=None) -> str | None:
    """Create and deliver a fresh onboarding OTP for a supported track.

    Existing accounts still receive the same generic response, but no onboarding
    code is sent to them; this prevents account enumeration and nuisance emails.

    Returns the OTP code only when email delivery failed and
    OTP_RETURN_ON_FAILURE is enabled (demo environments); otherwise None.
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

    delivered = enqueue_otp_email(email, code, track)
    if not delivered and settings.OTP_RETURN_ON_FAILURE:
        return code
    return None


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
