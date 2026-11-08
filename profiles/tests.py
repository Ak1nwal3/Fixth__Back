import tempfile
from datetime import timedelta
from io import BytesIO

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import OTPCode, User
from applications.models import Application
from common.models import Document
from companies.models import Company
from placements.models import Placement
from placements.serializers import PlacementWriteSerializer
from profiles.fields import DisciplineField, SkillNamesField
from profiles.models import Discipline, DisciplineAlias, Skill, StudentProfile
from profiles.normalization import normalize_term
from verification.models import VerificationRequest, VerificationStatus
from profiles.serializers import StudentOnboardingSerializer
from profiles.services import resolve_discipline, resolve_skill, resolve_skills


class NormalizationTests(TestCase):
    def test_case_whitespace_and_punctuation_are_normalized(self):
        self.assertEqual(
            normalize_term("  Computer   Engineering. "),
            "computer engineering",
        )
        self.assertEqual(
            normalize_term("Data-analysis"),
            "data analysis",
        )
        self.assertEqual(
            normalize_term("Bachelor's"),
            "bachelors",
        )

    def test_technical_symbols_remain_distinct(self):
        self.assertEqual(normalize_term("C++"), "c++")
        self.assertEqual(normalize_term("C#"), "c#")
        self.assertEqual(normalize_term("C"), "c")
        self.assertNotEqual(normalize_term("C++"), normalize_term("C"))


class SkillResolutionTests(TestCase):
    def test_skill_reuses_case_insensitive_match(self):
        first = resolve_skill("Python")
        second = resolve_skill(" python ")
        third = resolve_skill("PYTHON")

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.pk, third.pk)
        self.assertEqual(Skill.objects.count(), 1)

    def test_similar_skill_names_are_not_merged(self):
        java = resolve_skill("Java")
        javascript = resolve_skill("JavaScript")

        self.assertNotEqual(java.pk, javascript.pk)
        self.assertEqual(Skill.objects.count(), 2)

    def test_skill_list_is_deduplicated_and_accepts_delimited_string(self):
        skills = resolve_skills("Python \u00b7 AutoCAD, python")

        self.assertEqual([skill.name for skill in skills], ["Python", "AutoCAD"])
        self.assertEqual(Skill.objects.count(), 2)

    def test_skill_names_field_resolves_and_serializes_names(self):
        field = SkillNamesField()
        skills = field.run_validation(["Python", "python"])

        self.assertEqual(len(skills), 1)
        self.assertEqual(field.to_representation(skills), ["Python"])


class DisciplineResolutionTests(TestCase):
    def test_discipline_reuses_case_insensitive_match(self):
        first = resolve_discipline("Computer Engineering")
        second = resolve_discipline("computer engineering")

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Discipline.objects.count(), 1)

    def test_abbreviation_creates_an_alias(self):
        canonical = resolve_discipline("Computer Engineering")
        abbreviated = resolve_discipline("Computer Eng.")

        self.assertEqual(canonical.pk, abbreviated.pk)
        self.assertEqual(Discipline.objects.count(), 1)
        self.assertTrue(
            DisciplineAlias.objects.filter(
                alias_key="computer eng",
                discipline=canonical,
            ).exists()
        )

    def test_typo_is_matched_conservatively(self):
        canonical = resolve_discipline("Computer Engineering")
        typo = resolve_discipline("Computer Enginering")

        self.assertEqual(canonical.pk, typo.pk)
        self.assertEqual(Discipline.objects.count(), 1)

    def test_distinct_disciplines_are_not_merged(self):
        civil = resolve_discipline("Civil Engineering")
        environmental = resolve_discipline("Environmental Engineering")

        self.assertNotEqual(civil.pk, environmental.pk)
        self.assertEqual(Discipline.objects.count(), 2)

    def test_extra_qualifier_is_not_merged(self):
        engineering = resolve_discipline("Computer Engineering")
        technology = resolve_discipline("Computer Engineering Technology")

        self.assertNotEqual(engineering.pk, technology.pk)
        self.assertEqual(Discipline.objects.count(), 2)

    def test_discipline_field_resolves_and_serializes_name(self):
        field = DisciplineField()
        discipline = field.run_validation("Computer Engineering")

        self.assertIsInstance(discipline, Discipline)
        self.assertEqual(field.to_representation(discipline), "Computer Engineering")


