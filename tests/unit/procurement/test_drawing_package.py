"""订单图纸 ZIP 的确定清单、路径安全和完整性单元测试。"""

import hashlib
import json
import uuid
from io import BytesIO
from zipfile import ZipFile

import pytest

from pms.procurement.infrastructure.drawing_package import (
    DrawingPackageData,
    DrawingPackageFile,
    MissingDrawingMaterial,
    render_drawing_package,
)


def test_manifest_reports_missing_and_archive_path_cannot_traverse() -> None:
    """AC-P3A-031/032/033：部分缺图可见，恶意编码不能成为 ZIP 路径。"""
    content = b"%PDF-1.4\nsynthetic"
    attachment_id = uuid.uuid7()
    drawing_id = uuid.uuid7()
    data = DrawingPackageData(
        order_id=uuid.uuid7(),
        order_number="PO-20260826-001",
        order_status="ISSUED",
        package_version=1,
        files=(
            DrawingPackageFile(
                drawing_id=drawing_id,
                attachment_id=attachment_id,
                material_id=uuid.uuid7(),
                material_code="../../unsafe\\code",
                material_name="虚构图纸",
                document_format="PDF",
                drawing_version=1,
                revision_label="A",
                original_filename="../source.pdf",
                size_bytes=len(content),
                sha256_hex=hashlib.sha256(content).hexdigest(),
            ),
        ),
        missing=(
            MissingDrawingMaterial(
                material_id=uuid.uuid7(),
                material_code="MAT-MISSING",
                material_name="虚构缺图物料",
            ),
        ),
    )
    rendered = render_drawing_package(data, {attachment_id: content})
    with ZipFile(BytesIO(rendered.content)) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
    assert all(".." not in name and "\\" not in name for name in names)
    assert manifest["included_file_count"] == 1
    assert manifest["missing_material_count"] == 1
    assert manifest["missing_materials"][0]["material_code"] == "MAT-MISSING"


def test_digest_mismatch_rejects_entire_package() -> None:
    """清单元数据与实际字节不一致时不产生可交付 ZIP。"""
    attachment_id = uuid.uuid7()
    data = DrawingPackageData(
        order_id=uuid.uuid7(),
        order_number="PO-20260826-002",
        order_status="ISSUED",
        package_version=1,
        files=(
            DrawingPackageFile(
                drawing_id=uuid.uuid7(),
                attachment_id=attachment_id,
                material_id=uuid.uuid7(),
                material_code="MAT-001",
                material_name="虚构物料",
                document_format="PDF",
                drawing_version=1,
                revision_label="",
                original_filename="drawing.pdf",
                size_bytes=3,
                sha256_hex="0" * 64,
            ),
        ),
        missing=(),
    )
    with pytest.raises(ValueError, match="SHA-256"):
        render_drawing_package(data, {attachment_id: b"bad"})
