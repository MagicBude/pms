"""物料图纸页面上传、历史展示和下载的 HTTP 验收。"""

from pathlib import Path

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from pms.master_data.infrastructure.django.models import MaterialDrawing
from pms.tenancy.infrastructure.django.models import Membership
from pms.web.context import SESSION_MEMBERSHIP_KEY
from tests.integration.business.test_procurement_pricing import submitted_line


@pytest.mark.django_db
@pytest.mark.acceptance
def test_material_page_uploads_and_downloads_pdf_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, client: Client
) -> None:
    """AC-P3A-026/029：认证页面上传后展示哈希并受控下载。"""
    monkeypatch.setattr(
        settings, "ATTACHMENT_STORAGE_ROOT", tmp_path / "web-drawings", raising=False
    )
    context, line, _ = submitted_line(monkeypatch, tmp_path)
    membership = Membership.objects.select_related("user").get(id=context.membership_id)
    client.force_login(membership.user)
    session = client.session
    session[SESSION_MEMBERSHIP_KEY] = str(membership.id)
    session.save()
    page = f"/materials/{line.material_id}/drawings/"
    response = client.post(
        f"{page}new/",
        data={
            "file": SimpleUploadedFile(
                "虚构网页图纸.pdf", b"%PDF-1.4\nsynthetic-web", content_type="application/pdf"
            ),
            "revision_label": "A",
            "note": "虚构页面测试",
        },
        follow=True,
    )
    assert response.status_code == 200
    content = response.content.decode()
    assert "PDF V1" in content
    assert "当前" in content
    drawing = MaterialDrawing.objects.get(material_id=line.material_id)
    download = client.get(f"/material-drawings/{drawing.attachment_id}/download/")
    assert download.status_code == 200
    assert ".pdf" in download["Content-Disposition"]
