from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied
from rest_framework.views import APIView

from accounts.permissions import IsAdminAccount
from accounts.serializers import DetailSerializer
from applications.models import Application
from common.models import Document


@extend_schema(
    tags=["documents"],
    summary="Download an authorized document",
    description=(
        "Streams a document to its owner or an administrator. Private "
        "resumes are also available to a company that received the resume "
        "with an application. Content-Disposition uses the uploaded file name."
    ),
    responses={
        200: {"type": "string", "format": "binary"},
        401: DetailSerializer,
        403: DetailSerializer,
        404: DetailSerializer,
    },
)
class DocumentDownloadView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        document = get_object_or_404(Document, pk=pk)
        user = request.user

        is_owner = document.owner_id == user.id
        is_admin = IsAdminAccount().has_permission(request, None)
        if not is_owner and not is_admin:
            company = getattr(user, "company", None)
            has_company_access = (
                company is not None
                and company.deleted_at is None
                and Application.objects.filter(
                    resume=document,
                    placement__company=company,
                ).exists()
            )
            if not has_company_access:
                raise PermissionDenied("This document is not available to you.")

        if not document.file or not document.file.storage.exists(document.file.name):
            raise Http404("Document file not found.")

        safe_name = (
            document.original_name
            or document.file.name.rsplit("/", 1)[-1]
            or "document"
        )
        response = FileResponse(
            document.file.open("rb"),
            as_attachment=True,
            filename=safe_name,
        )
        response["Content-Type"] = (
            document.mime_type or "application/octet-stream"
        )
        return response
