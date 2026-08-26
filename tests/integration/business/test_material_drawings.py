"""Phase 3A 物料图纸内容校验、版本和租户权限验收。"""

from pathlib import Path

import pytest
from django.conf import settings

from pms.audit.infrastructure.django.models import AuditLog
from pms.authorization.application.authorize import PermissionDeniedError
from pms.authorization.domain.permissions import RoleCode
from pms.master_data.application.drawings import UploadDrawingCommand
from pms.master_data.infrastructure.django.models import MaterialDrawing
from pms.platform.business_services import drawing_service
from tests.integration.business.test_bom_workflow import create_member_context
from tests.integration.business.test_procurement_pricing import submitted_line


@pytest.mark.django_db
@pytest.mark.acceptance
def test_pdf_and_dwg_versions_are_independent_and_history_is_preserved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-P3A-026/027/036：格式内递增版本，历史附件和审计均保留。"""
    monkeypatch.setattr(settings, "ATTACHMENT_STORAGE_ROOT", tmp_path / "drawings", raising=False)
    context, line, _ = submitted_line(monkeypatch, tmp_path)
    service = drawing_service()
    pdf_v1 = service.upload(
        context=context,
        command=UploadDrawingCommand(
            material_id=line.material_id,
            filename="虚构零件.pdf",
            content=b"%PDF-1.4\nsynthetic-v1",
            revision_label="A",
        ),
    )
    pdf_v2 = service.upload(
        context=context,
        command=UploadDrawingCommand(
            material_id=line.material_id,
            filename="虚构零件新版.pdf",
            content=b"%PDF-1.4\nsynthetic-v2",
            revision_label="B",
        ),
    )
    dwg_v1 = service.upload(
        context=context,
        command=UploadDrawingCommand(
            material_id=line.material_id,
            filename="虚构零件.dwg",
            content=b"AC1027synthetic-dwg",
            revision_label="B",
        ),
    )
    assert (pdf_v1.version, pdf_v2.version, dwg_v1.version) == (1, 2, 1)
    assert MaterialDrawing.objects.get(id=pdf_v1.id).is_current is False
    assert MaterialDrawing.objects.get(id=pdf_v2.id).is_current is True
    assert MaterialDrawing.objects.get(id=dwg_v1.id).is_current is True
    assert AuditLog.objects.filter(action="material_drawing.uploaded").count() == 3


@pytest.mark.django_db
@pytest.mark.acceptance
def test_disguised_file_and_read_only_role_are_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-P3A-028/029：文件签名、权限和租户边界都在服务端执行。"""
    monkeypatch.setattr(settings, "ATTACHMENT_STORAGE_ROOT", tmp_path / "drawings", raising=False)
    context, line, _ = submitted_line(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="内容与扩展名一致"):
        drawing_service().upload(
            context=context,
            command=UploadDrawingCommand(
                material_id=line.material_id,
                filename="伪装图纸.pdf",
                content=b"not-a-pdf",
            ),
        )
    manager = create_member_context(
        tenant=line.tenant, role=RoleCode.PROJECT_MANAGER, suffix="drawing"
    )
    with pytest.raises(PermissionDeniedError):
        drawing_service().upload(
            context=manager,
            command=UploadDrawingCommand(
                material_id=line.material_id,
                filename="无权图纸.pdf",
                content=b"%PDF-1.4\nsynthetic",
            ),
        )
