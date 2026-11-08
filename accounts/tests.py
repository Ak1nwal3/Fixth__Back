import json
import re
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.db import IntegrityError
from django.test import SimpleTestCase, override_settings
from django.urls import Resolver404, resolve, reverse
from django.utils import timezone
from rest_framework import serializers
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from accounts import services
from accounts.fields import StudentEmailField
from accounts.models import OTPCode, User
from profiles.models import StudentProfile

OTP_REQUEST_URL = "/api/v1/onboarding/students/request-otp/"
OTP_VERIFY_URL = "/api/v1/onboarding/students/verify-otp/"
ONBOARDING_COMPLETE_URL = "/api/v1/onboarding/students/complete/"
STUDENT_ME_URL = "/api/v1/students/me/"
SIGN_IN_URL = "/api/v1/auth/student/sign-in/"
STUDENT_REFRESH_URL = "/api/v1/auth/student/refresh/"
STUDENT_SIGN_OUT_URL = "/api/v1/auth/student/sign-out/"
STUDENT_PASSWORD_RESET_URL = "/api/v1/auth/student/password-reset/"
STUDENT_PASSWORD_RESET_CONFIRM_URL = (
    "/api/v1/auth/student/password-reset/confirm/"
)
STUDENT_AUTH_ME_URL = "/api/v1/auth/student/me/"
ORGANIZATION_SIGN_IN_URL = "/api/v1/auth/organization/sign-in/"
ORGANIZATION_REFRESH_URL = "/api/v1/auth/organization/refresh/"
ORGANIZATION_SIGN_OUT_URL = "/api/v1/auth/organization/sign-out/"
ORGANIZATION_PASSWORD_RESET_URL = "/api/v1/auth/organization/password-reset/"
ORGANIZATION_AUTH_ME_URL = "/api/v1/auth/organization/me/"
ADMIN_SIGN_IN_URL = "/api/v1/auth/sign-in/"
ADMIN_REFRESH_URL = "/api/v1/auth/refresh/"
ADMIN_AUTH_ME_URL = "/api/v1/auth/me/"


def onboarding_payload(email: str, code: str, **overrides):
    payload = {
        "email": email,
        "code": code,
        "password": "StrongPass!234",
        "first_name": "Ada",
        "last_name": "Lovelace",
        "location": "Lagos",
        "institution": "University of Lagos",
        "discipline": "Computer Engineering",
        "level": 300,
        "skills": ["Python"],
    }
    payload.update(overrides)
    return payload


