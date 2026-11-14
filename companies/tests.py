import re
import tempfile
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import OTPCode, User
from applications.models import Application
from common.models import Document
from companies.models import Company
from placements.models import Placement
from profiles.models import Discipline, StudentProfile
from verification.models import VerificationRequest

OTP_REQUEST_URL = "/api/v1/auth/otp/request/"
OTP_VERIFY_URL = "/api/v1/auth/otp/verify/"
ONBOARDING_COMPLETE_URL = "/api/v1/onboarding/organizations/complete/"
ORG_SIGN_IN_URL = "/api/v1/auth/organization/sign-in/"
WORK_EMAIL = "hello@novalabs.example"


def organization_payload(verification_token, **overrides):
    payload = {
        "verification_token": verification_token,
        "organization_name": "Nova Labs",
        "website": "novalabs.example",
        "industry": "Technology",
        "description": "We build engineering tools.",
        "country": "Nigeria",
        "city": "Lagos",
        "address": "12 Marina Road",
        "work_email": WORK_EMAIL,
    }
    payload.update(overrides)
    return payload


def logo_file(name="logo.png", image_format="PNG"):
    buffer = BytesIO()
    Image.new("RGB", (32, 32), (20, 80, 140)).save(buffer, format=image_format)
    return SimpleUploadedFile(
        name,
        buffer.getvalue(),
        content_type=f"image/{image_format.lower()}",
    )


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class OrganizationOnboardingTests(APITestCase):
    def setUp(self):
        cache.clear()

    def _verified_token(self, email=WORK_EMAIL):
        with patch("accounts.services.send_mail") as mocked:
            response = self.client.post(
                OTP_REQUEST_URL,
                {"email": email, "track": "organization"},
                format="json",
            )
        self.assertEqual(response.status_code, 202, response.data)
        code = re.search(r"\b(\d{6})\b", mocked.call_args.kwargs["message"]).group(1)

        response = self.client.post(
            OTP_VERIFY_URL,
            {"email": email, "track": "organization", "code": code},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["verification_token"])
        return response.data["verification_token"]

    def _complete(self, payload, **extra):
        return self.client.post(
            ONBOARDING_COMPLETE_URL,
            {**payload, **extra},
            format="multipart",
        )

    def test_complete_creates_company_account_company_and_pending_review(self):
        token = self._verified_token()
        response = self._complete(
            organization_payload(
                token,
                verification_status="Verified",
                risk_level="Low",
                role="admin",
                is_staff=True,
            ),
            logo=logo_file(),
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertNotIn(token, response.content.decode())
        self.assertEqual(response.data["company"]["name"], "Nova Labs")

        user = User.objects.get(email=WORK_EMAIL)
        self.assertEqual(user.role, User.Role.COMPANY)
        self.assertTrue(user.is_active)
        self.assertIsNotNone(user.email_verified_at)
        self.assertFalse(user.is_staff)

        company = Company.objects.get(owner=user)
        self.assertEqual(company.name, "Nova Labs")
        self.assertEqual(company.website, "https://novalabs.example")
        self.assertEqual(company.industry, Company.Industry.TECHNOLOGY)
        self.assertEqual(company.verification_status, "Pending")
        self.assertEqual(company.risk_level, "Low")
        self.assertIsNotNone(company.logo)
        self.assertFalse(company.logo.is_private)
        self.assertEqual(company.logo.mime_type, "image/png")
        self.assertTrue(company.logo.file.name.endswith(".png"))

        request = VerificationRequest.objects.get(company=company)
        self.assertEqual(request.kind, VerificationRequest.Kind.COMPANY)
        self.assertEqual(request.status, "Pending")

        otp = OTPCode.objects.get(email=WORK_EMAIL)
        self.assertIsNotNone(otp.consumed_at)
        self.assertEqual(otp.user_id, user.id)

        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {response.data['access']}"
        )
        me = self.client.get("/api/v1/auth/organization/me/")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.data["company_id"], str(company.id))

    def test_complete_sets_a_password_when_provided(self):
        token = self._verified_token()
        response = self._complete(
            organization_payload(token),
            password="StrongPass!234",
        )

        self.assertEqual(response.status_code, 201, response.data)
        user = User.objects.get(email=WORK_EMAIL)
        self.assertTrue(user.check_password("StrongPass!234"))

        sign_in = self.client.post(
            ORG_SIGN_IN_URL,
            {"email": WORK_EMAIL, "password": "StrongPass!234"},
            format="json",
        )
        self.assertEqual(sign_in.status_code, 200, sign_in.data)

    def test_complete_leaves_the_account_passwordless_when_blank(self):
        token = self._verified_token()
        response = self._complete(organization_payload(token), password="")

        self.assertEqual(response.status_code, 201, response.data)
        user = User.objects.get(email=WORK_EMAIL)
        self.assertFalse(user.has_usable_password())

    def test_complete_rejects_a_weak_password(self):
        token = self._verified_token()
        response = self._complete(organization_payload(token), password="password")

        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(User.objects.filter(email=WORK_EMAIL).exists())

    def test_endpoint_requires_multipart_form_data(self):
        response = self.client.post(
            ONBOARDING_COMPLETE_URL,
            organization_payload("irrelevant"),
            format="json",
        )

        self.assertEqual(response.status_code, 415)
        self.assertFalse(User.objects.exists())

    def test_replayed_verification_token_is_rejected(self):
        token = self._verified_token()
        payload = organization_payload(token)

        first = self._complete(payload)
        second = self._complete(payload)

        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 400)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(Company.objects.count(), 1)

    def test_work_email_must_match_verified_token(self):
        token = self._verified_token()

        response = self._complete(
            organization_payload(token, work_email="other@novalabs.example")
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.exists())
        self.assertFalse(Company.objects.exists())

    def test_existing_account_cannot_be_recreated(self):
        existing = User.objects.create_user(email=WORK_EMAIL, role=User.Role.COMPANY)
        otp = OTPCode(
            email=WORK_EMAIL,
            track=OTPCode.Track.ORGANIZATION,
            purpose=OTPCode.Purpose.ONBOARDING,
        )
        otp.set_code("123456")
        otp.save()
        response = self.client.post(
            OTP_VERIFY_URL,
            {"email": WORK_EMAIL, "track": "organization", "code": "123456"},
            format="json",
        )
        token = response.data["verification_token"]

        response = self._complete(organization_payload(token))

        self.assertEqual(response.status_code, 409)
        self.assertEqual(User.objects.count(), 1)
        self.assertFalse(Company.objects.exists())
        self.assertEqual(User.objects.get().id, existing.id)

    def test_logo_size_and_type_are_validated_without_consuming_token(self):
        token = self._verified_token()
        payload = organization_payload(token)

        with patch("companies.serializers.MAX_LOGO_BYTES", 4):
            too_large = self._complete(
                payload,
                logo=SimpleUploadedFile(
                    "large.png",
                    b"xxxxx",
                    content_type="image/png",
                ),
            )
        self.assertEqual(too_large.status_code, 413)

        wrong_type = self._complete(
            payload,
            logo=SimpleUploadedFile(
                "logo.png",
                b"nope",
                content_type="image/png",
            ),
        )
        self.assertEqual(wrong_type.status_code, 415)

        valid = self._complete(payload, logo=logo_file())
        self.assertEqual(valid.status_code, 201, valid.data)

    def test_company_failure_rolls_back_account_and_logo(self):
        token = self._verified_token()

        with patch(
            "companies.serializers.VerificationRequest.objects.create",
            side_effect=IntegrityError("simulated database failure"),
        ):
            response = self._complete(
                organization_payload(token),
                logo=logo_file(),
            )

        self.assertEqual(response.status_code, 409)
        self.assertFalse(User.objects.exists())
        self.assertFalse(Company.objects.exists())
        self.assertFalse(Document.objects.exists())
        otp = OTPCode.objects.get(email=WORK_EMAIL)
        self.assertIsNone(otp.consumed_at)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class CompanyProfileAPITests(APITestCase):
    profile_url = "/api/v1/companies/me/"

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            email="owner@novalabs.example",
            password="StrongPass!234",
            role=User.Role.COMPANY,
            email_verified_at=timezone.now(),
        )
        self.company = Company.objects.create(
            owner=self.user,
            name="Nova Labs",
            industry=Company.Industry.TECHNOLOGY,
            country="Nigeria",
            city="Lagos",
        )
        self.other_user = User.objects.create_user(
            email="owner@otherlabs.example",
            password="StrongPass!234",
            role=User.Role.COMPANY,
            email_verified_at=timezone.now(),
        )
        self.other_company = Company.objects.create(
            owner=self.other_user,
            name="Other Labs",
            industry=Company.Industry.ENERGY,
            country="Nigeria",
            city="Abuja",
        )
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(self.user).access_token}"
        )

    def test_company_retrieves_its_own_profile(self):
        response = self.client.get(self.profile_url)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["id"], str(self.company.id))
        self.assertEqual(response.data["name"], "Nova Labs")
        self.assertIn("registration_number", response.data)
        self.assertIn("primary_contact_name", response.data)
        self.assertIn("verification_status", response.data)

    def test_company_updates_its_profile_without_changing_protected_fields(self):
        response = self.client.patch(
            self.profile_url,
            {
                "name": "Nova Labs Africa",
                "website": "novalabs.example",
                "industry": Company.Industry.ENERGY,
                "description": "Updated profile.",
                "country": "Nigeria",
                "city": "Abuja",
                "address": "45 New Road",
                "registration_number": "RC-123456",
                "primary_contact_name": "Bola Verifier",
                "primary_contact_title": "Talent Lead",
                "owner_id": str(self.other_user.id),
                "slug": "hijacked-slug",
                "verification_status": "Verified",
                "risk_level": "Medium",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.company.refresh_from_db()
        self.assertEqual(self.company.name, "Nova Labs Africa")
        self.assertEqual(self.company.website, "https://novalabs.example")
        self.assertEqual(self.company.industry, Company.Industry.ENERGY)
        self.assertEqual(self.company.slug, "nova-labs")
        self.assertEqual(self.company.owner, self.user)
        self.assertEqual(self.company.verification_status, "Pending")
        self.assertEqual(self.company.risk_level, "Low")

    def test_company_updates_and_clears_its_logo(self):
        first_logo = logo_file("first.png")
        response = self.client.patch(
            self.profile_url,
            {"logo": first_logo},
            format="multipart",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.company.refresh_from_db()
        self.assertIsNotNone(self.company.logo)
        self.assertFalse(self.company.logo.is_private)
        first_document_id = self.company.logo_id

        response = self.client.patch(
            self.profile_url,
            {"logo": logo_file("second.png")},
            format="multipart",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.company.refresh_from_db()
        self.assertNotEqual(self.company.logo_id, first_document_id)
        self.assertEqual(response.data["logo"]["mime_type"], "image/png")

        response = self.client.patch(
            self.profile_url,
            {"logo": None},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.company.refresh_from_db()
        self.assertIsNone(self.company.logo)
        self.assertIsNone(response.data["logo"])

    def test_invalid_profile_update_does_not_change_logo(self):
        self.client.patch(
            self.profile_url,
            {"logo": logo_file("valid.png")},
            format="multipart",
        )
        self.company.refresh_from_db()
        logo_id = self.company.logo_id

        with patch(
            "companies.serializers.MAX_LOGO_BYTES",
            4,
        ):
            response = self.client.patch(
                self.profile_url,
                {"logo": logo_file("too-large.png")},
                format="multipart",
            )

        self.assertEqual(response.status_code, 413)
        self.company.refresh_from_db()
        self.assertEqual(self.company.logo_id, logo_id)

    def test_invalid_website_is_rejected(self):
        response = self.client.patch(
            self.profile_url,
            {"website": "not a valid host"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.company.refresh_from_db()
        self.assertEqual(self.company.website, "")

    def test_each_company_only_receives_its_own_profile(self):
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(self.other_user).access_token}"
        )
        response = self.client.get(self.profile_url)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["id"], str(self.other_company.id))
        self.assertEqual(response.data["name"], "Other Labs")

    def test_company_role_and_profile_requirements_are_enforced(self):
        student = User.objects.create_user(
            email="student-role@university.edu.ng",
            password="StrongPass!234",
            role=User.Role.STUDENT,
            email_verified_at=timezone.now(),
        )
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(student).access_token}"
        )
        response = self.client.get(self.profile_url)
        self.assertEqual(response.status_code, 403)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class PublicCompanyAPITests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            email="public@novalabs.example",
            password="StrongPass!234",
            role=User.Role.COMPANY,
            email_verified_at=timezone.now(),
        )
        self.company = Company.objects.create(
            owner=self.user,
            name="Nova Labs",
            slug="nova-labs",
            industry=Company.Industry.TECHNOLOGY,
            country="Nigeria",
            city="Lagos",
            description="Engineering tools.",
            registration_number="RC-PRIVATE",
            primary_contact_name="Private Contact",
        )

    def test_public_company_profile_exposes_only_public_fields(self):
        response = self.client.get("/api/v1/companies/nova-labs/")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["name"], "Nova Labs")
        self.assertEqual(response.data["verification_status"], "Pending")
        self.assertNotIn("owner_id", response.data)
        self.assertNotIn("registration_number", response.data)
        self.assertNotIn("primary_contact_name", response.data)
        self.assertNotIn("risk_level", response.data)

    def test_hidden_company_profiles_are_not_found(self):
        rejected = User.objects.create_user(
            email="rejected@labs.example",
            password="StrongPass!234",
            role=User.Role.COMPANY,
            email_verified_at=timezone.now(),
        )
        rejected_company = Company.objects.create(
            owner=rejected,
            name="Rejected Labs",
            industry=Company.Industry.OTHER,
            country="Nigeria",
            city="Lagos",
            verification_status="Rejected",
        )
        deleted = User.objects.create_user(
            email="deleted@labs.example",
            password="StrongPass!234",
            role=User.Role.COMPANY,
            email_verified_at=timezone.now(),
        )
        deleted_company = Company.objects.create(
            owner=deleted,
            name="Deleted Labs",
            industry=Company.Industry.OTHER,
            country="Nigeria",
            city="Lagos",
            deleted_at=timezone.now(),
        )

        self.assertEqual(
            self.client.get(f"/api/v1/companies/{rejected_company.slug}/").status_code,
            404,
        )
        self.assertEqual(
            self.client.get(f"/api/v1/companies/{deleted_company.slug}/").status_code,
            404,
        )
        self.assertEqual(
            self.client.get("/api/v1/companies/missing-company/").status_code,
            404,
        )


