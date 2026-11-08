from django.urls import path

from profiles.views import (
    DisciplineListView,
    SkillListView,
    StudentMeView,
    StudentOnboardingCompleteView,
    StudentPhotoView,
    StudentResumeView,
)
from verification.views import StudentVerificationView

urlpatterns = [
    path("disciplines/", DisciplineListView.as_view(), name="discipline-list"),
    path("skills/", SkillListView.as_view(), name="skill-list"),
    path(
        "onboarding/students/complete/",
        StudentOnboardingCompleteView.as_view(),
        name="student-onboarding-complete",
    ),
    path("students/me/", StudentMeView.as_view(), name="student-me"),
    path("students/me/photo/", StudentPhotoView.as_view(), name="student-photo"),
    path("students/me/resume/", StudentResumeView.as_view(), name="student-resume"),
    path(
        "students/me/verification/",
        StudentVerificationView.as_view(),
        name="student-verification-submit",
    ),
]
