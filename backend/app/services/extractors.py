"""文档抽取适配器。

首期只实现本地文字层抽取；扫描 PDF 会返回空文本，调用方可以在不改工作流
的情况下注册 OCRExtractor 接入本地 OCR 或云端 OCR。
"""
from __future__ import annotations

import io
import zipfile
from typing import Protocol
from xml.etree import ElementTree as ET


class OcrExtractor(Protocol):
    def extract(self, raw: bytes) -> str:
        """从扫描文档提取文本。"""


def extract_pdf_text(raw: bytes) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(raw))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:  # noqa: BLE001 - 文档解析失败交给调用方给出用户提示
        return ""


def extract_docx_text(raw: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        return "\n".join(
            "".join((node.text or "") for node in paragraph.findall(".//w:t", ns)).strip()
            for paragraph in root.findall(".//w:p", ns)
        )
    except Exception:  # noqa: BLE001
        return ""


def extract_document(raw: bytes, suffix: str) -> str:
    """按文件后缀抽取文字层；OCR 作为后续可插拔实现。"""
    suffix = suffix.lower()
    if suffix == ".pdf":
        return extract_pdf_text(raw)
    if suffix == ".docx":
        return extract_docx_text(raw)
    if suffix in (".txt", ".md", ""):
        return raw.decode("utf-8", errors="ignore")
    raise ValueError("仅支持 .txt / .md / .pdf / .docx 素材")
