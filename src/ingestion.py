"""Layer 3: text ingestion, page parsing, heading detection and chunking.

Reads plain-text exports, validates their content, preserves page boundaries
where markers are present, normalises the text, detects headings and splits the
content into searchable passages with traceable page references.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .catalogue import utc_now
from .logging_setup import get_logger

logger = get_logger("ingestion")

PAGE_MARKER_RE = re.compile(r"^\s*=+\s*PAGE\s+(\d+)\s*=+\s*$", re.IGNORECASE)
DOCUMENT_LINE_RE = re.compile(r"^\s*DOCUMENT\s*:\s*(.+?)\s*$", re.IGNORECASE)
MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s+\S")
NUMBERED_HEADING_RE = re.compile(r"^\d+(\.\d+)*[.)]?\s+[A-Z].+$")

MINIMUM_TEXT_LENGTH = 200

# Small connector words allowed to stay lower-case inside a title-case heading.
_CONNECTOR_WORDS = {
    "and", "or", "of", "the", "for", "to", "in", "on", "with", "a", "an", "per", "via",
}
_TERMINAL_PUNCTUATION = (".", ",", ";", ":")


def read_text_file(path: Path, encodings: List[str]) -> Tuple[str, str]:
    """Read a text file, trying each approved encoding in turn."""

    last_error: Optional[Exception] = None
    for encoding in encodings:
        try:
            return path.read_text(encoding=encoding), encoding
        except UnicodeDecodeError as error:
            last_error = error
            logger.warning("Decoding %s with %s failed; trying next encoding", path.name, encoding)
    raise ValueError(f"Unable to decode {path.name} with encodings {encodings}: {last_error}")


def normalise_text(text: str) -> str:
    """Normalise line endings and collapse repeated blank lines."""

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    normalised: List[str] = []
    blank_pending = False
    for line in text.split("\n"):
        stripped = line.rstrip()
        if stripped:
            if blank_pending and normalised:
                normalised.append("")
            blank_pending = False
            normalised.append(stripped)
        else:
            blank_pending = True
    return "\n".join(normalised).strip()


def split_pages(text: str) -> Tuple[List[Dict[str, object]], bool, Optional[str]]:
    """Split text into pages using ``===== PAGE n =====`` markers.

    Returns the pages, whether any marker was found and any declared
    ``DOCUMENT:`` filename from the file header.
    """

    pages: List[Dict[str, object]] = []
    current_number: Optional[int] = None
    current_lines: List[str] = []
    found_marker = False
    declared_document: Optional[str] = None

    def flush() -> None:
        content = "\n".join(current_lines).strip()
        if content or current_number is not None:
            pages.append({"page_number": current_number, "text": content})

    for line in text.split("\n"):
        if declared_document is None:
            match = DOCUMENT_LINE_RE.match(line)
            if match:
                declared_document = match.group(1)
                continue
        marker = PAGE_MARKER_RE.match(line)
        if marker:
            flush()
            current_number = int(marker.group(1))
            current_lines = []
            found_marker = True
        else:
            current_lines.append(line)

    flush()
    return pages, found_marker, declared_document


def _looks_title_case(line: str) -> bool:
    """Return True when a short line is entirely title case."""

    if line.rstrip().endswith(_TERMINAL_PUNCTUATION):
        return False
    words = [word for word in line.split() if any(char.isalpha() for char in word)]
    if not words or len(words) > 8:
        return False
    for word in words:
        if word.lower() in _CONNECTOR_WORDS:
            continue
        if not word[0].isupper():
            return False
    return True


def is_heading_line(line: str) -> bool:
    """Heuristically decide whether a line is a heading.

    Heading detection is an approximation and is recorded as such in the
    generated documentation.
    """

    if not line or len(line) > 90:
        return False
    if MARKDOWN_HEADING_RE.match(line):
        return True
    if NUMBERED_HEADING_RE.match(line) and len(line.split()) <= 12:
        return True
    letters = [char for char in line if char.isalpha()]
    if letters and line.upper() == line and len(line.split()) <= 10 and len(letters) >= 3:
        return True
    return _looks_title_case(line)


def split_segments(page_text: str) -> List[Tuple[str, bool]]:
    """Split page text into paragraph and heading segments."""

    segments: List[Tuple[str, bool]] = []
    paragraph_lines: List[str] = []

    def flush_paragraph() -> None:
        if paragraph_lines:
            segments.append(("\n".join(paragraph_lines).strip(), False))
            paragraph_lines.clear()

    for line in page_text.split("\n"):
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            continue
        if is_heading_line(stripped):
            flush_paragraph()
            segments.append((stripped, True))
        else:
            paragraph_lines.append(line.rstrip())

    flush_paragraph()
    return segments


def _overlap_tail(content: str, overlap: int) -> str:
    if overlap <= 0 or not content:
        return ""
    tail = content[-overlap:]
    if len(content) > overlap:
        space = tail.find(" ")
        if space != -1:
            tail = tail[space + 1:]
    return tail.strip()


def _split_long_segment(text: str, limit: int) -> List[str]:
    """Break an oversized paragraph into pieces at word boundaries."""

    if len(text) <= limit:
        return [text]
    pieces: List[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = window.rfind(" ")
        if cut <= 0:
            cut = limit
        pieces.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        pieces.append(remaining)
    return pieces


def chunk_pages(pages: List[Dict[str, object]], chunk_size: int, overlap: int) -> List[Dict[str, object]]:
    """Split pages into overlapping passages up to ``chunk_size`` characters.

    Chunks never split inside a word and retain the page range and the active
    section heading.
    """

    segments: List[Tuple[Optional[int], str, bool]] = []
    for page in pages:
        for text, is_heading in split_segments(str(page["text"])):
            if is_heading:
                segments.append((page["page_number"], text, True))
            else:
                for piece in _split_long_segment(text, chunk_size):
                    segments.append((page["page_number"], piece, False))

    chunks: List[Dict[str, object]] = []
    buffer: List[str] = []
    buffer_length = 0
    start_page: Optional[int] = None
    end_page: Optional[int] = None
    heading: Optional[str] = None
    chunk_heading: Optional[str] = None

    def flush() -> None:
        nonlocal buffer, buffer_length, start_page, end_page, chunk_heading
        content = "\n\n".join(buffer).strip()
        if content:
            chunks.append(
                {
                    "page_start": start_page,
                    "page_end": end_page,
                    "section_heading": chunk_heading,
                    "content": content,
                }
            )
        tail = _overlap_tail(content, overlap)
        buffer = [tail] if tail else []
        buffer_length = len(tail) + 2 if tail else 0
        start_page = end_page

    for page_number, text, is_heading in segments:
        if is_heading:
            heading = text.lstrip("#").strip()
        segment_length = len(text) + 2
        if buffer and buffer_length + segment_length > chunk_size:
            flush()
        if start_page is None:
            start_page = page_number
            chunk_heading = heading
        buffer.append(text)
        buffer_length += segment_length
        end_page = page_number

    final_content = "\n\n".join(buffer).strip()
    if final_content:
        chunks.append(
            {
                "page_start": start_page,
                "page_end": end_page,
                "section_heading": chunk_heading,
                "content": final_content,
            }
        )
    return chunks


def _clear_document_content(conn, document_id: int) -> None:
    conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
    conn.execute("DELETE FROM pages WHERE document_id = ?", (document_id,))
    conn.execute(
        "DELETE FROM evidence WHERE chunk_id NOT IN (SELECT chunk_id FROM chunks)"
    )


def _set_status(conn, document_id: int, status: str) -> None:
    conn.execute(
        "UPDATE documents SET processing_status = ?, updated_at = ? WHERE document_id = ?",
        (status, utc_now(), document_id),
    )


def _store_pages(conn, document_id: int, pages: List[Dict[str, object]]) -> int:
    count = 0
    for page in pages:
        conn.execute(
            "INSERT INTO pages (document_id, page_number, page_text, extraction_status) "
            "VALUES (?, ?, ?, ?)",
            (document_id, page["page_number"], page["text"], "PARSED"),
        )
        count += 1
    return count


def _store_chunks(conn, document_id: int, chunks: List[Dict[str, object]]) -> Tuple[int, int]:
    count = 0
    duplicates = 0
    seen: set = set()
    for chunk in chunks:
        content = str(chunk["content"])
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if content_hash in seen:
            duplicates += 1
            continue
        seen.add(content_hash)
        try:
            conn.execute(
                """
                INSERT INTO chunks
                    (document_id, page_start, page_end, section_heading, content, content_hash)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (document_id, chunk["page_start"], chunk["page_end"],
                 chunk["section_heading"], content, content_hash),
            )
            count += 1
        except sqlite3.IntegrityError:
            duplicates += 1
    return count, duplicates