class PlacementDisciplineFieldTests(TestCase):
    def test_placement_accepts_a_new_discipline_name(self):
        serializer = PlacementWriteSerializer(
            data={
                "title": "Software Engineering Intern",
                "location": "Lagos",
                "work_style": "Hybrid",
                "duration": "6 months",
                "discipline": "Software Engineering",
                "level": 300,
            }
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertIsInstance(serializer.validated_data["discipline"], Discipline)
        self.assertEqual(Discipline.objects.count(), 1)


class ReferenceEndpointTests(TestCase):
    def test_discipline_search_matches_aliases(self):
        resolve_discipline("Computer Engineering")
        resolve_discipline("Computer Eng.")

        response = self.client.get(
            "/api/v1/disciplines/",
            {"search": "computer eng"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(response.json()[0]["name"], "Computer Engineering")

    def test_skill_endpoint_returns_canonical_names(self):
        resolve_skill("Python")

        response = self.client.get("/api/v1/skills/", {"search": "pyt"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [
                {
                    "id": str(Skill.objects.get().id),
                    "name": "Python",
                    "slug": "python",
                }
            ],
        )

    def test_reference_endpoints_are_read_only(self):
        response = self.client.post("/api/v1/skills/", {"name": "Python"})

        self.assertEqual(response.status_code, 405)


class OnboardingResolutionTests(TestCase):
    def test_onboarding_creates_terms_without_seed_data(self):
        otp = OTPCode(
            email="student@university.edu.ng",
            track=OTPCode.Track.STUDENT,
            purpose=OTPCode.Purpose.ONBOARDING,
        )
        otp.set_code("123456")
        otp.save()

        serializer = StudentOnboardingSerializer(
            data={
                "email": "student@university.edu.ng",
                "code": "123456",
                "first_name": "Ada",
                "last_name": "Lovelace",
                "location": "Lagos",
                "institution": "University of Lagos",
                "discipline": "Computer Engineering",
                "level": 300,
                "skills": ["Python", "AutoCAD", "python"],
            },
            context={"otp_id": otp.pk},
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        profile = serializer.save()

        self.assertEqual(profile.discipline.name, "Computer Engineering")
        self.assertCountEqual(
            [skill.name for skill in profile.skills.all()],
            ["Python", "AutoCAD"],
        )
        self.assertEqual(Discipline.objects.count(), 1)
        self.assertEqual(Skill.objects.count(), 2)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class StudentMediaAPITests(APITestCase):
    photo_url = "/api/v1/students/me/photo/"
    resume_url = "/api/v1/students/me/resume/"
    download_url = "/api/v1/documents/{}/download/"

    def setUp(self):
        self.discipline = Discipline.objects.create(name="Computer Engineering")
        self.student_user = User.objects.create_user(
            email="media-student@university.edu.ng",
            password="StrongPass!234",
            role=User.Role.STUDENT,
            email_verified_at=timezone.now(),
        )
        self.student = StudentProfile.objects.create(
            user=self.student_user,
            first_name="Ada",
            last_name="Lovelace",
            phone="+2348010000000",
            location="Lagos",
            institution="University of Lagos",
            discipline=self.discipline,
            level=400,
        )
        self.company_user, self.company = self._company(
            "media-company@novalabs.example"
        )
        self.placement = self._placement()
        self._authenticate(self.student_user)

    def _company(self, email):
        user = User.objects.create_user(
            email=email,
            password="StrongPass!234",
            role=User.Role.COMPANY,
            email_verified_at=timezone.now(),
        )
        company = Company.objects.create(
            owner=user,
            name="Nova Labs",
            industry=Company.Industry.TECHNOLOGY,
            country="Nigeria",
            city="Lagos",
        )
        return user, company

    def _placement(self):
        return Placement.objects.create(
            company=self.company,
            title="Backend Engineering Intern",
            location="Lagos",
            work_style=Placement.WorkStyle.HYBRID,
            duration="6 months",
            discipline=self.discipline,
            level=300,
            status=Placement.Status.ACTIVE,
            created_by=self.company_user,
        )

    def _authenticate(self, user):
        refresh = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    def _photo(self, name="photo.png", image_format="PNG", size=32):
        buffer = BytesIO()
        Image.new("RGB", (size, size), (20, 80, 140)).save(
            buffer,
            format=image_format,
        )
        return SimpleUploadedFile(
            name,
            buffer.getvalue(),
            content_type=f"image/{image_format.lower()}",
        )

    def _resume(self, name="ada-resume.pdf"):
        return SimpleUploadedFile(
            name,
            b"%PDF-1.4\n%Test resume\n",
            content_type="application/pdf",
        )

    def test_photo_and_resume_uploads_replacements_and_deletions(self):
        invalid_photo = self.client.post(
            self.photo_url,
            {"file": SimpleUploadedFile("bad.png", b"not-an-image", content_type="image/png")},
            format="multipart",
        )
        self.assertEqual(invalid_photo.status_code, 400, invalid_photo.data)

        invalid_resume = self.client.post(
            self.resume_url,
            {"file": SimpleUploadedFile("resume.txt", b"text", content_type="text/plain")},
            format="multipart",
        )
        self.assertEqual(invalid_resume.status_code, 400, invalid_resume.data)

        photo_response = self.client.post(
            self.photo_url,
            {"file": self._photo()},
            format="multipart",
        )
        self.assertEqual(photo_response.status_code, 200, photo_response.data)
        self.student.refresh_from_db()
        photo = self.student.photo
        self.assertFalse(photo.is_private)
        self.assertEqual(photo.original_name, "photo.png")
        self.assertEqual(photo.mime_type, "image/png")
        self.assertTrue(photo.file.storage.exists(photo.file.name))

        old_photo_name = photo.file.name
        with self.captureOnCommitCallbacks(execute=True):
            photo_response = self.client.post(
                self.photo_url,
                {"file": self._photo("replacement.png")},
                format="multipart",
            )
        self.assertEqual(photo_response.status_code, 200, photo_response.data)
        self.student.refresh_from_db()
        self.assertFalse(Document.objects.filter(pk=photo.id).exists())
        self.assertFalse(default_storage.exists(old_photo_name))

        resume_response = self.client.post(
            self.resume_url,
            {"file": self._resume()},
            format="multipart",
        )
        self.assertEqual(resume_response.status_code, 201, resume_response.data)
        self.student.refresh_from_db()
        resume = self.student.resume
        self.assertTrue(resume.is_private)
        self.assertEqual(resume.original_name, "ada-resume.pdf")
        self.assertEqual(resume.mime_type, "application/pdf")
        self.assertTrue(
            resume_response.data["url"].endswith(
                f"/api/v1/documents/{resume.id}/download/"
            )
        )
        self.assertNotIn("://", resume_response.data["file"])

        old_resume_name = resume.file.name
        with self.captureOnCommitCallbacks(execute=True):
            resume_response = self.client.post(
                self.resume_url,
                {"file": self._resume("replacement-resume.pdf")},
                format="multipart",
            )
        self.assertEqual(resume_response.status_code, 201, resume_response.data)
        self.student.refresh_from_db()
        self.assertFalse(Document.objects.filter(pk=resume.id).exists())
        self.assertFalse(default_storage.exists(old_resume_name))

        deleted_photo = self.client.delete(self.photo_url)
        self.assertEqual(deleted_photo.status_code, 204)
        self.student.refresh_from_db()
        self.assertIsNone(self.student.photo)

        with self.captureOnCommitCallbacks(execute=True):
            deleted_resume = self.client.delete(self.resume_url)
        self.assertEqual(deleted_resume.status_code, 204)
        self.student.refresh_from_db()
        self.assertIsNone(self.student.resume)

    def test_resume_details_are_returned_or_404_when_missing(self):
        missing = self.client.get(self.resume_url)
        self.assertEqual(missing.status_code, 404, missing.data)

        resume = Document.objects.create(
            owner=self.student_user,
            kind=Document.Kind.RESUME,
            file=SimpleUploadedFile("ada-resume.pdf", b"%PDF-1.4\n"),
            original_name="ada-resume.pdf",
            mime_type="application/pdf",
            size=1024,
        )
        self.student.resume = resume
        self.student.save(update_fields=["resume", "updated_at"])

        response = self.client.get(self.resume_url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["id"], str(resume.id))
        self.assertEqual(response.data["original_name"], "ada-resume.pdf")
        self.assertEqual(response.data["mime_type"], "application/pdf")
        self.assertEqual(response.data["size"], 1024)
        self.assertIn("/api/v1/documents/", response.data["url"])
        self.assertIn("download/", response.data["url"])

    def test_owner_and_application_company_can_download_resume(self):
        resume = Document.objects.create(
            owner=self.student_user,
            kind=Document.Kind.RESUME,
            file=SimpleUploadedFile("ada-resume.pdf", b"%PDF-1.4\n"),
            original_name="ada-resume.pdf",
            mime_type="application/pdf",
            size=1024,
            scan_status=Document.ScanStatus.CLEAN,
        )
        Application.objects.create(
            placement=self.placement,
            student=self.student,
            resume=resume,
        )

        owner = self.client.get(self.download_url.format(resume.id))
        self.assertEqual(owner.status_code, 200)
        self.assertEqual(b"".join(owner.streaming_content), b"%PDF-1.4\n")
        self.assertIn("attachment;", owner["Content-Disposition"])
        self.assertIn("ada-resume.pdf", owner["Content-Disposition"])

        self._authenticate(self.company_user)
        company = self.client.get(self.download_url.format(resume.id))
        self.assertEqual(company.status_code, 200)
        self.assertEqual(b"".join(company.streaming_content), b"%PDF-1.4\n")
        self.assertIn("ada-resume.pdf", company["Content-Disposition"])

    def test_document_downloads_enforce_authentication_and_access(self):
        resume = Document.objects.create(
            owner=self.student_user,
            kind=Document.Kind.RESUME,
            file=SimpleUploadedFile("private-resume.pdf", b"%PDF-1.4\n"),
            original_name="private-resume.pdf",
            mime_type="application/pdf",
            size=1024,
            scan_status=Document.ScanStatus.CLEAN,
        )
        url = self.download_url.format(resume.id)

        self.client.credentials()
        anonymous = self.client.get(url)
        self.assertEqual(anonymous.status_code, 401)

        other_student_user = User.objects.create_user(
            email="other-media-student@university.edu.ng",
            password="StrongPass!234",
            role=User.Role.STUDENT,
            email_verified_at=timezone.now(),
        )
        StudentProfile.objects.create(
            user=other_student_user,
            first_name="Grace",
            last_name="Hopper",
            phone="+2348010000001",
            location="Lagos",
            institution="University of Lagos",
            discipline=self.discipline,
            level=400,
        )
        self._authenticate(other_student_user)
        other_student = self.client.get(url)
        self.assertEqual(other_student.status_code, 403)

        other_company_user, other_company = self._company(
            "other-media-company@otherlabs.example"
        )
        other_company.placements.create(
            title="Other Company Placement",
            location="Lagos",
            work_style=Placement.WorkStyle.HYBRID,
            duration="6 months",
            discipline=self.discipline,
            level=300,
            status=Placement.Status.ACTIVE,
        )
        self._authenticate(other_company_user)
        unauthorized_company = self.client.get(url)
        self.assertEqual(unauthorized_company.status_code, 403)

        admin = User.objects.create_user(
            email="media-admin@fixth.example",
            password="StrongPass!234",
            role=User.Role.ADMIN,
            email_verified_at=timezone.now(),
            is_staff=True,
            is_superuser=True,
        )
        self._authenticate(admin)
        admin_download = self.client.get(url)
        self.assertEqual(admin_download.status_code, 200)


class StudentVerificationAndDeletionTests(APITestCase):
    verification_url = "/api/v1/students/me/verification/"
    me_url = "/api/v1/students/me/"

    def setUp(self):
        self.discipline = Discipline.objects.create(name="Computer Engineering")
        self.student_user = User.objects.create_user(
            email="account-student@university.edu.ng",
            password="StrongPass!234",
            role=User.Role.STUDENT,
            email_verified_at=timezone.now(),
        )
        self.student = StudentProfile.objects.create(
            user=self.student_user,
            first_name="Ada",
            last_name="Lovelace",
            phone="+2348010000000",
            location="Lagos",
            institution="University of Lagos",
            discipline=self.discipline,
            level=400,
        )
        self.company_user, self.company = self._company()
        self.placement = self._placement()
        self._authenticate(self.student_user)

    def _company(self, email="account-company@novalabs.example"):
        user = User.objects.create_user(
            email=email,
            password="StrongPass!234",
            role=User.Role.COMPANY,
            email_verified_at=timezone.now(),
        )
        company = Company.objects.create(
            owner=user,
            name="Nova Labs",
            industry=Company.Industry.TECHNOLOGY,
            country="Nigeria",
            city="Lagos",
        )
        return user, company

    def _placement(self):
        return Placement.objects.create(
            company=self.company,
            title="Backend Engineering Intern",
            location="Lagos",
            work_style=Placement.WorkStyle.HYBRID,
            duration="6 months",
            discipline=self.discipline,
            level=300,
            status=Placement.Status.ACTIVE,
            created_by=self.company_user,
        )

    def _authenticate(self, user):
        refresh = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
        return refresh

    def test_student_verification_submission_is_pending(self):
        response = self.client.post(self.verification_url, {}, format="json")

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["kind"], VerificationRequest.Kind.STUDENT)
        self.assertEqual(response.data["status"], VerificationStatus.PENDING)
        self.assertEqual(
            str(response.data["subject_id"]),
            str(self.student.id),
        )
        self.assertIsNotNone(response.data["submitted_at"])

        verification = VerificationRequest.objects.get()
        self.student.refresh_from_db()
        self.assertEqual(verification.student, self.student)
        self.assertEqual(verification.status, VerificationStatus.PENDING)
        self.assertEqual(
            self.student.verification_status,
            VerificationStatus.PENDING,
        )

    def test_students_cannot_change_verification_status(self):
        VerificationRequest.objects.create(
            kind=VerificationRequest.Kind.STUDENT,
            student=self.student,
            status=VerificationStatus.PENDING,
            risk_level=self.student.risk_level,
        )

        profile_change = self.client.patch(
            self.me_url,
            {
                "verification_status": VerificationStatus.VERIFIED,
                "risk_level": "Low",
            },
            format="json",
        )
        self.assertEqual(profile_change.status_code, 200, profile_change.data)

        verification_change = self.client.patch(
            self.verification_url,
            {"status": VerificationStatus.VERIFIED},
            format="json",
        )
        self.assertEqual(verification_change.status_code, 405)

        duplicate = self.client.post(self.verification_url, {}, format="json")
        self.assertEqual(duplicate.status_code, 409)

        self.student.refresh_from_db()
        verification = VerificationRequest.objects.get()
        self.assertEqual(
            self.student.verification_status,
            VerificationStatus.PENDING,
        )
        self.assertEqual(verification.status, VerificationStatus.PENDING)

    def test_account_deletion_soft_deletes_anonymizes_and_revokes_access(self):
        refresh = self._authenticate(self.student_user)
        original_email = self.student_user.email

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(
                self.me_url,
                {"confirm": True},
                format="json",
            )

        self.assertEqual(response.status_code, 204)
        self.student_user.refresh_from_db()
        self.student.refresh_from_db()

        self.assertFalse(self.student_user.is_active)
        self.assertEqual(self.student_user.account_status, User.AccountStatus.DELETED)
        self.assertNotEqual(self.student_user.email, original_email)
        self.assertFalse(self.student_user.has_usable_password())
        self.assertIsNotNone(self.student.deleted_at)
        self.assertEqual(self.student.first_name, "")

        self._authenticate(self.student_user)
        authenticated = self.client.get(self.me_url)
        self.assertEqual(authenticated.status_code, 401)

        refresh_response = self.client.post(
            "/api/v1/auth/student/refresh/",
            {"refresh": str(refresh)},
            format="json",
        )
        self.assertEqual(refresh_response.status_code, 401)

    def test_account_deletion_retains_applications_and_attached_resumes(self):
        resume = Document.objects.create(
            owner=self.student_user,
            kind=Document.Kind.RESUME,
            file=SimpleUploadedFile(
                "retained-resume.pdf",
                b"%PDF-1.4\n",
                content_type="application/pdf",
            ),
            original_name="retained-resume.pdf",
            mime_type="application/pdf",
            size=1024,
        )
        self.student.resume = resume
        self.student.save(update_fields=["resume", "updated_at"])
        photo = Document.objects.create(
            owner=self.student_user,
            kind=Document.Kind.PROFILE_PHOTO,
            file=SimpleUploadedFile(
                "profile.png",
                b"photo-bytes",
                content_type="image/png",
            ),
            original_name="profile.png",
            mime_type="image/png",
            size=1024,
        )
        self.student.photo = photo
        self.student.save(update_fields=["photo", "updated_at"])
        application = Application.objects.create(
            placement=self.placement,
            student=self.student,
            resume=resume,
            cover_note="Personal contact details are in this note.",
        )

        original_file_name = resume.file.name
        original_photo_name = photo.file.name
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(
                self.me_url,
                {"confirm": True},
                format="json",
            )

        self.assertEqual(response.status_code, 204, response.data)
        self.student.refresh_from_db()
        application.refresh_from_db()
        resume.refresh_from_db()
        self.assertTrue(Application.objects.filter(pk=application.pk).exists())
        self.assertEqual(application.student, self.student)
        self.assertEqual(application.resume, resume)
        self.assertTrue(default_storage.exists(original_file_name))
        self.assertFalse(default_storage.exists(original_photo_name))
        self.assertFalse(Document.objects.filter(pk=photo.id).exists())
        self.assertEqual(self.student.location, "Deleted account")
        self.assertEqual(application.cover_note, "")

