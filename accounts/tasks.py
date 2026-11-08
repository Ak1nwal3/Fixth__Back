from celery import shared_task

from accounts.services import EmailDeliveryError, _send_otp_email


@shared_task(
    bind=True,
    autoretry_for=(EmailDeliveryError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def send_otp_email_task(self, email: str, code: str, track: str) -> None:
    """Deliver an onboarding OTP outside the request/response cycle."""
    _send_otp_email(email, code, track)