def _start_run(conn) -> int:
    cursor = conn.execute("INSERT INTO ingestion_runs (started_at) VALUES (?)", (utc_now(),))
    return int(cursor.lastrowid)


def _finish_run(conn, run_id: int, stats: Dict[str, int]) -> None:
    conn.execute(
        """
        UPDATE ingestion_runs
           SET completed_at = ?, files_discovered = ?, files_ingested = ?,
               files_pending = ?, files_failed = ?
         WHERE run_id = ?
        """,
        (utc_now(), stats.get("documents_processed", 0), stats.get("documents_ingested", 0),
         stats.get("documents_review", 0), stats.get("documents_failed", 0), run_id),
    )


def ingest_documents(conn, config) -> Dict[str, int]:
    """Ingest every document that has an available text source."""

    stats = {
        "documents_processed": 0,
        "documents_ingested": 0,
        "documents_review": 0,
        "documents_failed": 0,
        "pages": 0,
        "chunks": 0,
        "duplicate_chunks": 0,
    }

    run_id = _start_run(conn)
    rows = conn.execute(
        """
        SELECT document_id, filename, text_source_path
          FROM documents
         WHERE text_source_path IS NOT NULL
         ORDER BY technology, filename
        """
    ).fetchall()

    for row in rows:
        stats["documents_processed"] += 1
        document_id = int(row["document_id"])
        text_path = Path(row["text_source_path"])

        if not text_path.exists():
            logger.error("Text source missing for %s: %s", row["filename"], text_path)
            _set_status(conn, document_id, "FAILED")
            stats["documents_failed"] += 1
            continue

        try:
            raw, encoding = read_text_file(text_path, config.encodings)
        except (OSError, ValueError) as error:
            logger.error("Unable to read %s: %s", text_path, error)
            _set_status(conn, document_id, "FAILED")
            stats["documents_failed"] += 1
            continue

        text = normalise_text(raw)
        if len(text) < MINIMUM_TEXT_LENGTH:
            logger.warning("Text file is empty or too short, marking NEEDS_REVIEW: %s", text_path.name)
            _set_status(conn, document_id, "NEEDS_REVIEW")
            stats["documents_review"] += 1
            continue

        pages, has_markers, declared = split_pages(text)
        if not has_markers and config.require_page_markers:
            logger.warning(
                "No page markers in %s; page traceability is limited", text_path.name
            )
        if declared:
            logger.debug("Declared document in %s: %s", text_path.name, declared)

        chunks = chunk_pages(pages, config.chunk_size, config.chunk_overlap)

        _clear_document_content(conn, document_id)
        stats["pages"] += _store_pages(conn, document_id, pages)
        chunk_count, duplicate_count = _store_chunks(conn, document_id, chunks)
        stats["chunks"] += chunk_count
        stats["duplicate_chunks"] += duplicate_count

        status = "NEEDS_REVIEW" if (not has_markers and config.require_page_markers) else "INGESTED"
        _set_status(conn, document_id, status)
        if status == "INGESTED":
            stats["documents_ingested"] += 1
        else:
            stats["documents_review"] += 1

        logger.info(
            "Ingested %s [%s]: %d page(s), %d chunk(s)",
            row["filename"], encoding, stats["pages"], chunk_count,
        )

    _finish_run(conn, run_id, stats)
    conn.commit()
    logger.info("Ingestion complete: %s", stats)
    return stats