class CompanySummaryAPITests(APITestCase):
    summary_url = "/api/v1/company/summary/"

    def setUp(self):
        cache.clear()
        self.discipline = Discipline.objects.create(name="Computer Engineering")
        self.user = self._company_user("owner@novalabs.example")
        self.company = self._company(self.user, "Nova Labs")
        self.other_user = self._company_user("other-owner@otherlabs.example")
        self.other_company = self._company(self.other_user, "Other Labs")

        self.active = self._placement("Active Role")
        self.other_active = self._placement("Other Active", company=self.other_company)
        self.closed = self._placement("Closed Role")
        self.closed.set_status(Placement.Status.CLOSED)
        self.expired = self._placement("Expired Role")
        self.expired.expires_at = timezone.now() - timedelta(minutes=1)
        self.expired.save(update_fields=["expires_at", "updated_at"])

        self.student = self._student("ada@university.edu.ng")

    def _company_user(self, email):
        return User.objects.create_user(
            email=email,
            password="StrongPass!234",
            role=User.Role.COMPANY,
            email_verified_at=timezone.now(),
        )

    def _company(self, user, name):
        return Company.objects.create(
            owner=user,
            name=name,
            industry=Company.Industry.TECHNOLOGY,
            country="Nigeria",
            city="Lagos",
        )

    def _placement(self, title, company=None, owner=None):
        return Placement.objects.create(
            company=company or self.company,
            title=title,
            location="Lagos",
            work_style=Placement.WorkStyle.HYBRID,
            duration="6 months",
            discipline=self.discipline,
            level=300,
            status=Placement.Status.ACTIVE,
            created_by=owner or self.user,
        )

    def _student(self, email):
        user = User.objects.create_user(
            email=email,
            password="StrongPass!234",
            role=User.Role.STUDENT,
            email_verified_at=timezone.now(),
        )
        return StudentProfile.objects.create(
            user=user,
            first_name="Ada",
            last_name="Lovelace",
            phone="+2348010000000",
            location="Lagos",
            institution="University of Lagos",
            discipline=self.discipline,
            level=400,
        )

    def _authenticate(self, user):
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}"
        )

    def test_summary_scopes_metrics_to_the_calling_company(self):
        self._authenticate(self.user)
        Application.objects.create(
            placement=self.active,
            student=self.student,
            status=Application.Status.SUBMITTED,
        )
        Application.objects.create(
            placement=self.active,
            student=self._student("grace@university.edu.ng"),
            status=Application.Status.SHORTLISTED,
        )
        Application.objects.create(
            placement=self.closed,
            student=self._student("alan@university.edu.ng"),
            status=Application.Status.ACCEPTED,
        )
        Application.objects.create(
            placement=self.other_active,
            student=self._student("anonymous@university.edu.ng"),
            status=Application.Status.ACCEPTED,
        )

        response = self.client.get(self.summary_url)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            response.data,
            {
                "active_placements": 1,
                "applicants": 3,
                "shortlisted": 1,
                "hired": 1,
            },
        )

        self._authenticate(self.other_user)
        other = self.client.get(self.summary_url)
        self.assertEqual(other.status_code, 200, other.data)
        self.assertEqual(
            other.data,
            {
                "active_placements": 1,
                "applicants": 1,
                "shortlisted": 0,
                "hired": 1,
            },
        )

    def test_summary_requires_verified_company(self):
        self.client.credentials()
        anonymous = self.client.get(self.summary_url)
        self.assertEqual(anonymous.status_code, 401)

        self._authenticate(self._student("role-check@university.edu.ng").user)
        student = self.client.get(self.summary_url)
        self.assertEqual(student.status_code, 403)
