from datetime import timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from applications.models import Application
from companies.models import Company
from placements.models import Placement, SavedPlacement
from profiles.models import Discipline, StudentProfile

COMPANY_PLACEMENTS_URL = "/api/v1/company/placements/"
PUBLIC_PLACEMENTS_URL = "/api/v1/placements/"


def placement_payload(**overrides):
    payload = {
        "title": "Backend Engineering Intern",
        "location": "Lagos",
        "work_style": "Hybrid",
        "duration": "6 months",
        "discipline": "Computer Engineering",
        "level": 300,
        "stipend_amount": "150000.00",
        "currency": "NGN",
        "description": "Build and maintain the Fixth API.",
        "responsibilities": "Ship features, write tests, and review code.",
        "requirements": "Python, Django, and REST APIs.",
        "status": Placement.Status.ACTIVE,
    }
    payload.update(overrides)
    return payload


class PlacementAPITests(APITestCase):
    def setUp(self):
        self.company_user, self.company = self._company("Nova Labs")
        self._authenticate(self.company_user)

    def _company(self, name, email=None):
        user = User.objects.create_user(
            email=email or f"{name.lower().replace(' ', '')}@example.com",
            password="StrongPass!234",
            role=User.Role.COMPANY,
            email_verified_at=timezone.now(),
        )
        company = Company.objects.create(
            owner=user,
            name=name,
            industry=Company.Industry.TECHNOLOGY,
            country="Nigeria",
            city="Lagos",
        )
        return user, company

    def _authenticate(self, user):
        refresh = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    def _create(self, **overrides):
        return self.client.post(
            COMPANY_PLACEMENTS_URL,
            placement_payload(**overrides),
            format="json",
        )

    def test_company_creates_draft_and_can_preview_it(self):
        response = self._create(
            status=Placement.Status.DRAFT,
            description="",
            responsibilities="",
            requirements="",
            stipend_amount=None,
            company="00000000-0000-0000-0000-000000000000",
            slug="client-owned-slug",
            reports_count=99,
            published_at="2000-01-01T00:00:00Z",
            created_by="00000000-0000-0000-0000-000000000000",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], Placement.Status.DRAFT)
        self.assertIsNone(response.data["published_at"])

        placement = Placement.objects.get()
        self.assertEqual(placement.company, self.company)
        self.assertEqual(placement.created_by, self.company_user)
        self.assertNotEqual(placement.slug, "client-owned-slug")
        self.assertEqual(placement.reports_count, 0)
        self.assertIsNone(placement.published_at)

        preview = self.client.get(
            f"{COMPANY_PLACEMENTS_URL}{placement.id}/"
        )
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(preview.data["title"], placement.title)

        public = self.client.get(PUBLIC_PLACEMENTS_URL)
        self.assertEqual(public.status_code, 200, public.data)
        self.assertEqual(public.data["count"], 0)

        detail = self.client.get(f"{PUBLIC_PLACEMENTS_URL}{placement.slug}/")
        self.assertEqual(detail.status_code, 404)

    def test_published_placement_appears_in_student_discovery(self):
        response = self._create()

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], Placement.Status.ACTIVE)
        self.assertIsNotNone(response.data["published_at"])
        self.assertEqual(response.data["company"], self.company.name)
        self.assertFalse(response.data["company_verified"])
        self.assertEqual(response.data["stipend"], "₦150,000")

        placement = Placement.objects.get()
        public = self.client.get(PUBLIC_PLACEMENTS_URL)
        self.assertEqual(public.status_code, 200, public.data)
        self.assertEqual(public.data["count"], 1)
        self.assertEqual(public.data["results"][0]["slug"], placement.slug)

        by_slug = self.client.get(f"{PUBLIC_PLACEMENTS_URL}{placement.slug}/")
        self.assertEqual(by_slug.status_code, 200, by_slug.data)
        self.assertEqual(by_slug.data["title"], placement.title)
        self.assertEqual(
            by_slug.data["eligibility"]["required_discipline"],
            placement.discipline.name,
        )

        by_id = self.client.get(
            f"{PUBLIC_PLACEMENTS_URL}by-id/{placement.id}/"
        )
        self.assertEqual(by_id.status_code, 200, by_id.data)
        self.assertEqual(by_id.data["id"], str(placement.id))

    def test_placement_type_can_be_set_and_cleared(self):
        response = self._create(type=Placement.Type.SIWES)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["type"], Placement.Type.SIWES)

        placement = Placement.objects.get()
        self.assertEqual(placement.type, Placement.Type.SIWES)

        response = self.client.patch(
            f"{COMPANY_PLACEMENTS_URL}{placement.id}/",
            {"type": ""},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data["type"])
        placement.refresh_from_db()
        self.assertIsNone(placement.type)

        invalid = self.client.patch(
            f"{COMPANY_PLACEMENTS_URL}{placement.id}/",
            {"type": "Unknown"},
            format="json",
        )
        self.assertEqual(invalid.status_code, 400)

    def test_publish_requires_complete_content(self):
        draft = self._create(
            status=Placement.Status.DRAFT,
            description="",
            responsibilities="",
            requirements="",
            stipend_amount=None,
        )
        self.assertEqual(draft.status_code, 201, draft.data)
        placement = Placement.objects.get()
        detail_url = f"{COMPANY_PLACEMENTS_URL}{placement.id}/"

        publish = self.client.post(f"{detail_url}publish/")
        self.assertEqual(publish.status_code, 400)
        self.assertIn("description", publish.data)

        patch = self.client.patch(
            detail_url,
            {"status": Placement.Status.ACTIVE},
            format="json",
        )
        self.assertEqual(patch.status_code, 400)
        placement.refresh_from_db()
        self.assertEqual(placement.status, Placement.Status.DRAFT)
        self.assertIsNone(placement.published_at)

        patch = self.client.patch(
            detail_url,
            {
                "description": "Build and maintain the Fixth API.",
                "responsibilities": "Ship features and review code.",
                "requirements": "Python and Django.",
                "status": Placement.Status.ACTIVE,
            },
            format="json",
        )
        self.assertEqual(patch.status_code, 200, patch.data)
        self.assertEqual(patch.data["status"], Placement.Status.ACTIVE)

        placement.refresh_from_db()
        self.assertEqual(placement.status, Placement.Status.ACTIVE)
        self.assertIsNotNone(placement.published_at)
        # An unpaid internship publishes with no stipend amount.
        self.assertIsNone(placement.stipend_amount)

    def test_negative_stipend_is_rejected(self):
        create = self._create(stipend_amount="-500.00")
        self.assertEqual(create.status_code, 400, create.data)

    def test_company_cannot_manage_another_companys_placement(self):
        response = self._create()
        self.assertEqual(response.status_code, 201, response.data)
        placement = Placement.objects.get()

        other_user, other_company = self._company("Other Labs", "other@example.com")
        self._authenticate(other_user)

        detail = self.client.get(f"{COMPANY_PLACEMENTS_URL}{placement.id}/")
        self.assertEqual(detail.status_code, 404)

        patch = self.client.patch(
            f"{COMPANY_PLACEMENTS_URL}{placement.id}/",
            {"title": "Hijacked"},
            format="json",
        )
        self.assertEqual(patch.status_code, 404)

        publish = self.client.post(
            f"{COMPANY_PLACEMENTS_URL}{placement.id}/publish/"
        )
        self.assertEqual(publish.status_code, 404)

        own_list = self.client.get(COMPANY_PLACEMENTS_URL)
        self.assertEqual(own_list.status_code, 200)
        self.assertEqual(own_list.data["count"], 0)
        self.assertEqual(other_company.placements.count(), 0)

        placement.refresh_from_db()
        self.assertEqual(placement.title, "Backend Engineering Intern")

    def test_company_endpoint_requires_verified_company_account(self):
        self.client.credentials()
        anonymous = self.client.post(
            COMPANY_PLACEMENTS_URL,
            placement_payload(),
            format="json",
        )
        self.assertEqual(anonymous.status_code, 401)

        student = User.objects.create_user(
            email="student@university.edu.ng",
            password="StrongPass!234",
            role=User.Role.STUDENT,
            email_verified_at=timezone.now(),
        )
        self._authenticate(student)
        wrong_role = self.client.post(
            COMPANY_PLACEMENTS_URL,
            placement_payload(),
            format="json",
        )
        self.assertEqual(wrong_role.status_code, 403)

        unverified = User.objects.create_user(
            email="unverified@example.com",
            password="StrongPass!234",
            role=User.Role.COMPANY,
        )
        Company.objects.create(
            owner=unverified,
            name="Unverified Labs",
            industry=Company.Industry.TECHNOLOGY,
            country="Nigeria",
            city="Lagos",
        )
        self._authenticate(unverified)
        unverified_response = self.client.post(
            COMPANY_PLACEMENTS_URL,
            placement_payload(),
            format="json",
        )
        self.assertEqual(unverified_response.status_code, 403)

        no_company = User.objects.create_user(
            email="nocompany@example.com",
            password="StrongPass!234",
            role=User.Role.COMPANY,
            email_verified_at=timezone.now(),
        )
        self._authenticate(no_company)
        no_profile = self.client.post(
            COMPANY_PLACEMENTS_URL,
            placement_payload(),
            format="json",
        )
        self.assertEqual(no_profile.status_code, 403)

        self.assertFalse(Placement.objects.exists())

    def test_close_reopen_and_illegal_transitions(self):
        response = self._create()
        self.assertEqual(response.status_code, 201, response.data)
        placement = Placement.objects.get()
        detail_url = f"{COMPANY_PLACEMENTS_URL}{placement.id}/"

        closed = self.client.patch(
            detail_url,
            {"status": Placement.Status.CLOSED},
            format="json",
        )
        self.assertEqual(closed.status_code, 200, closed.data)
        self.assertEqual(closed.data["status"], Placement.Status.CLOSED)
        placement.refresh_from_db()
        self.assertIsNotNone(placement.closed_at)

        public = self.client.get(PUBLIC_PLACEMENTS_URL)
        self.assertEqual(public.data["count"], 0)
        public_detail = self.client.get(f"{PUBLIC_PLACEMENTS_URL}{placement.slug}/")
        self.assertEqual(public_detail.status_code, 404)

        reopened = self.client.patch(
            detail_url,
            {"status": Placement.Status.ACTIVE},
            format="json",
        )
        self.assertEqual(reopened.status_code, 200, reopened.data)
        self.assertEqual(reopened.data["status"], Placement.Status.ACTIVE)
        placement.refresh_from_db()
        self.assertIsNone(placement.closed_at)

        public = self.client.get(PUBLIC_PLACEMENTS_URL)
        self.assertEqual(public.data["count"], 1)

        illegal = self.client.patch(
            detail_url,
            {"status": Placement.Status.DRAFT},
            format="json",
        )
        self.assertEqual(illegal.status_code, 409)
        placement.refresh_from_db()
        self.assertEqual(placement.status, Placement.Status.ACTIVE)

    def test_public_discovery_filters_and_hides_expired_listings(self):
        first = self._create(title="Platform Engineering Intern")
        self.assertEqual(first.status_code, 201, first.data)
        second = self._create(
            title="Data Engineering Intern",
            discipline="Data Science",
            work_style="Remote",
            location="Abuja",
        )
        self.assertEqual(second.status_code, 201, second.data)

        expired = Placement.objects.get(title="Data Engineering Intern")
        expired.expires_at = timezone.now() - timedelta(minutes=1)
        expired.save(update_fields=["expires_at", "updated_at"])

        response = self.client.get(PUBLIC_PLACEMENTS_URL)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(
            response.data["results"][0]["title"],
            "Platform Engineering Intern",
        )

        search = self.client.get(
            PUBLIC_PLACEMENTS_URL,
            {"q": "Platform"},
        )
        self.assertEqual(search.data["count"], 1)

        remote = self.client.get(
            PUBLIC_PLACEMENTS_URL,
            {"work_style": "Remote"},
        )
        self.assertEqual(remote.data["count"], 0)

        invalid_sort = self.client.get(
            PUBLIC_PLACEMENTS_URL,
            {"sort": "unsupported"},
        )
        self.assertEqual(invalid_sort.status_code, 400)

    def test_company_placement_status_filters(self):
        draft = self._create(
            title="Draft Role",
            status=Placement.Status.DRAFT,
            description="",
            responsibilities="",
            requirements="",
            stipend_amount=None,
        )
        self.assertEqual(draft.status_code, 201, draft.data)

        active = self._create(title="Active Role")
        self.assertEqual(active.status_code, 201, active.data)

        closed = self._create(title="Closed Role")
        self.assertEqual(closed.status_code, 201, closed.data)
        closed_placement = Placement.objects.get(title="Closed Role")
        close = self.client.patch(
            f"{COMPANY_PLACEMENTS_URL}{closed_placement.id}/",
            {"status": Placement.Status.CLOSED},
            format="json",
        )
        self.assertEqual(close.status_code, 200, close.data)

        expired = self._create(
            title="Expired Role",
            expires_at=(timezone.now() - timedelta(minutes=1)).isoformat(),
        )
        self.assertEqual(expired.status_code, 201, expired.data)

        expected = {
            Placement.Status.DRAFT: 1,
            Placement.Status.ACTIVE: 1,
            Placement.Status.CLOSED: 1,
            Placement.Status.EXPIRED: 1,
            "All": 4,
        }
        for status_filter, count in expected.items():
            response = self.client.get(
                COMPANY_PLACEMENTS_URL,
                {"status": status_filter},
            )
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data["count"], count, status_filter)

        lower = self.client.get(
            COMPANY_PLACEMENTS_URL,
            {"status": "active"},
        )
        self.assertEqual(lower.status_code, 200, lower.data)
        self.assertEqual(lower.data["count"], 1)

        invalid = self.client.get(
            COMPANY_PLACEMENTS_URL,
            {"status": "Unknown"},
        )
        self.assertEqual(invalid.status_code, 400)

    def _student(self, email, discipline):
        user = User.objects.create_user(
            email=email,
            password="StrongPass!234",
            role=User.Role.STUDENT,
            email_verified_at=timezone.now(),
        )
        return StudentProfile.objects.create(
            user=user,
            first_name=email.split("@")[0].title(),
            last_name="Applicant",
            phone="+2348010000000",
            location="Lagos",
            institution="University of Lagos",
            discipline=discipline,
            level=400,
        )

    def test_company_placement_list_includes_applicant_counts_without_n_plus_one(self):
        discipline = Discipline.objects.create(name="Computer Engineering")
        active = self._create(title="Active Role")
        self.assertEqual(active.status_code, 201, active.data)
        draft = self._create(
            title="Draft Role",
            status=Placement.Status.DRAFT,
            description="",
            responsibilities="",
            requirements="",
            stipend_amount=None,
        )
        self.assertEqual(draft.status_code, 201, draft.data)
        closed_response = self._create(title="Closed Role")
        self.assertEqual(closed_response.status_code, 201, closed_response.data)
        closed = Placement.objects.get(title="Closed Role")
        self.client.patch(
            f"{COMPANY_PLACEMENTS_URL}{closed.id}/",
            {"status": Placement.Status.CLOSED},
            format="json",
        )

        active_placement = Placement.objects.get(title="Active Role")
        for index, status_value in enumerate(
            [
                Application.Status.SUBMITTED,
                Application.Status.SUBMITTED,
                Application.Status.SHORTLISTED,
                Application.Status.ACCEPTED,
            ]
        ):
            Application.objects.create(
                placement=active_placement,
                student=self._student(f"applicant{index}@university.edu.ng", discipline),
                status=status_value,
            )
        Application.objects.create(
            placement=closed,
            student=self._student("closed-applicant@university.edu.ng", discipline),
            status=Application.Status.SUBMITTED,
        )

        with CaptureQueriesContext(connection) as context:
            response = self.client.get(COMPANY_PLACEMENTS_URL)
        self.assertEqual(response.status_code, 200, response.data)

        counts = {
            item["title"]: (
                item["applicants_count"],
                item["new_applicants_count"],
                item["shortlisted_count"],
            )
            for item in response.data["results"]
        }
        self.assertEqual(
            counts,
            {
                "Active Role": (4, 2, 1),
                "Closed Role": (1, 1, 0),
                "Draft Role": (0, 0, 0),
            },
        )
        # Authentication, pagination/count, and one annotated listing query.
        self.assertLessEqual(len(context.captured_queries), 5)

    def test_public_facets_are_dynamic_and_filter_discovery(self):
        self._create(
            title="Backend Engineering Intern",
            discipline="Computer Engineering",
            location="Lagos",
            duration="6 months",
            work_style="Hybrid",
        )
        self._create(
            title="Data Engineering Intern",
            discipline="Data Science",
            location="Abuja",
            duration="3 months",
            work_style="Remote",
        )
        self._create(
            title="Field Engineering Intern",
            discipline="Computer Engineering",
            location="Ibadan",
            duration="12 months",
            work_style="On-site",
        )

        expired = Placement.objects.get(title="Field Engineering Intern")
        expired.expires_at = timezone.now() - timedelta(minutes=1)
        expired.save(update_fields=["expires_at", "updated_at"])

        response = self.client.get("/api/v1/placements/facets/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            response.data,
            {
                "disciplines": ["Computer Engineering", "Data Science"],
                "locations": ["Abuja", "Lagos"],
                "durations": ["3 months", "6 months"],
                "work_styles": ["Hybrid", "Remote"],
            },
        )

        discipline_filter = self.client.get(
            PUBLIC_PLACEMENTS_URL,
            {"discipline": response.data["disciplines"][0]},
        )
        self.assertEqual(discipline_filter.data["count"], 1)
        self.assertEqual(
            discipline_filter.data["results"][0]["title"],
            "Backend Engineering Intern",
        )

        location_filter = self.client.get(
            PUBLIC_PLACEMENTS_URL,
            {"location": response.data["locations"][0]},
        )
        self.assertEqual(location_filter.data["count"], 1)
        self.assertEqual(
            location_filter.data["results"][0]["title"],
            "Data Engineering Intern",
        )

        duration_filter = self.client.get(
            PUBLIC_PLACEMENTS_URL,
            {"duration": response.data["durations"][0]},
        )
        self.assertEqual(duration_filter.data["count"], 1)
        self.assertEqual(
            duration_filter.data["results"][0]["title"],
            "Data Engineering Intern",
        )

        style_filter = self.client.get(
            PUBLIC_PLACEMENTS_URL,
            {"work_style": response.data["work_styles"][0]},
        )
        self.assertEqual(style_filter.data["count"], 1)
        self.assertEqual(
            style_filter.data["results"][0]["title"],
            "Backend Engineering Intern",
        )


