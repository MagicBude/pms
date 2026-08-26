"""物料图纸格式识别和不可信文件名清理规则。"""

from enum import StrEnum
from pathlib import Path


class DrawingFormat(StrEnum):
    PDF = "PDF"
    DWG = "DWG"


def detect_drawing_format(*, filename: str, content: bytes) -> DrawingFormat:
    """同时检查扩展名和文件签名，拒绝只改后缀的伪装上传。"""
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf" and content.startswith(b"%PDF-"):
        return DrawingFormat.PDF
    if suffix == ".dwg" and len(content) >= 6 and content[:6].startswith(b"AC10"):
        return DrawingFormat.DWG
    raise ValueError("只接受文件内容与扩展名一致的 PDF 或 DWG 图纸。")
