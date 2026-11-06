"""PDF text extraction with citable locations (pipeline step 1).

The same invariant as the Markdown segmenter holds: a segment's text is exactly what is stored,
so a quote checked against it stays checkable. Lines of one paragraph are joined here, because
a PDF breaks lines for layout, and "10 000\\nMAD" must still read as one amount.
"""

import hashlib
import re
from collections import Counter
from dataclasses import dataclass

import pymupdf

from app.modules.extraction.segmenter import MAX_SEGMENT_CHARS

# Below this many characters per page on average, the file is treated as a scan (FR-001).
_MIN_CHARS_PER_PAGE = 20
_NUMBERED_TITLE_RE = re.compile(r"^\d+(?:\.\d+)*\.?\s+\S")
_TITLE_MAX_CHARS = 120
_HEADING_SIZE_RATIO = 1.15


class PdfRejected(ValueError):
    pass


@dataclass(frozen=True)
class PdfSegment:
    page: int
    section_path: str | None
    start_char: int
    end_char: int
    text: str
    sha256: str


@dataclass(frozen=True)
class PdfExtraction:
    page_count: int
    segments: list[PdfSegment]
    needs_ocr: bool


@dataclass(frozen=True)
class _Block:
    page: int
    text: str
    size: float
    bold: bool


def _join_lines(lines: list[str]) -> str:
    """One paragraph per block: a word cut at a line end is rejoined, other lines get a space."""
    text = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if text.endswith("-") and line[0].islower():
            text = text[:-1] + line
        else:
            text = f"{text} {line}" if text else line
    return text


def _read_blocks(document: pymupdf.Document) -> list[_Block]:
    blocks: list[_Block] = []
    for number, page in enumerate(document, start=1):
        for block in page.get_text("dict", sort=True)["blocks"]:
            if block.get("type") != 0:
                continue  # images have no text to cite
            lines, sizes, bold = [], [], True
            for line in block["lines"]:
                spans = [span for span in line["spans"] if span["text"].strip()]
                if not spans:
                    continue
                lines.append("".join(span["text"] for span in line["spans"]))
                sizes += [span["size"] for span in spans]
                # Bit 4 of a span's flags marks bold text.
                bold = bold and all(span["flags"] & 16 for span in spans)
            text = _join_lines(lines)
            if text:
                blocks.append(_Block(number, text, max(sizes), bold))
    return blocks


def _heading_level(block: _Block, body_size: float) -> int | None:
    """No outline to rely on in most PDFs, so headings are recognised by how they look."""
    if len(block.text) > _TITLE_MAX_CHARS or block.text.endswith((".", ";", ":", ",")):
        return None
    numbered = _NUMBERED_TITLE_RE.match(block.text)
    looks_like_title = block.size >= body_size * _HEADING_SIZE_RATIO or block.bold
    if numbered and looks_like_title:
        return block.text.split()[0].rstrip(".").count(".") + 1
    if block.size >= body_size * _HEADING_SIZE_RATIO:
        return 1
    return None


def _segment(blocks: list[_Block]) -> list[PdfSegment]:
    if not blocks:
        return []
    # Weighted by length: on a short page, titles are as many blocks as paragraphs.
    body_size = Counter(
        {
            size: sum(len(b.text) for b in blocks if b.size == size)
            for size in {b.size for b in blocks}
        }
    ).most_common(1)[0][0]
    segments: list[PdfSegment] = []
    stack: list[tuple[int, str]] = []
    offset = 0
    pending: list[str] = []
    pending_page = blocks[0].page
    pending_section: str | None = None

    def flush() -> None:
        nonlocal offset, pending
        if not pending:
            return
        text = "\n\n".join(pending)
        digest = hashlib.sha256(text.encode()).hexdigest()
        segments.append(
            PdfSegment(pending_page, pending_section, offset, offset + len(text), text, digest)
        )
        offset += len(text) + 2
        pending = []

    for block in blocks:
        level = _heading_level(block, body_size)
        if level is not None:
            flush()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, block.text))
            continue
        section = " > ".join(title for _, title in stack) or None
        too_long = sum(len(part) + 2 for part in pending) + len(block.text) > MAX_SEGMENT_CHARS
        # A segment never spans two pages, so its page number is always exact.
        if pending and (block.page != pending_page or section != pending_section or too_long):
            flush()
        if not pending:
            pending_page, pending_section = block.page, section
        pending.append(block.text)
    flush()
    return segments


def extract_pdf(data: bytes, max_pages: int) -> PdfExtraction:
    """Raises PdfRejected for a file that cannot be read. A scan is not an error: it comes back
    with needs_ocr set, and is stored but excluded from analysis (FR-001)."""
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # the library raises several unrelated types for damaged files
        raise PdfRejected("Ce PDF est illisible ou endommagé.") from exc
    with document:
        if document.needs_pass:
            raise PdfRejected("Ce PDF est protégé par un mot de passe.")
        if document.page_count > max_pages:
            raise PdfRejected(f"Ce PDF dépasse la limite de {max_pages} pages.")
        page_count = document.page_count
        blocks = _read_blocks(document)
    total_chars = sum(len(block.text) for block in blocks)
    if page_count == 0 or total_chars < _MIN_CHARS_PER_PAGE * page_count:
        return PdfExtraction(page_count, [], needs_ocr=True)
    return PdfExtraction(page_count, _segment(blocks), needs_ocr=False)
