"""Safe readers for the server-owned D01-D12 document snapshot.

Readers return source-addressable chunks and never interpret document text as
instructions. Optional format libraries are loaded lazily so a malformed
document produces a sanitized per-document processing error.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from typing import Iterable
import zipfile
import xml.etree.ElementTree as ET

from .markdown import chunk_markdown


class DocumentProcessingError(ValueError):
    def __init__(self, doc_id: str, reason: str):
        self.doc_id = doc_id
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class ChunkRecord:
    chunk_id: str
    doc_id: str
    title: str
    doc_type: str
    version: str
    content_sha256: str
    section: str | None
    text: str
    page: int | None
    bbox: list[float] | None
    source_uri: str


def _source_uri(doc: dict, suffix: str) -> str:
    return f"data/knowledge/{doc['path']}{suffix}"


def _split_text(text: str, *, doc: dict, location: str, page: int | None = None,
                bbox: list[float] | None = None, section: str | None = None,
                max_chars: int = 1200) -> Iterable[ChunkRecord]:
    value = text.strip()
    if not value:
        return
    digest = doc["sha256"]
    for offset in range(0, len(value), max_chars):
        part = value[offset:offset + max_chars].strip()
        if not part:
            continue
        chunk_hash = hashlib.sha256(part.encode("utf-8")).hexdigest()[:12]
        chunk_id = f"{doc['doc_id']}@{digest[:12]}:{location}:{offset}:{chunk_hash}"
        yield ChunkRecord(
            chunk_id=chunk_id,
            doc_id=doc["doc_id"],
            title=doc["title"],
            doc_type=doc["doc_type"],
            version=doc["version"],
            content_sha256=hashlib.sha256(part.encode("utf-8")).hexdigest(),
            section=section,
            text=part,
            page=page,
            bbox=bbox,
            source_uri=_source_uri(doc, location),
        )


def _read_markdown(path: Path, doc: dict, max_chars: int) -> tuple[ChunkRecord, ...]:
    chunks = chunk_markdown(path.read_text(encoding="utf-8"), doc_id=doc["doc_id"],
                            version_hash=doc["sha256"], max_chars=max_chars)
    return tuple(
        ChunkRecord(
            chunk_id=item.chunk_id,
            doc_id=doc["doc_id"],
            title=doc["title"],
            doc_type=doc["doc_type"],
            version=doc["version"],
            content_sha256=hashlib.sha256(item.text.encode("utf-8")).hexdigest(),
            section=item.section,
            text=item.text,
            page=None,
            bbox=None,
            source_uri=_source_uri(doc, f"#L{item.line_start}-L{item.line_end}"),
        )
        for item in chunks
    )


def _read_docx(path: Path, doc: dict, max_chars: int) -> tuple[ChunkRecord, ...]:
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    try:
        with zipfile.ZipFile(path) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
    except (OSError, KeyError, ET.ParseError, zipfile.BadZipFile) as exc:
        raise DocumentProcessingError(doc["doc_id"], "DOCX_PARSE_FAILED") from exc
    records: list[ChunkRecord] = []
    heading = "正文"
    paragraph_number = 0
    for paragraph in root.findall(".//w:p", ns):
        paragraph_number += 1
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", ns)).strip()
        if not text:
            continue
        style = paragraph.find("./w:pPr/w:pStyle", ns)
        style_name = style.get(f"{{{ns['w']}}}val", "") if style is not None else ""
        if style_name.lower().startswith("heading"):
            heading = text
        records.extend(_split_text(
            text, doc=doc, location=f"#P{paragraph_number}", section=heading,
            max_chars=max_chars,
        ))
    if not records:
        raise DocumentProcessingError(doc["doc_id"], "DOCX_EMPTY")
    return tuple(records)


def _read_pdf(path: Path, doc: dict, max_chars: int) -> tuple[ChunkRecord, ...]:
    try:
        from pypdf import PdfReader
        pages = PdfReader(path).pages
    except Exception as exc:
        raise DocumentProcessingError(doc["doc_id"], "PDF_PARSE_FAILED") from exc
    records: list[ChunkRecord] = []
    for number, page in enumerate(pages, 1):
        try:
            text = (page.extract_text() or "").strip()
        except Exception as exc:
            raise DocumentProcessingError(doc["doc_id"], "PDF_PAGE_PARSE_FAILED") from exc
        records.extend(_split_text(
            text, doc=doc, location=f"#P{number}", page=number,
            section=f"第{number}页", max_chars=max_chars,
        ))
    if not records:
        raise DocumentProcessingError(doc["doc_id"], "PDF_EMPTY")
    return tuple(records)


def _read_png(path: Path, doc: dict, max_chars: int) -> tuple[ChunkRecord, ...]:
    try:
        from PIL import Image
        image = Image.open(path)
        image.load()
        width, height = image.size
    except Exception as exc:
        raise DocumentProcessingError(doc["doc_id"], "IMAGE_PARSE_FAILED") from exc
    try:
        from rapidocr_onnxruntime import RapidOCR
        result, _ = RapidOCR()(str(path))
    except Exception as exc:
        raise DocumentProcessingError(doc["doc_id"], "OCR_UNAVAILABLE") from exc
    records: list[ChunkRecord] = []
    for index, item in enumerate(result or [], 1):
        if len(item) < 2 or not item[1]:
            continue
        polygon = item[0]
        xs = [float(point[0]) for point in polygon]
        ys = [float(point[1]) for point in polygon]
        bbox = [max(0.0, min(xs)), max(0.0, min(ys)), min(float(width), max(xs)), min(float(height), max(ys))]
        records.extend(_split_text(
            str(item[1]), doc=doc, location=f"#B{index}", page=1,
            bbox=bbox, section="OCR", max_chars=max_chars,
        ))
    if not records:
        raise DocumentProcessingError(doc["doc_id"], "OCR_EMPTY")
    return tuple(records)


def read_document(root: Path, doc: dict, *, max_chars: int = 1200) -> tuple[ChunkRecord, ...]:
    path = (root / doc["path"]).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise DocumentProcessingError(doc["doc_id"], "PATH_OUTSIDE_ROOT") from exc
    if not path.is_file():
        raise DocumentProcessingError(doc["doc_id"], "FILE_MISSING")
    raw = path.read_bytes()
    if len(raw) != doc["size_bytes"] or hashlib.sha256(raw).hexdigest() != doc["sha256"]:
        raise DocumentProcessingError(doc["doc_id"], "HASH_MISMATCH")
    if doc["doc_type"] == "markdown":
        return _read_markdown(path, doc, max_chars)
    if doc["doc_type"] == "docx":
        return _read_docx(path, doc, max_chars)
    if doc["doc_type"] == "pdf":
        return _read_pdf(path, doc, max_chars)
    if doc["doc_type"] == "png":
        return _read_png(path, doc, max_chars)
    raise DocumentProcessingError(doc["doc_id"], "UNSUPPORTED_DOCUMENT_TYPE")
