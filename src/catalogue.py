"""Layer 1 (source management) and Layer 2 (extraction readiness).

Discovers PDF and text sources, records metadata and SHA-256 fingerprints, links
available text exports to their originating PDF and marks PDFs without text as
``PENDING_EXTRACTION``.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .logging_setup import get_logger

logger = get_logger("catalogue")

KNOWLEDGE_TECHNOLOGIES = ("ACBS", "IMFT", "Airflow", "Solace", "MQ")


def utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def compute_sha256(path: Path, block_size: int = 65536) -> str:
    """Return the SHA-256 fingerprint of a file."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(block_size), b""):
            digest.update(block)
    return digest.hexdigest()


def _iter_files(root: Path, suffixes: Iterable[str]) -> List[Path]:
    """Return files under ``root`` whose extension is in ``suffixes``."""

    if not root.exists():
        return []
    wanted = {suffix.lower() for suffix in suffixes}
    return sorted(
        path for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in wanted
    )


def _technology_for(path: Path, root: Path) -> Optional[str]:
    """Derive the technology name from the folder or the filename."""

    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = None

    if relative is not None and len(relative.parts) > 1:
        candidate = relative.parts[0]
        for known in KNOWLEDGE_TECHNOLOGIES:
            if candidate.lower() == known.lower():
                return known
        return candidate

    stem = path.stem.lower()
    for known in KNOWLEDGE_TECHNOLOGIES:
        if stem.startswith(known.lower()):
            return known
    return None


def _stat_block(path: Path) -> Dict[str, object]:
    stat = path.stat()
    modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).replace(microsecond=0)
    return {"file_size": stat.st_size, "modified_at": modified.isoformat()}


def _tag(conn, document_id: int, tag: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO document_tags (document_id, tag) VALUES (?, ?)",
        (document_id, tag),
    )


def _upsert_document(
    conn,
    *,
    filename: str,
    source_path: Path,
    technology: Optional[str],
    file_type: str,
    sha256: str,
    file_size: int,
    modified_at: str,
    text_source_path: Optional[Path],
    processing_status: str,
) -> int:
    """Insert or update a document row and return its id.

    An already ``INGESTED`` document is not downgraded while its text source
    remains available.
    """

    now = utc_now()
    source_path_str = str(source_path)
    existing = conn.execute(
        "SELECT document_id, processing_status FROM documents WHERE source_path = ?",
        (source_path_str,),
    ).fetchone()

    text_str = str(text_source_path) if text_source_path else None

    if existing is None:
        cursor = conn.execute(
            """
            INSERT INTO documents
                (filename, source_path, technology, file_type, sha256, file_size,
                 modified_at, text_source_path, processing_status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (filename, source_path_str, technology, file_type, sha256, file_size,
             modified_at, text_str, processing_status, now, now),
        )
        return int(cursor.lastrowid)

    document_id = int(existing["document_id"])
    if existing["processing_status"] == "INGESTED" and text_str is not None:
        processing_status = "INGESTED"

    conn.execute(
        """
        UPDATE documents
           SET filename = ?, technology = ?, file_type = ?, sha256 = ?,
               file_size = ?, modified_at = ?, text_source_path = ?,
               processing_status = ?, updated_at = ?
         WHERE document_id = ?
        """,
        (filename, technology, file_type, sha256, file_size, modified_at,
         text_str, processing_status, now, document_id),
    )
    return document_id


def scan_and_register(conn, config) -> Dict[str, int]:
    """Scan the configured directories and register every source file."""

    stats = {
        "pdf_discovered": 0,
        "text_discovered": 0,
        "created": 0,
        "updated": 0,
        "duplicates": 0,
        "pending_extraction": 0,
        "text_available": 0,
        "orphan_text": 0,
    }

    text_files = _iter_files(config.text_directory, {".txt"})
    text_index: Dict[str, Path] = {}
    for text_path in text_files:
        text_index.setdefault(text_path.stem.lower(), text_path)
    stats["text_discovered"] = len(text_files)

    pdf_files = _iter_files(config.pdf_directory, {".pdf"})
    seen_hashes: Dict[str, Path] = {}

    for pdf_path in pdf_files:
        stats["pdf_discovered"] += 1
        sha256 = compute_sha256(pdf_path)
        is_duplicate = sha256 in seen_hashes
        if is_duplicate:
            stats["duplicates"] += 1
            logger.warning(
                "Duplicate source content: %s duplicates %s",
                pdf_path.name, seen_hashes[sha256].name,
            )
        else:
            seen_hashes[sha256] = pdf_path

        meta = _stat_block(pdf_path)
        text_path = text_index.get(pdf_path.stem.lower())
        if text_path is not None:
            status = "TEXT_AVAILABLE"
            stats["text_available"] += 1
        else:
            status = "PENDING_EXTRACTION"
            stats["pending_extraction"] += 1
            logger.info("PDF without matching text marked PENDING_EXTRACTION: %s", pdf_path.name)

        existed = conn.execute(
            "SELECT 1 FROM documents WHERE source_path = ?", (str(pdf_path),)
        ).fetchone()
        document_id = _upsert_document(
            conn,
            filename=pdf_path.name,
            source_path=pdf_path,
            technology=_technology_for(pdf_path, config.pdf_directory),
            file_type="pdf",
            sha256=sha256,
            file_size=int(meta["file_size"]),
            modified_at=str(meta["modified_at"]),
            text_source_path=text_path,
            processing_status=status,
        )
        stats["updated" if existed else "created"] += 1

        if document_id and _technology_for(pdf_path, config.pdf_directory):
            _tag(conn, document_id, str(_technology_for(pdf_path, config.pdf_directory)))
        if is_duplicate and document_id:
            _tag(conn, document_id, "duplicate")
        if text_path is None and document_id:
            _tag(conn, document_id, "pending-extraction")

    # Stand-alone text files without a matching PDF become their own document.
    pdf_stems = {path.stem.lower() for path in pdf_files}
    for text_path in text_files:
        if text_path.stem.lower() in pdf_stems:
            continue
        stats["orphan_text"] += 1
        sha256 = compute_sha256(text_path)
        meta = _stat_block(text_path)
        existed = conn.execute(
            "SELECT 1 FROM documents WHERE source_path = ?", (str(text_path),)
        ).fetchone()
        document_id = _upsert_document(
            conn,
            filename=text_path.name,
            source_path=text_path,
            technology=_technology_for(text_path, config.text_directory),
            file_type="txt",
            sha256=sha256,
            file_size=int(meta["file_size"]),
            modified_at=str(meta["modified_at"]),
            text_source_path=text_path,
            processing_status="TEXT_AVAILABLE",
        )
        stats["updated" if existed else "created"] += 1
        if document_id:
            _tag(conn, document_id, "text-only")

    conn.commit()
    logger.info("Catalogue scan complete: %s", stats)
    return stats


def readiness_records(conn) -> List:
    """Return catalogue records for every PDF, ordered for review."""

    return conn.execute(
        """
        SELECT filename, technology, source_path, sha256, processing_status, text_source_path
          FROM documents
         WHERE file_type = 'pdf'
         ORDER BY technology, filename
        """
    ).fetchall()


def pending_extraction_count(conn) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS total FROM documents "
        "WHERE file_type = 'pdf' AND processing_status = 'PENDING_EXTRACTION'"
    ).fetchone()
    return int(row["total"])