class SavedPlacementAPITests(APITestCase):
    saved_list_url = "/api/v1/students/me/saved-placements/"

    def save_url(self, placement_id):
        return f"/api/v1/placements/{placement_id}/save/"

    def setUp(self):
        self.discipline = Discipline.objects.create(name="Computer Engineering")
        self.company_user = User.objects.create_user(
            email="talent@novalabs.example",
            password="StrongPass!234",
            role=User.Role.COMPANY,
            email_verified_at=timezone.now(),
        )
        self.company = Company.objects.create(
            owner=self.company_user,
            name="Nova Labs",
            industry=Company.Industry.TECHNOLOGY,
            country="Nigeria",
            city="Lagos",
        )
        self.student_user = User.objects.create_user(
            email="ada@university.edu.ng",
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
        self.other_student_user = User.objects.create_user(
            email="grace@university.edu.ng",
            password="StrongPass!234",
            role=User.Role.STUDENT,
            email_verified_at=timezone.now(),
        )
        self.other_student = StudentProfile.objects.create(
            user=self.other_student_user,
            first_name="Grace",
            last_name="Hopper",
            phone="+2348010000001",
            location="Lagos",
            institution="University of Lagos",
            discipline=self.discipline,
            level=400,
        )
        self.active_placement = self._placement("Backend Engineering Intern")
        self._authenticate(self.student_user)

    def _placement(self, title, **overrides):
        return Placement.objects.create(
            company=self.company,
            title=title,
            location="Lagos",
            work_style=Placement.WorkStyle.HYBRID,
            duration="6 months",
            discipline=self.discipline,
            level=300,
            status=Placement.Status.ACTIVE,
            created_by=self.company_user,
            **overrides,
        )

    def _authenticate(self, user):
        refresh = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    def test_student_can_save_placement_and_duplicates_are_rejected(self):
        missing = self.client.post(
            self.save_url("00000000-0000-0000-0000-000000000000")
        )
        self.assertEqual(missing.status_code, 404)

        draft = self._placement(
            "Draft Role",
            description="",
            responsibilities="",
            requirements="",
            stipend_amount=None,
        )
        draft.status = Placement.Status.DRAFT
        draft.save(update_fields=["status", "updated_at"])
        unavailable = self.client.post(self.save_url(draft.id))
        self.assertEqual(unavailable.status_code, 404)

        response = self.client.post(self.save_url(self.active_placement.id))
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(
            response.data["placement"]["id"],
            str(self.active_placement.id),
        )
        self.assertEqual(response.data["placement"]["status"], "Active")
        self.assertIn("expires_at", response.data["placement"])
        self.assertIn("created_at", response.data)
        self.assertEqual(SavedPlacement.objects.count(), 1)

        duplicate = self.client.post(self.save_url(self.active_placement.id))
        self.assertEqual(duplicate.status_code, 409, duplicate.data)
        self.assertEqual(SavedPlacement.objects.count(), 1)

    def test_student_can_remove_only_their_own_saved_record(self):
        saved = SavedPlacement.objects.create(
            student=self.student,
            placement=self.active_placement,
        )

        response = self.client.delete(self.save_url(self.active_placement.id))
        self.assertEqual(response.status_code, 204)
        self.assertFalse(SavedPlacement.objects.filter(pk=saved.pk).exists())

        missing = self.client.delete(self.save_url(self.active_placement.id))
        self.assertEqual(missing.status_code, 404)

    def test_student_retrieves_only_their_saved_placements(self):
        closed = self._placement("Closed Engineering Intern")
        self.active_placement.type = Placement.Type.SIWES
        self.active_placement.save(update_fields=["type", "updated_at"])
        SavedPlacement.objects.create(
            student=self.student,
            placement=self.active_placement,
        )
        SavedPlacement.objects.create(student=self.student, placement=closed)
        other_saved = SavedPlacement.objects.create(
            student=self.other_student,
            placement=self.active_placement,
        )
        closed.set_status(Placement.Status.CLOSED)

        response = self.client.get(self.saved_list_url)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(
            {item["placement"]["id"] for item in response.data["results"]},
            {
                str(self.active_placement.id),
                str(closed.id),
            },
        )
        self.assertNotIn(
            str(other_saved.id),
            {item["id"] for item in response.data["results"]},
        )
        self.assertEqual(
            {item["placement"]["status"] for item in response.data["results"]},
            {"Active", "Closed"},
        )
        self.assertEqual(
            {
                item["placement"]["type"]
                for item in response.data["results"]
                if item["placement"]["id"] == str(self.active_placement.id)
            },
            {Placement.Type.SIWES},
        )

    def test_saved_placement_endpoints_require_student_account(self):
        missing_id = "00000000-0000-0000-0000-000000000000"
        self.client.credentials()
        self.assertEqual(self.client.get(self.saved_list_url).status_code, 401)
        self.assertEqual(self.client.post(self.save_url(missing_id)).status_code, 401)
        self.assertEqual(self.client.delete(self.save_url(missing_id)).status_code, 401)

        company = User.objects.create_user(
            email="other-company@novalabs.example",
            password="StrongPass!234",
            role=User.Role.COMPANY,
            email_verified_at=timezone.now(),
        )
        self._authenticate(company)
        self.assertEqual(self.client.get(self.saved_list_url).status_code, 403)
        self.assertEqual(self.client.post(self.save_url(missing_id)).status_code, 403)
        self.assertEqual(self.client.delete(self.save_url(missing_id)).status_code, 403)

        admin = User.objects.create_user(
            email="admin@fixth.example",
            password="StrongPass!234",
            role=User.Role.ADMIN,
            email_verified_at=timezone.now(),
            is_staff=True,
            is_superuser=True,
        )
        self._authenticate(admin)
        self.assertEqual(self.client.get(self.saved_list_url).status_code, 403)
        self.assertEqual(self.client.post(self.save_url(missing_id)).status_code, 403)
        self.assertEqual(self.client.delete(self.save_url(missing_id)).status_code, 403)
