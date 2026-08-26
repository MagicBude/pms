"""生成路径安全、内容可校验且结果确定的订单图纸 ZIP。"""

import hashlib
import json
import re
from dataclasses import dataclass
from io import BytesIO
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


@dataclass(frozen=True, slots=True)
class DrawingPackageFile:
    drawing_id: UUID
    attachment_id: UUID
    material_id: UUID
    material_code: str
    material_name: str
    document_format: str
    drawing_version: int
    revision_label: str
    original_filename: str
    size_bytes: int
    sha256_hex: str


@dataclass(frozen=True, slots=True)
class MissingDrawingMaterial:
    material_id: UUID
    material_code: str
    material_name: str


@dataclass(frozen=True, slots=True)
class DrawingPackageData:
    order_id: UUID
    order_number: str
    order_status: str
    package_version: int
    files: tuple[DrawingPackageFile, ...]
    missing: tuple[MissingDrawingMaterial, ...]


@dataclass(frozen=True, slots=True)
class RenderedDrawingPackage:
    content: bytes
    archive_paths: dict[UUID, str]


def render_drawing_package(
    data: DrawingPackageData, contents: dict[UUID, bytes]
) -> RenderedDrawingPackage:
    """复核附件摘要后写 ZIP；任何不一致都会阻断整个包。"""
    archive_paths: dict[UUID, str] = {}
    manifest_files: list[dict[str, object]] = []
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for item in sorted(
            data.files,
            key=lambda row: (row.material_code, row.document_format, str(row.drawing_id)),
        ):
            content = contents[item.attachment_id]
            digest = hashlib.sha256(content).hexdigest()
            if len(content) != item.size_bytes or digest != item.sha256_hex:
                raise ValueError("图纸附件大小或 SHA-256 与元数据不一致。")
            extension = item.document_format.lower()
            safe_code = _safe_segment(item.material_code)
            path = (
                f"drawings/{safe_code}/{safe_code}-{item.document_format}-"
                f"V{item.drawing_version}-{str(item.drawing_id)[:8]}.{extension}"
            )
            archive_paths[item.drawing_id] = path
            _write_entry(archive, path, content)
            manifest_files.append(
                {
                    "drawing_id": str(item.drawing_id),
                    "material_code": item.material_code,
                    "material_name": item.material_name,
                    "format": item.document_format,
                    "drawing_version": item.drawing_version,
                    "revision_label": item.revision_label,
                    "original_filename": item.original_filename,
                    "archive_path": path,
                    "size_bytes": item.size_bytes,
                    "sha256": item.sha256_hex,
                }
            )
        manifest = {
            "schema": "pms-order-drawing-package-v1",
            "order_id": str(data.order_id),
            "order_number": data.order_number,
            "package_version": data.package_version,
            "included_file_count": len(manifest_files),
            "missing_material_count": len(data.missing),
            "files": manifest_files,
            "missing_materials": [
                {
                    "material_id": str(item.material_id),
                    "material_code": item.material_code,
                    "material_name": item.material_name,
                }
                for item in sorted(data.missing, key=lambda row: row.material_code)
            ],
        }
        _write_entry(
            archive,
            "manifest.json",
            (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(),
        )
    return RenderedDrawingPackage(content=output.getvalue(), archive_paths=archive_paths)


def _safe_segment(value: str) -> str:
    """只保留跨平台安全字符，UUID 后缀负责最终消歧。"""
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", value).strip("._")
    return cleaned[:80] or "material"


def _write_entry(archive: ZipFile, path: str, content: bytes) -> None:
    """固定时间和权限，避免同一事实因运行电脑不同产生无意义差异。"""
    info = ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    archive.writestr(info, content)
