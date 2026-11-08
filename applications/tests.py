from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from applications.models import Application, ApplicationEvent
from common.models import Document
from companies.models import Company
from placements.models import Placement
from profiles.models import Discipline, StudentProfile


class CompanyApplicationAPITests(APITestCase):
    list_url = "/api/v1/company/applications/"

    def setUp(self):
        self.discipline = Discipline.objects.create(name="Computer Engineering")

        self.company_user, self.company = self._company(
            "Nova Labs",
            "talent@novalabs.example",
        )
        self.other_company_user, self.other_company = self._company(
            "Other Labs",
            "talent@otherlabs.example",
        )

        self.placement = self._placement(
            self.company,
            self.company_user,
            "Backend Engineering Intern",
        )
        self.other_placement = self._placement(
            self.other_company,
            self.other_company_user,
            "Frontend Engineering Intern",
        )

        self.student_user, self.student = self._student(
            "ada@university.edu.ng",
            "Ada",
            "Lovelace",
        )
        self.other_student_user, self.other_student = self._student(
            "grace@university.edu.ng",
            "Grace",
            "Hopper",
        )
        self.third_student_user, self.third_student = self._student(
            "alan@university.edu.ng",
            "Alan",
            "Turing",
        )

        self.application = Application.objects.create(
            placement=self.placement,
            student=self.student,
            status=Application.Status.SUBMITTED,
        )
        self.shortlisted_application = Application.objects.create(
            placement=self.placement,
            student=self.other_student,
            status=Application.Status.SHORTLISTED,
        )
        self.other_application = Application.objects.create(
            placement=self.other_placement,
            student=self.third_student,
            status=Application.Status.SUBMITTED,
        )

        self.detail_url = f"{self.list_url}{self.application.id}/"
        self.other_detail_url = f"{self.list_url}{self.other_application.id}/"
        self.contact_url = f"{self.detail_url}contact/"
        self.other_contact_url = f"{self.other_detail_url}contact/"
        self.applicants_url = (
            f"/api/v1/company/placements/{self.placement.id}/applicants/"
        )
        self._authenticate(self.company_user)

    def _company(self, name, email):
        user = User.objects.create_user(
            email=email,
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

    def _placement(self, company, owner, title):
        return Placement.objects.create(
            company=company,
            title=title,
            location="Lagos",
            work_style=Placement.WorkStyle.HYBRID,
            duration="6 months",
            discipline=self.discipline,
            level=300,
            status=Placement.Status.ACTIVE,
            created_by=owner,
        )

    def _student(self, email, first_name, last_name):
        user = User.objects.create_user(
            email=email,
            password="StrongPass!234",
            role=User.Role.STUDENT,
            email_verified_at=timezone.now(),
        )
        profile = StudentProfile.objects.create(
            user=user,
            first_name=first_name,
            last_name=last_name,
            location="Lagos",
            institution="University of Lagos",
            discipline=self.discipline,
            level=400,
        )
        return user, profile

    def _authenticate(self, user):
        refresh = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    def test_company_lists_only_its_applications_with_filters(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(
            {item["id"] for item in response.data["results"]},
            {str(self.application.id), str(self.shortlisted_application.id)},
        )

        shortlisted = self.client.get(self.list_url, {"status": "shortlisted"})
        self.assertEqual(shortlisted.status_code, 200, shortlisted.data)
        self.assertEqual(shortlisted.data["count"], 1)
        self.assertEqual(
            shortlisted.data["results"][0]["id"],
            str(self.shortlisted_application.id),
        )

        search = self.client.get(self.list_url, {"q": "ada"})
        self.assertEqual(search.status_code, 200, search.data)
        self.assertEqual(search.data["count"], 1)
        self.assertEqual(search.data["results"][0]["id"], str(self.application.id))

        discipline = self.client.get(
            self.list_url,
            {"discipline": "computer-engineering"},
        )
        self.assertEqual(discipline.status_code, 200, discipline.data)
        self.assertEqual(discipline.data["count"], 2)

        by_placement = self.client.get(
            self.list_url,
            {"placement": str(self.placement.id)},
        )
        self.assertEqual(by_placement.status_code, 200, by_placement.data)
        self.assertEqual(by_placement.data["count"], 2)

        invalid_status = self.client.get(self.list_url, {"status": "Unknown"})
        self.assertEqual(invalid_status.status_code, 400)

        invalid_sort = self.client.get(self.list_url, {"sort": "unsupported"})
        self.assertEqual(invalid_sort.status_code, 400)

    def test_company_can_review_and_update_application_status(self):
        detail = self.client.get(self.detail_url)
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data["student"]["first_name"], "Ada")
        self.assertEqual(detail.data["status"], Application.Status.SUBMITTED)
        self.assertEqual(detail.data["events"], [])

        update = self.client.patch(
            self.detail_url,
            {
                "status": Application.Status.SHORTLISTED,
                "reason": "Strong portfolio.",
            },
            format="json",
        )
        self.assertEqual(update.status_code, 200, update.data)
        self.assertEqual(update.data["status"], Application.Status.SHORTLISTED)
        self.assertEqual(update.data["decided_by_email"], self.company_user.email)
        self.assertEqual(len(update.data["events"]), 1)
        self.assertEqual(
            update.data["events"][0]["to_status"],
            Application.Status.SHORTLISTED,
        )
        self.assertEqual(update.data["events"][0]["reason"], "Strong portfolio.")

        rejected = self.client.patch(
            self.detail_url,
            {
                "status": Application.Status.REJECTED,
                "reason": "Not enough backend experience.",
            },
            format="json",
        )
        self.assertEqual(rejected.status_code, 200, rejected.data)
        self.assertEqual(rejected.data["status"], Application.Status.REJECTED)
        self.assertEqual(
            rejected.data["rejection_reason"],
            "Not enough backend experience.",
        )

        self.application.refresh_from_db()
        self.assertEqual(self.application.status, Application.Status.REJECTED)
        self.assertEqual(self.application.decided_by, self.company_user)
        self.assertEqual(
            self.application.rejection_reason,
            "Not enough backend experience.",
        )
        self.assertEqual(ApplicationEvent.objects.filter(
            application=self.application
        ).count(), 2)

    def test_application_status_transitions_and_ownership(self):
        viewed = self.client.patch(
            self.detail_url,
            {"status": Application.Status.VIEWED},
            format="json",
        )
        self.assertEqual(viewed.status_code, 200, viewed.data)

        same_status = self.client.patch(
            self.detail_url,
            {"status": Application.Status.VIEWED},
            format="json",
        )
        self.assertEqual(same_status.status_code, 409)

        back_to_submitted = self.client.patch(
            self.detail_url,
            {"status": Application.Status.SUBMITTED},
            format="json",
        )
        self.assertEqual(back_to_submitted.status_code, 409)

        invalid_status = self.client.patch(
            self.detail_url,
            {"status": "Unknown"},
            format="json",
        )
        self.assertEqual(invalid_status.status_code, 400)

        other_company = self.client.patch(
            self.other_detail_url,
            {"status": Application.Status.VIEWED},
            format="json",
        )
        self.assertEqual(other_company.status_code, 404)

        write_actions = self.client.post(self.list_url, {}, format="json")
        self.assertEqual(write_actions.status_code, 405)
        delete = self.client.delete(self.detail_url)
        self.assertEqual(delete.status_code, 405)

    def test_company_can_list_own_placement_applicants(self):
        response = self.client.get(self.applicants_url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(
            {item["placement_id"] for item in response.data["results"]},
            {str(self.placement.id)},
        )

        other_company = self.client.get(
            f"/api/v1/company/placements/{self.other_placement.id}/applicants/"
        )
        self.assertEqual(other_company.status_code, 404)

    def test_applicant_contact_is_gated_by_application_status(self):
        self.student.phone = "+2348010000000"
        self.student.save(update_fields=["phone", "updated_at"])

        submitted = self.client.get(self.contact_url)
        self.assertEqual(submitted.status_code, 403, submitted.data)

        self.application.set_status(Application.Status.VIEWED)
        viewed = self.client.get(self.contact_url)
        self.assertEqual(viewed.status_code, 403, viewed.data)

        self.application.set_status(Application.Status.SHORTLISTED)
        shortlisted = self.client.get(self.contact_url)
        self.assertEqual(shortlisted.status_code, 200, shortlisted.data)
        self.assertEqual(
            shortlisted.data,
            {
                "email": "ada@university.edu.ng",
                "phone": "+2348010000000",
            },
        )

        self.application.set_status(Application.Status.ACCEPTED)
        accepted = self.client.get(self.contact_url)
        self.assertEqual(accepted.status_code, 200, accepted.data)
        self.assertEqual(accepted.data["email"], "ada@university.edu.ng")

        self.application.set_status(Application.Status.REJECTED)
        rejected = self.client.get(self.contact_url)
        self.assertEqual(rejected.status_code, 403, rejected.data)

    def test_applicant_contact_prevents_cross_company_access(self):
        other_contact = self.client.get(self.other_contact_url)
        self.assertEqual(other_contact.status_code, 404)

    def test_applicant_contact_requires_verified_company(self):
        self.client.credentials()
        anonymous = self.client.get(self.contact_url)
        self.assertEqual(anonymous.status_code, 401)

        self._authenticate(self.student_user)
        student = self.client.get(self.contact_url)
        self.assertEqual(student.status_code, 403)

    def test_company_application_endpoints_enforce_company_role(self):
        self.client.credentials()
        anonymous = self.client.get(self.list_url)
        self.assertEqual(anonymous.status_code, 401)

        self._authenticate(self.student_user)
        student = self.client.get(self.list_url)
        self.assertEqual(student.status_code, 403)

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
        unverified_response = self.client.get(self.list_url)
        self.assertEqual(unverified_response.status_code, 403)


class StudentApplicationAPITests(APITestCase):
    list_url = "/api/v1/applications/"
    summary_url = "/api/v1/applications/summary/"

    def setUp(self):
        self.discipline = Discipline.objects.create(name="Computer Engineering")

        self.company_user, self.company = self._company(
            "Nova Labs",
            "talent@novalabs.example",
        )
        self.other_company_user, self.other_company = self._company(
            "Other Labs",
            "talent@otherlabs.example",
        )
        self.placement = self._placement("Backend Engineering Intern")
        self.other_placement = self._placement(
            "Other Company Placement",
            company=self.other_company,
            owner=self.other_company_user,
        )

        self.student_user, self.student = self._student(
            "ada@university.edu.ng",
        )
        self.other_student_user, self.other_student = self._student(
            "grace@university.edu.ng"
        )
        self.resume = Document.objects.create(
            owner=self.student_user,
            kind=Document.Kind.RESUME,
            file="documents/resume/ada-resume.pdf",
            original_name="ada-resume.pdf",
            mime_type="application/pdf",
            size=2048,
        )
        self.student.resume = self.resume
        self.student.save(update_fields=["resume", "updated_at"])

        self._authenticate(self.student_user)

    def _company(self, name, email):
        user = User.objects.create_user(
            email=email,
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

    def _placement(
        self,
        title,
        company=None,
        owner=None,
        status=Placement.Status.ACTIVE,
        **overrides,
    ):
        return Placement.objects.create(
            company=company or self.company,
            title=title,
            location="Lagos",
            work_style=Placement.WorkStyle.HYBRID,
            duration="6 months",
            discipline=self.discipline,
            level=300,
            status=status,
            created_by=owner or self.company_user,
            **overrides,
        )

    def _student(self, email, resume=False):
        user = User.objects.create_user(
            email=email,
            password="StrongPass!234",
            role=User.Role.STUDENT,
            email_verified_at=timezone.now(),
        )
        profile = StudentProfile.objects.create(
            user=user,
            first_name=email.split("@")[0].title(),
            last_name="Applicant",
            phone="+2348010000000",
            location="Lagos",
            institution="University of Lagos",
            discipline=self.discipline,
            level=400,
        )
        if resume:
            profile.resume = Document.objects.create(
                owner=user,
                kind=Document.Kind.RESUME,
                file=f"documents/resume/{email}-resume.pdf",
                original_name="resume.pdf",
                mime_type="application/pdf",
                size=2048,
            )
            profile.save(update_fields=["resume", "updated_at"])
        return user, profile

    def _authenticate(self, user):
        refresh = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    def test_student_can_apply_once_and_invalid_attempts_are_rejected(self):
        response = self.client.post(
            self.list_url,
            {"placement_id": str(self.placement.id)},
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(
            response.data["placement_id"],
            str(self.placement.id),
        )
        self.assertEqual(response.data["status"], Application.Status.SUBMITTED)
        self.assertEqual(response.data["resume"]["id"], str(self.resume.id))
        self.assertEqual(len(response.data["events"]), 1)
        self.assertEqual(
            response.data["events"][0]["to_status"],
            Application.Status.SUBMITTED,
        )

        duplicate = self.client.post(
            self.list_url,
            {"placement_id": str(self.placement.id)},
            format="json",
        )
        self.assertEqual(duplicate.status_code, 409, duplicate.data)
        self.assertEqual(Application.objects.count(), 1)

        no_resume_user, no_resume_profile = self._student(
            "no-resume@university.edu.ng"
        )
        self._authenticate(no_resume_user)
        no_resume = self.client.post(
            self.list_url,
            {"placement_id": str(self.placement.id)},
            format="json",
        )
        self.assertEqual(no_resume.status_code, 400, no_resume.data)

        draft = self._placement(
            "Draft Placement",
            status=Placement.Status.DRAFT,
        )
        self._authenticate(self.student_user)
        unavailable = self.client.post(
            self.list_url,
            {"placement_id": str(draft.id)},
            format="json",
        )
        self.assertEqual(unavailable.status_code, 400, unavailable.data)

        missing = self.client.post(
            self.list_url,
            {"placement_id": "00000000-0000-0000-0000-000000000000"},
            format="json",
        )
        self.assertEqual(missing.status_code, 400, missing.data)

    def test_student_retrieves_only_their_applications_and_details(self):
        own = Application.objects.create(
            placement=self.placement,
            student=self.student,
            status=Application.Status.VIEWED,
        )
        other = Application.objects.create(
            placement=self.placement,
            student=self.other_student,
            status=Application.Status.SUBMITTED,
        )

        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], str(own.id))
        self.assertEqual(
            response.data["results"][0]["placement"]["title"],
            self.placement.title,
        )
        self.assertEqual(
            response.data["results"][0]["placement"]["company"],
            self.company.name,
        )

        viewed = self.client.get(self.list_url, {"status": "Viewed"})
        self.assertEqual(viewed.data["count"], 1)
        invalid_status = self.client.get(self.list_url, {"status": "Unknown"})
        self.assertEqual(invalid_status.status_code, 400)

        detail = self.client.get(f"{self.list_url}{own.id}/")
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data["id"], str(own.id))
        self.assertEqual(detail.data["student"]["id"], str(self.student.id))

        other_detail = self.client.get(f"{self.list_url}{other.id}/")
        self.assertEqual(other_detail.status_code, 404)

    def test_student_summary_and_event_history_are_scoped_to_owner(self):
        application = Application.objects.create(
            placement=self.placement,
            student=self.student,
            status=Application.Status.SUBMITTED,
        )
        other_application = Application.objects.create(
            placement=self.placement,
            student=self.other_student,
            status=Application.Status.SUBMITTED,
        )
        application.set_status(Application.Status.VIEWED, actor=self.company_user)
        application.set_status(
            Application.Status.SHORTLISTED,
            actor=self.company_user,
            reason="Strong portfolio.",
        )
        second = self._placement("Second Engineering Intern")
        third = self._placement("Third Engineering Intern")
        Application.objects.create(
            placement=second,
            student=self.student,
            status=Application.Status.ACCEPTED,
        )
        Application.objects.create(
            placement=third,
            student=self.student,
            status=Application.Status.REJECTED,
        )

        summary = self.client.get(self.summary_url)
        self.assertEqual(summary.status_code, 200, summary.data)
        self.assertEqual(
            summary.data,
            {
                "total": 3,
                "pending": 0,
                "under_review": 0,
                "shortlisted": 1,
                "accepted": 1,
                "rejected": 1,
                "withdrawn": 0,
            },
        )

        events = self.client.get(f"{self.list_url}{application.id}/events/")
        self.assertEqual(events.status_code, 200, events.data)
        self.assertEqual(len(events.data), 2)
        self.assertEqual(
            [item["to_status"] for item in events.data],
            [
                Application.Status.VIEWED,
                Application.Status.SHORTLISTED,
            ],
        )
        self.assertEqual(events.data[-1]["reason"], "Strong portfolio.")

        other_events = self.client.get(
            f"{self.list_url}{other_application.id}/events/"
        )
        self.assertEqual(other_events.status_code, 404)

    def test_withdrawal_records_history_and_enforces_access(self):
        application = Application.objects.create(
            placement=self.placement,
            student=self.student,
            status=Application.Status.SHORTLISTED,
        )
        other_application = Application.objects.create(
            placement=self.placement,
            student=self.other_student,
            status=Application.Status.SUBMITTED,
        )

        response = self.client.post(f"{self.list_url}{application.id}/withdraw/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], Application.Status.WITHDRAWN)
        self.assertEqual(
            [
                event.to_status
                for event in ApplicationEvent.objects.filter(
                    application=application
                ).order_by("created_at")
            ],
            [Application.Status.WITHDRAWN],
        )
        self.assertEqual(
            response.data["events"][-1]["reason"],
            "Application withdrawn by student.",
        )

        repeated = self.client.post(f"{self.list_url}{application.id}/withdraw/")
        self.assertEqual(repeated.status_code, 409, repeated.data)

        other = self.client.post(
            f"{self.list_url}{other_application.id}/withdraw/"
        )
        self.assertEqual(other.status_code, 404)
        other_application.refresh_from_db()
        self.assertEqual(
            other_application.status,
            Application.Status.SUBMITTED,
        )

        self._authenticate(self.company_user)
        company_withdraw = self.client.post(
            f"{self.list_url}{application.id}/withdraw/"
        )
        self.assertEqual(company_withdraw.status_code, 403)

        self.client.credentials()
        anonymous = self.client.get(self.list_url)
        self.assertEqual(anonymous.status_code, 401)