class StudentEmailValidationTests(APITestCase):
    def test_valid_emails_are_normalized(self):
        field = StudentEmailField()
        cases = {
            " Student@University.Edu.NG ": "student@university.edu.ng",
            "student@dept.university.edu.ng": "student@dept.university.edu.ng",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(field.run_validation(raw), expected)

    def test_invalid_or_lookalike_emails_are_rejected(self):
        field = StudentEmailField()
        cases = [
            "student@example.com",
            "student@university.edu.ng.attacker.com",
            "student@university.edu.ng.",
            "student@edu.ng",
            "student@university..edu.ng",
            "student@university.edu.ng@attacker.com",
            "student@university.\u0435du.ng",
            "not-an-email",
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                with self.assertRaises(serializers.ValidationError):
                    field.run_validation(raw)


@override_settings(
    RESEND_API_KEY="",
    RESEND_USE_TEST_SENDER=False,
    EMAIL_REDIRECT_TO="",
)
class StudentOTPTests(APITestCase):
    def setUp(self):
        cache.clear()

    def _otp_with_known_code(self, email="student@university.edu.ng"):
        otp = OTPCode(
            email=email,
            track=OTPCode.Track.STUDENT,
            purpose=OTPCode.Purpose.ONBOARDING,
        )
        otp.set_code("123456")
        otp.save()
        return otp

    def test_request_issues_hashed_code_without_exposing_it(self):
        with patch("accounts.services.send_mail") as mocked:
            response = self.client.post(
                OTP_REQUEST_URL,
                {"email": "student@university.edu.ng"},
                format="json",
            )

        self.assertEqual(response.status_code, 202)
        otp = OTPCode.objects.get()
        code = re.search(r"\b(\d{6})\b", mocked.call_args.kwargs["message"]).group(1)
        self.assertTrue(otp.check_code(code))
        self.assertNotEqual(otp.code_hash, code)
        self.assertNotIn(code, response.content.decode())

    def test_existing_account_gets_same_response_without_email(self):
        User.objects.create_user(
            email="taken@university.edu.ng",
            role=User.Role.STUDENT,
        )
        with patch("accounts.services.send_mail"):
            new_response = self.client.post(
                OTP_REQUEST_URL,
                {"email": "new@university.edu.ng"},
                format="json",
            )

        with patch("accounts.services.send_mail") as mocked:
            existing_response = self.client.post(
                OTP_REQUEST_URL,
                {"email": "taken@university.edu.ng"},
                format="json",
            )

        self.assertEqual(existing_response.status_code, 202)
        self.assertEqual(existing_response.data, new_response.data)
        mocked.assert_not_called()
        self.assertEqual(OTPCode.objects.count(), 1)

    def test_request_is_rate_limited(self):
        statuses = []
        with patch("accounts.services.send_mail"):
            for index in range(6):
                response = self.client.post(
                    OTP_REQUEST_URL,
                    {"email": f"student{index}@university.edu.ng"},
                    format="json",
                )
                statuses.append(response.status_code)

        self.assertEqual(statuses[:5], [202] * 5)
        self.assertEqual(statuses[5], 429)

    def test_valid_code_verifies_without_consuming(self):
        otp = self._otp_with_known_code()

        response = self.client.post(
            OTP_VERIFY_URL,
            {"email": otp.email, "code": "123456"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        otp.refresh_from_db()
        self.assertIsNone(otp.consumed_at)

    def test_code_is_locked_after_five_failures(self):
        otp = self._otp_with_known_code()

        for _ in range(5):
            response = self.client.post(
                OTP_VERIFY_URL,
                {"email": otp.email, "code": "654321"},
                format="json",
            )
            self.assertEqual(response.status_code, 400)

        response = self.client.post(
            OTP_VERIFY_URL,
            {"email": otp.email, "code": "123456"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_expired_or_consumed_code_is_rejected(self):
        expired = self._otp_with_known_code("expired@university.edu.ng")
        expired.expires_at = timezone.now() - timedelta(seconds=1)
        expired.save(update_fields=["expires_at"])
        consumed = self._otp_with_known_code("consumed@university.edu.ng")
        consumed.mark_consumed()

        for otp in (expired, consumed):
            with self.subTest(email=otp.email):
                response = self.client.post(
                    OTP_VERIFY_URL,
                    {"email": otp.email, "code": "123456"},
                    format="json",
                )
                self.assertEqual(response.status_code, 400)


class OTPEmailDeliveryTests(SimpleTestCase):
    @override_settings(
        RESEND_API_KEY="re_test",
        RESEND_API_URL="https://api.resend.com/emails",
        RESEND_FROM_EMAIL="Fixth <no-reply@example.com>",
        RESEND_TIMEOUT=5,
        RESEND_USE_TEST_SENDER=False,
        EMAIL_REDIRECT_TO="",
    )
    def test_resend_payload_contains_code_and_recipient(self):
        with patch("accounts.services.urllib_request.urlopen") as mocked:
            response = mocked.return_value.__enter__.return_value
            response.read.return_value = b'{"id":"email_123"}'

            services._send_otp_email(
                "student@university.edu.ng",
                "123456",
                OTPCode.Track.STUDENT,
            )

        request = mocked.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["to"], ["student@university.edu.ng"])
        self.assertEqual(payload["from"], "Fixth <no-reply@example.com>")
        self.assertIn("123456", payload["text"])
        self.assertIn("123456", payload["html"])
        self.assertEqual(request.get_header("Authorization"), "Bearer re_test")
        self.assertEqual(request.full_url, "https://api.resend.com/emails")
        self.assertEqual(request.get_header("User-agent"), "Fixth-Backend/1.0")

    @override_settings(
        RESEND_API_KEY="re_test",
        RESEND_API_URL="https://api.resend.com/emails",
        RESEND_FROM_EMAIL="Fixth <onboarding@resend.dev>",
        RESEND_TIMEOUT=5,
        RESEND_USE_TEST_SENDER=True,
        EMAIL_REDIRECT_TO="owner@example.com",
    )
    def test_test_sender_redirects_email_to_the_resend_owner(self):
        with patch("accounts.services.urllib_request.urlopen") as mocked:
            response = mocked.return_value.__enter__.return_value
            response.read.return_value = b'{"id":"email_123"}'

            services._send_otp_email(
                "student@university.edu.ng",
                "123456",
                OTPCode.Track.STUDENT,
            )

        request = mocked.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["to"], ["owner@example.com"])
        self.assertEqual(payload["from"], "Fixth <onboarding@resend.dev>")
        self.assertIn("[TEST: student@university.edu.ng]", payload["subject"])
        self.assertIn("student@university.edu.ng", payload["text"])

    @override_settings(
        RESEND_USE_TEST_SENDER=False,
        EMAIL_REDIRECT_TO="owner@example.com",
    )
    def test_redirect_is_ignored_when_test_sender_is_disabled(self):
        self.assertEqual(
            services._delivery_recipient("student@university.edu.ng"),
            "student@university.edu.ng",
        )

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_enqueue_uses_celery_before_synchronous_fallback(self):
        with patch("accounts.tasks.send_otp_email_task.delay") as mocked:
            services.enqueue_otp_email(
                "student@university.edu.ng",
                "123456",
                OTPCode.Track.STUDENT,
            )

        mocked.assert_called_once_with(
            "student@university.edu.ng",
            "123456",
            OTPCode.Track.STUDENT,
        )


@override_settings(
    RESEND_API_KEY="",
    RESEND_USE_TEST_SENDER=False,
    EMAIL_REDIRECT_TO="",
)
class StudentOnboardingCompleteTests(APITestCase):
    def setUp(self):
        cache.clear()

    def _request_code(self, email="student@university.edu.ng"):
        with patch("accounts.services.send_mail") as mocked:
            response = self.client.post(
                OTP_REQUEST_URL,
                {"email": email},
                format="json",
            )
        self.assertEqual(response.status_code, 202)
        return re.search(r"\b(\d{6})\b", mocked.call_args.kwargs["message"]).group(1)

    def _complete(self, payload):
        return self.client.post(
            ONBOARDING_COMPLETE_URL,
            payload,
            format="json",
        )

    def test_complete_creates_user_profile_and_tokens(self):
        email = "student@university.edu.ng"
        code = self._request_code(email)

        response = self._complete(onboarding_payload(email, code))

        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotIn(code, response.content.decode())
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        user = User.objects.get(email=email)
        self.assertEqual(user.role, User.Role.STUDENT)
        self.assertIsNotNone(user.email_verified_at)
        self.assertTrue(user.check_password("StrongPass!234"))
        self.assertTrue(StudentProfile.objects.filter(user=user).exists())
        profile = StudentProfile.objects.get(user=user)
        # Product decision: sign-up auto-verifies the student profile.
        self.assertEqual(profile.verification_status, "Verified")
        otp = OTPCode.objects.get(email=email)
        self.assertIsNotNone(otp.consumed_at)
        self.assertEqual(otp.user_id, user.id)

    def test_replaying_a_consumed_code_is_rejected(self):
        email = "student@university.edu.ng"
        code = self._request_code(email)
        payload = onboarding_payload(email, code)

        first = self._complete(payload)
        second = self._complete(payload)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 400)
        self.assertEqual(User.objects.count(), 1)

    def test_invalid_code_does_not_create_account(self):
        email = "student@university.edu.ng"
        self._request_code(email)
        otp = OTPCode.objects.get(email=email)
        otp.set_code("123456")
        otp.save(update_fields=["code_hash"])

        response = self._complete(onboarding_payload(email, "654321"))

        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.exists())

    def test_existing_account_conflict_is_generic(self):
        existing = User.objects.create_user(
            email="taken@university.edu.ng",
            role=User.Role.STUDENT,
        )
        otp = OTPCode(
            email=existing.email,
            track=OTPCode.Track.STUDENT,
            purpose=OTPCode.Purpose.ONBOARDING,
        )
        otp.set_code("123456")
        otp.save()

        response = self._complete(onboarding_payload(existing.email, "123456"))

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.data["detail"],
            "This email cannot be used for onboarding.",
        )
        self.assertEqual(User.objects.count(), 1)
        self.assertFalse(StudentProfile.objects.exists())

    def test_protected_user_fields_cannot_be_set(self):
        email = "student@university.edu.ng"
        code = self._request_code(email)
        payload = onboarding_payload(
            email,
            code,
            role="admin",
            is_staff=True,
            is_superuser=True,
            account_status="deleted",
            email_verified_at="2000-01-01T00:00:00Z",
        )

        response = self._complete(payload)

        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email=email)
        self.assertEqual(user.role, User.Role.STUDENT)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(user.account_status, User.AccountStatus.ACTIVE)

    def test_profile_failure_rolls_back_user_creation(self):
        email = "student@university.edu.ng"
        code = self._request_code(email)

        with patch(
            "profiles.serializers.StudentProfile.objects.create",
            side_effect=IntegrityError("simulated database failure"),
        ):
            response = self._complete(onboarding_payload(email, code))

        self.assertEqual(response.status_code, 409)
        self.assertFalse(User.objects.filter(email=email).exists())
        otp = OTPCode.objects.get(email=email)
        self.assertIsNone(otp.consumed_at)


