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
    context_members: tuple[tuple[str, tuple[float, ...]], ...] = ()


def _source_uri(doc: dict, suffix: str) -> str:
    return f"data/knowledge/{doc['path']}{suffix}"


def _split_text(text: str, *, doc: dict, location: str, page: int | None = None,
                bbox: list[float] | None = None, section: str | None = None,
                max_chars: int = 1200, context_members=()) -> Iterable[ChunkRecord]:
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
            context_members=context_members,
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
    # Preserve each original paragraph; add source-addressable table context.
    # Flattening cells independently loses the association between a label and
    # its threshold/frequency. These chunks contain only actual table text.
    for number, table in enumerate(root.findall(".//w:tbl", ns), 1):
        rows = []
        for row in table.findall("./w:tr", ns):
            cells = [" ".join(n.text or "" for n in cell.findall(".//w:t", ns)) for cell in row.findall("./w:tc", ns)]
            rows.append(" | ".join(cells))
        records.extend(_split_text("\n".join(rows), doc=doc, location=f"#T{number}", section="表格", max_chars=max_chars))
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
    # Bound neighbours by actual geometry, not the OCR enumeration order.
    # Keep each original block ID/bbox; union bbox is only the preview region.
    originals = list(records)
    for index, item in enumerate(originals):
        neighbours = spatial_neighbours(item, originals)
        if len(neighbours) < 2:
            continue
        boxes = [n.bbox for n in neighbours]
        bbox = [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]
        locations = [n.source_uri.rsplit("#", 1)[1] for n in neighbours]
        members = tuple((n.chunk_id, tuple(n.bbox)) for n in neighbours)
        records.extend(_split_text("\n".join(n.text for n in neighbours), doc=doc,
            location="#" + "+".join(locations), page=1, bbox=bbox, section="OCR邻接", max_chars=max_chars, context_members=members))
    if not records:
        raise DocumentProcessingError(doc["doc_id"], "OCR_EMPTY")
    return tuple(records)


def spatial_neighbours(seed, records):
    """At most two nearby same-column/baseline blocks; no distant joining."""
    box = seed.bbox
    if not box:
        return [seed]
    height = max(1.0, box[3] - box[1])
    ranked = []
    for candidate in records:
        if candidate.chunk_id == seed.chunk_id or candidate.page != seed.page or not candidate.bbox:
            continue
        other = candidate.bbox
        overlap = max(0, min(box[2], other[2]) - max(box[0], other[0]))
        ratio = overlap / max(1.0, min(box[2] - box[0], other[2] - other[0]))
        gap = other[1] - box[3]
        baseline = abs((other[1] + other[3]) / 2 - (box[1] + box[3]) / 2)
        horizontal = max(0, other[0] - box[2], box[0] - other[2])
        if 0 <= gap <= height * 2.5 and ratio >= 0.5 or baseline <= height * 0.6 and horizontal <= height * 2:
            ranked.append((max(0, gap) + baseline + horizontal, candidate))
    ranked.sort(key=lambda p: (p[0], p[1].chunk_id))
    return [seed, *(c for _, c in ranked[:2])]


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
