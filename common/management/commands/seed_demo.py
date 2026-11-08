"""
Seed enough demo content for a stakeholder walkthrough: one verified demo
organization with placements (paid and unpaid), two verified demo students,
and a couple of applications in different states.

Idempotent: a second run does nothing. Run the management command once after
migrations, locally or on the deployed instance:

    python manage.py seed_demo
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import User
from applications.models import Application
from companies.models import Company
from placements.models import Placement
from profiles.models import Discipline, Skill, StudentProfile


class Command(BaseCommand):
    help = "Create the demo organization, students, placements, and applications."

    def handle(self, *args, **options):
        if User.objects.filter(email="hello@novalabs.example").exists():
            self.stdout.write(self.style.WARNING("Demo data already present — nothing seeded."))
            return

        now = timezone.now()

        # --- Organization -------------------------------------------------
        company_user = User.objects.create_user(
            email="hello@novalabs.example",
            password="DemoComp1",
            role=User.Role.COMPANY,
            email_verified_at=now,
        )
        company = Company.objects.create(
            owner=company_user,
            name="Nova Labs",
            website="https://novalabs.example",
            industry=Company.Industry.TECHNOLOGY,
            description="We build engineering tools and hire interns from Nigerian universities.",
            country="Nigeria",
            city="Lagos",
            address="12 Marina Road",
            verification_status="Verified",
            risk_level="Low",
        )

        # --- Students -----------------------------------------------------
        computer_science = Discipline.objects.get_or_create(name="Computer Science")[0]
        electrical = Discipline.objects.get_or_create(name="Electrical Engineering")[0]
        skills = [
            Skill.objects.get_or_create(name=name)[0]
            for name in ("Python", "Django", "React", "TypeScript", "Git")
        ]

        def make_student(email, first, last, location, institution, discipline, level):
            user = User.objects.create_user(
                email=email,
                password="DemoStud1",
                role=User.Role.STUDENT,
                email_verified_at=now,
            )
            profile = StudentProfile.objects.create(
                user=user,
                first_name=first,
                last_name=last,
                phone="+234 800 000 0000",
                location=location,
                institution=institution,
                discipline=discipline,
                level=level,
                graduation_year=2027,
                verification_status="Verified",
                risk_level="Low",
            )
            profile.skills.add(*skills)
            return profile

        student_a = make_student(
            "ada.demo@run.edu.ng",
            "Ada",
            "Obi",
            "Yaba, Lagos",
            "Redeemer's University",
            computer_science,
            300,
        )
        student_b = make_student(
            "tunde.demo@unilag.edu.ng",
            "Tunde",
            "Bakare",
            "Akoka, Lagos",
            "University of Lagos",
            electrical,
            400,
        )

        # --- Placements (Nova Labs) ---------------------------------------
        backend = Placement.objects.create(
            company=company,
            created_by=company_user,
            title="Backend Engineering Intern",
            location="Lagos",
            work_style=Placement.WorkStyle.HYBRID,
            duration="6 months",
            discipline=computer_science,
            level=300,
            type=Placement.Type.INTERNSHIP,
            stipend_amount="150000.00",
            description=(
                "Join the platform team building our placement marketplace APIs. "
                "You will ship production code from week one."
            ),
            responsibilities=( 
                "Design and build Django REST endpoints and tests. Work with React teammates. "
                "Participate in code reviews and sprint planning."
            ),
            requirements=(
                "Python and Django basics, Git, REST concepts. At least 300 level standing. "
                "Available for a 6-month hybrid internship in Lagos."
            ),
            status=Placement.Status.ACTIVE,
        )
        frontend = Placement.objects.create(
            company=company,
            created_by=company_user,
            title="Frontend Engineering Intern",
            location="Remote",
            work_style=Placement.WorkStyle.REMOTE,
            duration="3 months",
            discipline=computer_science,
            level=200,
            type=Placement.Type.INTERNSHIP,
            stipend_amount="100000.00",
            description=(
                "Build student-facing interfaces for the Fixth marketplace in React. "
                "Focus on discovery, saved placements, and profile pages."
            ),
            responsibilities=(
                "Implement React components per mockups. Write accessible, responsive UI. "
                "Pair with design on polish."
            ),
            requirements=(
                "React plus HTML/CSS fundamentals, Git, comfortable with async remote work. "
                "200 level and above."
            ),
            status=Placement.Status.ACTIVE,
        )
        siwes = Placement.objects.create(
            company=company,
            created_by=company_user,
            title="Embedded Systems SIWES Placement",
            location="Lagos",
            work_style=Placement.WorkStyle.ON_SITE,
            duration="4 months",
            discipline=electrical,
            level=300,
            type=Placement.Type.SIWES,
            # Unpaid internship: stipend stays null by design.
            description=(
                "Hands-on SIWES placement in our hardware lab: firmware bring-up, "
                "schematics review, and bench testing under a senior engineer."
            ),
            responsibilities=(
                "Write and flash firmware for test rigs. Log bench measurements. "
                "Assist with PCB bring-up."
            ),
            requirements=(
                "C basics, digital electronics coursework, on-site availability in Lagos. "
                "300 level electrical engineering students."
            ),
            status=Placement.Status.ACTIVE,
        )
        draft = Placement.objects.create(
            company=company,
            created_by=company_user,
            title="QA Automation Intern",
            location="Lagos",
            work_style=Placement.WorkStyle.HYBRID,
            duration="3 months",
            discipline=computer_science,
            level=300,
            type=Placement.Type.INTERNSHIP,
            stipend_amount="80000.00",
            description="Draft placement kept unpublished for the demo of the editor flow.",
            responsibilities="Write pytest suites and CI checks.",
            requirements="Python plus curiosity about quality engineering.",
            status=Placement.Status.DRAFT,
        )

        # --- Applications --------------------------------------------------
        Application.objects.create(
            placement=backend,
            student=student_a,
            cover_note="I have shipped two Django projects at school and would love to contribute.",
            status=Application.Status.SUBMITTED,
        )
        shortlisted = Application.objects.create(
            placement=backend,
            student=student_b,
            cover_note="Built an ESP32 telemetry logger; comfortable moving into backend Python.",
            status=Application.Status.SHORTLISTED,
        )
        Application.objects.create(
            placement=frontend,
            student=student_a,
            cover_note="React is my home stack — see my coursework portfolio.",
            status=Application.Status.SUBMITTED,
        )
        shortlisted.set_status(Application.Status.SHORTLISTED, actor=company_user)

        self.stdout.write(self.style.SUCCESS(
            "Seed complete:\n"
            "  Organization: hello@novalabs.example / DemoComp1 (Nova Labs)\n"
            "  Students:     ada.demo@run.edu.ng, tunde.demo@unilag.edu.ng / DemoStud1\n"
            f"  Placements:   {backend}, {frontend}, {siwes} active; {draft} draft\n"
            f"  Applications: 3 (submitted/shortlisted)"
        ))