@override_settings(
    RESEND_API_KEY="",
    RESEND_USE_TEST_SENDER=False,
    EMAIL_REDIRECT_TO="",
)
class AuthAndProfileTests(APITestCase):
    def setUp(self):
        cache.clear()

    def _signup(self, email="student@university.edu.ng"):
        with patch("accounts.services.send_mail") as mocked:
            response = self.client.post(
                OTP_REQUEST_URL,
                {"email": email},
                format="json",
            )
        self.assertEqual(response.status_code, 202)
        code = re.search(r"\b(\d{6})\b", mocked.call_args.kwargs["message"]).group(1)
        response = self.client.post(
            ONBOARDING_COMPLETE_URL,
            onboarding_payload(email, code),
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def _authenticate(self, user):
        refresh = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    def test_profile_requires_authenticated_verified_student(self):
        response = self.client.get(STUDENT_ME_URL)
        self.assertEqual(response.status_code, 401)

        unverified = User.objects.create_user(
            email="unverified@university.edu.ng",
            password="StrongPass!234",
            role=User.Role.STUDENT,
        )
        self._authenticate(unverified)
        response = self.client.get(STUDENT_ME_URL)
        self.assertEqual(response.status_code, 403)

        company = User.objects.create_user(
            email="company@example.com",
            password="StrongPass!234",
            role=User.Role.COMPANY,
            email_verified_at=timezone.now(),
        )
        self._authenticate(company)
        response = self.client.get(STUDENT_ME_URL)
        self.assertEqual(response.status_code, 403)

    def test_verified_student_reads_only_own_profile(self):
        first = self._signup("first@university.edu.ng")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {first['access']}")
        response = self.client.get(STUDENT_ME_URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["email"], "first@university.edu.ng")

        self.client.credentials()
        second = self._signup("second@university.edu.ng")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {second['access']}")
        response = self.client.get(STUDENT_ME_URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["email"], "second@university.edu.ng")
        self.assertNotEqual(response.data["id"], first["student"]["id"])

    def test_patch_ignores_protected_fields_and_email(self):
        data = self._signup()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {data['access']}")
        # Sign-up auto-verifies the profile; reset it so the protected-field
        # assertion below has something to protect.
        StudentProfile.objects.filter(
            user=User.objects.get(email="student@university.edu.ng"),
        ).update(verification_status="Pending")

        response = self.client.patch(
            STUDENT_ME_URL,
            {
                "first_name": "Grace",
                "email": "changed@university.edu.ng",
                "role": "admin",
                "is_staff": True,
                "verification_status": "Verified",
                "user": "attacker",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["first_name"], "Grace")
        self.assertEqual(response.data["email"], "student@university.edu.ng")
        self.assertEqual(response.data["verification_status"], "Pending")
        user = User.objects.get(email="student@university.edu.ng")
        self.assertEqual(user.role, User.Role.STUDENT)
        self.assertFalse(user.is_staff)

    def test_profile_create_and_duplicate_conflict(self):
        user = User.objects.create_user(
            email="noprofile@university.edu.ng",
            password="StrongPass!234",
            role=User.Role.STUDENT,
            email_verified_at=timezone.now(),
        )
        self._authenticate(user)
        payload = {
            "first_name": "Grace",
            "last_name": "Hopper",
            "location": "Abuja",
            "institution": "University of Abuja",
            "discipline": "Computer Science",
            "level": 400,
            "skills": ["Python"],
        }

        created = self.client.post(STUDENT_ME_URL, payload, format="json")
        duplicate = self.client.post(STUDENT_ME_URL, payload, format="json")

        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(duplicate.status_code, 409)

    def test_sign_in_requires_verified_email(self):
        self._signup()
        verified = self.client.post(
            SIGN_IN_URL,
            {
                "email": "student@university.edu.ng",
                "password": "StrongPass!234",
            },
            format="json",
        )
        self.assertEqual(verified.status_code, 200)
        self.assertIn("access", verified.data)

        User.objects.create_user(
            email="unverified@university.edu.ng",
            password="StrongPass!234",
            role=User.Role.STUDENT,
        )
        unverified = self.client.post(
            SIGN_IN_URL,
            {
                "email": "unverified@university.edu.ng",
                "password": "StrongPass!234",
            },
            format="json",
        )
        self.assertEqual(unverified.status_code, 403)

    def test_refresh_rotates_and_blacklists_old_token(self):
        data = self._signup()
        old_refresh = data["refresh"]

        rotated = self.client.post(
            STUDENT_REFRESH_URL,
            {"refresh": old_refresh},
            format="json",
        )
        self.assertEqual(rotated.status_code, 200)
        self.assertIn("access", rotated.data)
        self.assertIn("refresh", rotated.data)

        reused = self.client.post(
            STUDENT_REFRESH_URL,
            {"refresh": old_refresh},
            format="json",
        )
        self.assertEqual(reused.status_code, 401)

        current = self.client.post(
            STUDENT_REFRESH_URL,
            {"refresh": rotated.data["refresh"]},
            format="json",
        )
        self.assertEqual(current.status_code, 200)

    def test_sign_out_blacklists_refresh_token(self):
        data = self._signup()
        refresh = data["refresh"]

        signed_out = self.client.post(
            STUDENT_SIGN_OUT_URL,
            {"refresh": refresh},
            format="json",
        )
        self.assertEqual(signed_out.status_code, 204)

        reused = self.client.post(
            STUDENT_REFRESH_URL,
            {"refresh": refresh},
            format="json",
        )
        self.assertEqual(reused.status_code, 401)

    def test_password_reset_flow(self):
        self._signup()
        with patch("accounts.services.send_mail") as mocked:
            response = self.client.post(
                STUDENT_PASSWORD_RESET_URL,
                {"email": "student@university.edu.ng"},
                format="json",
            )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(mocked.call_count, 1)
        match = re.search(
            r"uid=([^&\s]+)&token=([^\s]+)",
            mocked.call_args.kwargs["message"],
        )
        self.assertIsNotNone(match)
        uid, token = match.groups()

        confirmed = self.client.post(
            STUDENT_PASSWORD_RESET_CONFIRM_URL,
            {
                "uid": uid,
                "token": token,
                "new_password": "NewStrongPass!456",
            },
            format="json",
        )
        self.assertEqual(confirmed.status_code, 204)

        sign_in = self.client.post(
            SIGN_IN_URL,
            {
                "email": "student@university.edu.ng",
                "password": "NewStrongPass!456",
            },
            format="json",
        )
        self.assertEqual(sign_in.status_code, 200)

        reused = self.client.post(
            STUDENT_PASSWORD_RESET_CONFIRM_URL,
            {
                "uid": uid,
                "token": token,
                "new_password": "AnotherStrongPass!789",
            },
            format="json",
        )
        self.assertEqual(reused.status_code, 400)

        with patch("accounts.services.send_mail") as missing_mail:
            missing = self.client.post(
                STUDENT_PASSWORD_RESET_URL,
                {"email": "missing@university.edu.ng"},
                format="json",
            )
        self.assertEqual(missing.status_code, 202)
        missing_mail.assert_not_called()

    def test_patch_me_updates_name_and_password_but_blocks_email(self):
        data = self._signup()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {data['access']}")
        User.objects.create_user(
            email="taken@university.edu.ng",
            role=User.Role.STUDENT,
        )

        renamed = self.client.patch(
            STUDENT_AUTH_ME_URL,
            {"first_name": "Grace"},
            format="json",
        )
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(renamed.data["first_name"], "Grace")

        taken = self.client.patch(
            STUDENT_AUTH_ME_URL,
            {"email": "taken@university.edu.ng"},
            format="json",
        )
        self.assertEqual(taken.status_code, 409)

        unverified = self.client.patch(
            STUDENT_AUTH_ME_URL,
            {"email": "new@university.edu.ng"},
            format="json",
        )
        self.assertEqual(unverified.status_code, 400)

        wrong_password = self.client.patch(
            STUDENT_AUTH_ME_URL,
            {
                "current_password": "WrongPass!234",
                "new_password": "NewStrongPass!456",
            },
            format="json",
        )
        self.assertEqual(wrong_password.status_code, 400)

        changed = self.client.patch(
            STUDENT_AUTH_ME_URL,
            {
                "current_password": "StrongPass!234",
                "new_password": "NewStrongPass!456",
            },
            format="json",
        )
        self.assertEqual(changed.status_code, 200)

        sign_in = self.client.post(
            SIGN_IN_URL,
            {
                "email": "student@university.edu.ng",
                "password": "NewStrongPass!456",
            },
            format="json",
        )
        self.assertEqual(sign_in.status_code, 200)

    def test_role_scoped_sign_in_is_isolated(self):
        self._signup()
        company = User.objects.create_user(
            email="company@university.edu.ng",
            password="StrongPass!234",
            role=User.Role.COMPANY,
            email_verified_at=timezone.now(),
        )
        admin = User.objects.create_user(
            email="admin@fixth.edu.ng",
            password="StrongPass!234",
            role=User.Role.ADMIN,
            is_staff=True,
            email_verified_at=timezone.now(),
        )
        credentials = {
            "email": "company@university.edu.ng",
            "password": "StrongPass!234",
        }
        admin_credentials = {
            "email": "admin@fixth.edu.ng",
            "password": "StrongPass!234",
        }
        student_credentials = {
            "email": "student@university.edu.ng",
            "password": "StrongPass!234",
        }

        self.assertEqual(
            self.client.post(SIGN_IN_URL, student_credentials, format="json").status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                ORGANIZATION_SIGN_IN_URL,
                credentials,
                format="json",
            ).status_code,
            200,
        )
        admin_sign_in = self.client.post(
            ADMIN_SIGN_IN_URL,
            admin_credentials,
            format="json",
        )
        self.assertEqual(admin_sign_in.status_code, 200)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {admin_sign_in.data['access']}"
        )
        self.assertEqual(self.client.get(ADMIN_AUTH_ME_URL).status_code, 200)
        self.client.credentials()
        self.assertEqual(
            self.client.post(
                ORGANIZATION_SIGN_IN_URL,
                student_credentials,
                format="json",
            ).status_code,
            401,
        )
        self.assertEqual(
            self.client.post(SIGN_IN_URL, credentials, format="json").status_code,
            401,
        )
        self.assertEqual(
            self.client.post(
                ADMIN_SIGN_IN_URL,
                student_credentials,
                format="json",
            ).status_code,
            401,
        )
        self.assertEqual(
            self.client.post(
                ORGANIZATION_SIGN_IN_URL,
                admin_credentials,
                format="json",
            ).status_code,
            401,
        )
        self.assertEqual(company.role, User.Role.COMPANY)
        self.assertTrue(admin.is_staff)

    def test_scoped_refresh_and_sign_out_are_role_isolated(self):
        data = self._signup()
        company = User.objects.create_user(
            email="company@university.edu.ng",
            password="StrongPass!234",
            role=User.Role.COMPANY,
            email_verified_at=timezone.now(),
        )
        company_refresh = str(RefreshToken.for_user(company))

        self.assertEqual(
            self.client.post(
                ORGANIZATION_REFRESH_URL,
                {"refresh": data["refresh"]},
                format="json",
            ).status_code,
            401,
        )
        self.assertEqual(
            self.client.post(
                STUDENT_REFRESH_URL,
                {"refresh": company_refresh},
                format="json",
            ).status_code,
            401,
        )
        self.assertEqual(
            self.client.post(
                ADMIN_REFRESH_URL,
                {"refresh": data["refresh"]},
                format="json",
            ).status_code,
            401,
        )
        self.assertEqual(
            self.client.post(
                ORGANIZATION_SIGN_OUT_URL,
                {"refresh": data["refresh"]},
                format="json",
            ).status_code,
            401,
        )

        still_valid = self.client.post(
            STUDENT_REFRESH_URL,
            {"refresh": data["refresh"]},
            format="json",
        )
        self.assertEqual(still_valid.status_code, 200)
        self.assertEqual(
            self.client.post(
                ORGANIZATION_REFRESH_URL,
                {"refresh": company_refresh},
                format="json",
            ).status_code,
            200,
        )

    def test_scoped_password_reset_and_me_are_role_isolated(self):
        data = self._signup()
        with patch("accounts.services.send_mail") as wrong_role_mail:
            response = self.client.post(
                ORGANIZATION_PASSWORD_RESET_URL,
                {"email": "student@university.edu.ng"},
                format="json",
            )
        self.assertEqual(response.status_code, 202)
        wrong_role_mail.assert_not_called()

        with patch("accounts.services.send_mail") as correct_role_mail:
            response = self.client.post(
                STUDENT_PASSWORD_RESET_URL,
                {"email": "student@university.edu.ng"},
                format="json",
            )
        self.assertEqual(response.status_code, 202)
        correct_role_mail.assert_called_once()

        company = User.objects.create_user(
            email="company@university.edu.ng",
            role=User.Role.COMPANY,
            email_verified_at=timezone.now(),
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {data['access']}")
        self.assertEqual(
            self.client.get(STUDENT_AUTH_ME_URL).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(ORGANIZATION_AUTH_ME_URL).status_code,
            403,
        )
        self.assertEqual(self.client.get(ADMIN_AUTH_ME_URL).status_code, 403)

        self._authenticate(company)
        self.assertEqual(
            self.client.get(STUDENT_AUTH_ME_URL).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(ORGANIZATION_AUTH_ME_URL).status_code,
            200,
        )


class ApiNamespaceTests(SimpleTestCase):
    def test_legacy_api_prefix_is_not_registered(self):
        legacy_paths = [
            "/api/auth/sign-in/",
            "/api/auth/me/",
            "/api/onboarding/students/complete/",
            "/api/onboarding/organizations/complete/",
            "/api/students/me/",
            "/api/disciplines/",
            "/api/skills/",
            "/api/schema/",
            "/api/docs/",
            "/api/redoc/",
        ]

        for path in legacy_paths:
            with self.assertRaises(Resolver404, msg=path):
                resolve(path)

    def test_canonical_urls_are_versioned_and_unique(self):
        expected = {
            "auth-sign-in": "/api/v1/auth/sign-in/",
            "auth-refresh": "/api/v1/auth/refresh/",
            "auth-me": "/api/v1/auth/me/",
            "student-auth-sign-in": "/api/v1/auth/student/sign-in/",
            "student-auth-refresh": "/api/v1/auth/student/refresh/",
            "student-auth-me": "/api/v1/auth/student/me/",
            "organization-auth-sign-in": (
                "/api/v1/auth/organization/sign-in/"
            ),
            "organization-auth-refresh": (
                "/api/v1/auth/organization/refresh/"
            ),
            "organization-auth-me": "/api/v1/auth/organization/me/",
            "student-onboarding-complete": (
                "/api/v1/onboarding/students/complete/"
            ),
            "organization-onboarding-complete": (
                "/api/v1/onboarding/organizations/complete/"
            ),
            "student-me": "/api/v1/students/me/",
            "discipline-list": "/api/v1/disciplines/",
            "skill-list": "/api/v1/skills/",
            "schema": "/api/v1/schema/",
            "swagger-ui": "/api/v1/docs/",
            "redoc": "/api/v1/redoc/",
        }

        for name, path in expected.items():
            self.assertEqual(reverse(name), path)
            self.assertEqual(resolve(path).url_name, name)

    def test_documentation_is_served_from_canonical_namespace(self):
        for path in [
            "/api/v1/schema/",
            "/api/v1/docs/",
            "/api/v1/redoc/",
        ]:
            self.assertEqual(self.client.get(path).status_code, 200)

        for path in ["/api/schema/", "/api/docs/", "/api/redoc/"]:
            self.assertEqual(self.client.get(path).status_code, 404)
