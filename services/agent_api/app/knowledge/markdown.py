"""Small ATX Markdown reader with stable, versioned, line-addressable chunks.

This is a text reader, not a Markdown renderer, document agent or OCR parser.
"""

from dataclasses import dataclass
import hashlib
import re


HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


@dataclass(frozen=True)
class MarkdownChunk:
    chunk_id: str
    section: str
    text: str
    line_start: int
    line_end: int


def chunk_markdown(text: str, *, doc_id: str, version_hash: str, max_chars: int = 1000) -> tuple[MarkdownChunk, ...]:
    if type(max_chars) is not int or not 100 <= max_chars <= 10000:
        raise ValueError("invalid chunk length")
    if not text.strip() or "\x00" in text:
        raise ValueError("empty or invalid Markdown")
    if not re.fullmatch(r"[a-f0-9]{64}", version_hash) or not re.fullmatch(r"[A-Za-z0-9_-]+", doc_id):
        raise ValueError("invalid document identity")
    sections, stack, lines = [], [], []
    fence = None

    def flush_section():
        nonlocal lines
        if lines and any(value.strip() and not HEADING.match(value) for _, value in lines):
            sections.append((" / ".join(title for _, title in stack) or "正文", lines))
        lines = []

    for number, line in enumerate(text.splitlines(), 1):
        fence_match = FENCE.match(line)
        heading = HEADING.match(line) if fence is None else None
        if heading:
            flush_section()
            level = len(heading.group(1))
            stack = [(n, title) for n, title in stack if n < level]
            stack.append((level, heading.group(2).strip()))
        lines.append((number, line))
        if fence_match:
            marker = fence_match.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
    flush_section()

    chunks = []
    for section, section_lines in sections:
        # Blank-line blocks preserve ordinary paragraphs and Markdown tables.
        blocks, block = [], []
        block_fence = None
        for number, line in section_lines:
            match = FENCE.match(line)
            if not line.strip() and block_fence is None:
                if block:
                    blocks.append(block)
                    block = []
                continue
            block.append((number, line))
            if match:
                marker = match.group(1)
                if block_fence is None:
                    block_fence = marker
                elif marker[0] == block_fence[0] and len(marker) >= len(block_fence):
                    block_fence = None
        if block:
            blocks.append(block)

        def emit(items):
            value = "\n".join(line for _, line in items).strip()
            if value:
                digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
                start, end = items[0][0], items[-1][0]
                # Repeated equal pieces within one long source line need a
                # stable ordinal as well as line range and content digest.
                part = len(chunks) + 1
                chunks.append(MarkdownChunk(f"{doc_id}@{version_hash[:12]}:L{start}-L{end}:P{part}:{digest}", section, value, start, end))

        pending = []
        for block in blocks:
            length = len("\n".join(line for _, line in block))
            if length > max_chars:
                emit(pending)
                pending = []
                # Oversized blocks alone may split at lines/characters. Their
                # source line ranges remain truthful; text hash disambiguates.
                for number, line in block:
                    for offset in range(0, max(1, len(line)), max_chars):
                        piece = line[offset:offset + max_chars]
                        if pending and len("\n".join(x for _, x in pending)) + len(piece) + 1 > max_chars:
                            emit(pending)
                            pending = []
                        pending.append((number, piece))
                emit(pending)
                pending = []
            else:
                if pending and len("\n".join(x for _, x in pending)) + length + 1 > max_chars:
                    emit(pending)
                    pending = []
                pending.extend(block)
        emit(pending)
    if not chunks or len({chunk.chunk_id for chunk in chunks}) != len(chunks):
        raise ValueError("no content or duplicate chunk identities")
    return tuple(chunks)
