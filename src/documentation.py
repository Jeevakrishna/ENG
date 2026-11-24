"""Layer 6: Markdown documentation compiler and CSV report generation.

Loads indexed evidence, populates the Markdown template and produces
technology-specific documents, a cross-technology comparison, an evidence
register and quality reports.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Dict, List, Optional

from . import search
from .catalogue import utc_now
from .logging_setup import get_logger

logger = get_logger("documentation")

EMPTY_MESSAGE = "Not found in the available source material."

DEFAULT_TEMPLATE_NAME = "technology-document.md"

RELATED_TECHNOLOGIES_TEXT = (
    "This document covers one technology within the ACBS documentation set. "
    "Documented relationships with ACBS, IMFT, Apache Airflow, Solace and IBM MQ "
    "are reported only where the indexed source material supports them. "
    "Cross-technology relationships that are not evidenced in the sources remain "
    "unverified and must be confirmed by the application or integration team."
)

# Placeholder -> (topics, result limit)
SECTION_TOPICS = {
    "{{SECTION_EXECUTIVE_SUMMARY}}": (["Architecture", "Components", "Integration"], 4),
    "{{SECTION_PURPOSE}}": (["Integration", "Architecture"], 3),
    "{{SECTION_SCOPE}}": (["Integration", "File transfer", "Messaging", "Batch processing"], 4),
    "{{SECTION_TECHNICAL_OVERVIEW}}": (["Architecture", "Components"], 4),
    "{{SECTION_ARCHITECTURE}}": (["Architecture"], 6),
    "{{SECTION_COMPONENTS}}": (["Components"], 6),
    "{{SECTION_COMPONENT_DETAILS}}": (["Components"], 8),
    "{{SECTION_DEPENDENCIES}}": (["Integration", "Configuration"], 4),
    "{{SECTION_INTEGRATION}}": (["Integration"], 8),
    "{{SECTION_WORKFLOW}}": (["Batch processing", "Integration", "File transfer"], 6),
    "{{SECTION_FORMATS}}": (["File transfer", "Messaging"], 6),
    "{{SECTION_BATCH}}": (["Batch processing"], 6),
    "{{SECTION_MESSAGING}}": (["Messaging"], 6),
    "{{SECTION_CONFIGURATION}}": (["Configuration"], 6),
    "{{SECTION_SECURITY}}": (["Security"], 6),
    "{{SECTION_MONITORING}}": (["Monitoring", "Operational procedures"], 6),
    "{{SECTION_ERROR_HANDLING}}": (["Error handling", "Troubleshooting"], 6),
}


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-") or "unknown"


def _page_label(start: Optional[int], end: Optional[int]) -> str:
    if start is None and end is None:
        return "page unavailable"
    if end is None or start == end:
        return f"p. {start}"
    return f"pp. {start}-{end}"


def _reference(row) -> str:
    return f"{row['filename']} ({_page_label(row['page_start'], row['page_end'])})"


def _snippet(content: str, length: int = 320) -> str:
    collapsed = re.sub(r"\s+", " ", str(content)).strip()
    if len(collapsed) <= length:
        return collapsed
    return collapsed[:length].rsplit(" ", 1)[0] + " ..."


def _load_template(config) -> str:
    template_path = Path(config.templates_directory) / DEFAULT_TEMPLATE_NAME
    if template_path.exists():
        return template_path.read_text(encoding="utf-8")
    fallback = Path(__file__).resolve().parent.parent / "templates" / DEFAULT_TEMPLATE_NAME
    if fallback.exists():
        return fallback.read_text(encoding="utf-8")
    raise FileNotFoundError(f"Documentation template not found: {template_path}")


def _rows_for_topics(conn, technology: str, topics: List[str], limit: int) -> List:
    placeholders = ", ".join("?" for _ in topics)
    query = f"""
        SELECT e.topic, e.confidence, e.review_status, c.chunk_id,
               c.page_start, c.page_end, c.section_heading, c.content,
               d.filename
          FROM evidence e
          JOIN chunks c ON c.chunk_id = e.chunk_id
          JOIN documents d ON d.document_id = c.document_id
         WHERE LOWER(d.technology) = LOWER(?) AND e.topic IN ({placeholders})
         ORDER BY e.confidence DESC, d.filename, c.page_start
         LIMIT ?
    """
    params: List[object] = [technology, *topics, limit]
    return conn.execute(query, params).fetchall()


def _format_evidence(rows: List) -> str:
    if not rows:
        return EMPTY_MESSAGE
    lines: List[str] = []
    seen_chunks = set()
    for row in rows:
        if row["chunk_id"] in seen_chunks:
            continue
        seen_chunks.add(row["chunk_id"])
        lines.append(
            f"- {_snippet(row['content'])}\n"
            f"  - Source: `{_reference(row)}` · Classified topic: {row['topic']}"
        )
    return "\n".join(lines)


def _acronym_table(acronyms: Dict[str, Dict[str, object]]) -> str:
    if not acronyms:
        return EMPTY_MESSAGE
    lines = ["| Term | Expansion | Source |", "|---|---|---|"]
    for acronym in sorted(acronyms):
        info = acronyms[acronym]
        page = _page_label(info.get("page"), info.get("page"))  # type: ignore[arg-type]
        lines.append(f"| {acronym} | {info['expansion']} | {info['filename']} ({page}) |")
    lines.append("")
    lines.append("> Acronym extraction is heuristic and must be validated during review.")
    return "\n".join(lines)


def _evidence_register_markdown(conn, technology: str) -> str:
    rows = conn.execute(
        """
        SELECT e.evidence_id, e.topic, e.confidence, e.review_status,
               c.page_start, c.page_end, d.filename
          FROM evidence e
          JOIN chunks c ON c.chunk_id = e.chunk_id
          JOIN documents d ON d.document_id = c.document_id
         WHERE LOWER(d.technology) = LOWER(?)
         ORDER BY e.topic, d.filename, c.page_start
        """,
        (technology,),
    ).fetchall()
    if not rows:
        return "No evidence recorded. Run the `classify` command after ingestion."
    lines = [
        "| Evidence ID | Claim or topic | Source file | Page | Review status |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| EV-{row['evidence_id']} | {row['topic']} | {row['filename']} | "
            f"{_page_label(row['page_start'], row['page_end'])} | {row['review_status']} |"
        )
    return "\n".join(lines)


def _open_questions(conn, config, technology: str) -> str:
    pending = conn.execute(
        "SELECT filename FROM documents WHERE file_type = 'pdf' "
        "AND processing_status = 'PENDING_EXTRACTION' AND LOWER(technology) = LOWER(?)",
        (technology,),
    ).fetchall()
    review = conn.execute(
        "SELECT filename FROM documents WHERE processing_status = 'NEEDS_REVIEW' "
        "AND LOWER(technology) = LOWER(?)",
        (technology,),
    ).fetchall()
    covered = {
        row["topic"] for row in conn.execute(
            """
            SELECT DISTINCT e.topic
              FROM evidence e
              JOIN chunks c ON c.chunk_id = e.chunk_id
              JOIN documents d ON d.document_id = c.document_id
             WHERE LOWER(d.technology) = LOWER(?)
            """,
            (technology,),
        ).fetchall()
    }
    missing_topics = [topic for topic in config.classification_keywords if topic not in covered]

    lines: List[str] = []
    if pending:
        lines.append(
            f"- {len(pending)} PDF source(s) await extraction and are not represented: "
            + ", ".join(row["filename"] for row in pending)
        )
    if review:
        lines.append(
            f"- {len(review)} source(s) need review (missing page markers or short content): "
            + ", ".join(row["filename"] for row in review)
        )
    if missing_topics:
        lines.append(
            "- No indexed evidence was found for the following topics: "
            + ", ".join(missing_topics)
        )
    lines.append(
        "- Confirm documented configuration values, endpoints and operational "
        "procedures with the application or support team before relying on them."
    )
    return "\n".join(lines)


def build_technology_document(conn, config, technology: str, template: Optional[str] = None) -> str:
    template = template or _load_template(config)
    documents = conn.execute(
        "SELECT filename FROM documents WHERE LOWER(technology) = LOWER(?) ORDER BY filename",
        (technology,),
    ).fetchall()
    acronyms = search.get_acronyms(conn, technology)

    context: Dict[str, str] = {
        "{{TITLE}}": f"{technology} — Technical Documentation",
        "{{TECHNOLOGY}}": technology,
        "{{VERSION}}": "1.0 (generated)",
        "{{GENERATED_DATE}}": utc_now(),
        "{{DOCUMENT_COUNT}}": str(len(documents)),
        "{{SOURCE_DOCUMENTS}}": ", ".join(row["filename"] for row in documents) or "None catalogued",
        "{{SECTION_RELATED}}": RELATED_TECHNOLOGIES_TEXT,
        "{{SECTION_ACRONYMS}}": _acronym_table(acronyms),
        "{{SECTION_EVIDENCE_REGISTER}}": _evidence_register_markdown(conn, technology),
        "{{SECTION_OPEN_QUESTIONS}}": _open_questions(conn, config, technology),
    }

    for placeholder, (topics, limit) in SECTION_TOPICS.items():
        rows = _rows_for_topics(conn, technology, topics, limit)
        context[placeholder] = _format_evidence(rows)

    markdown = template
    for placeholder, value in context.items():
        markdown = markdown.replace(placeholder, value)
    return markdown


def generate_cross_technology_comparison(conn, config) -> Path:
    rows = conn.execute(
        """
        SELECT d.technology AS technology,
               COUNT(DISTINCT d.document_id) AS documents,
               COUNT(DISTINCT c.chunk_id) AS chunks,
               COUNT(DISTINCT e.topic) AS topics
          FROM documents d
          LEFT JOIN chunks c ON c.document_id = d.document_id
          LEFT JOIN evidence e ON e.chunk_id = c.chunk_id
         WHERE d.technology IS NOT NULL AND d.technology <> ''
         GROUP BY d.technology
         ORDER BY d.technology
        """
    ).fetchall()

    lines = [
        "# Cross-Technology Comparison",
        "",
        f"**Generated date:** {utc_now()}",
        "",
        "| Technology | Documents | Indexed passages | Classified topics |",
        "|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['technology']} | {row['documents']} | {row['chunks']} | {row['topics']} |"
        )
    if not rows:
        lines.append("| None | 0 | 0 | 0 |")
    lines.append("")
    lines.append(
        "> This comparison reports indexed coverage only. A low passage count does "
        "not imply that a technology is unimportant; it may indicate that source "
        "text is still pending extraction."
    )

    target = Path(config.output_directory) / "documentation" / "cross-technology-comparison.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Wrote cross-technology comparison: %s", target)
    return target


def generate_technology_documents(conn, config) -> List[Path]:
    template = _load_template(config)
    technologies = [
        row["technology"] for row in conn.execute(
            "SELECT DISTINCT technology FROM documents "
            "WHERE technology IS NOT NULL AND technology <> '' ORDER BY technology"
        ).fetchall()
    ]
    written: List[Path] = []
    for technology in technologies:
        markdown = build_technology_document(conn, config, technology, template)
        target = (
            Path(config.output_directory) / "documentation" / _slug(technology)
            / f"{_slug(technology)}-technical-documentation.md"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(markdown, encoding="utf-8")
        written.append(target)
        logger.info("Wrote technology document: %s", target)
    return written


def _write_csv(path: Path, header: List[str], rows: List[List[object]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    logger.info("Wrote report: %s", path)
    return path


def generate_evidence_register_csv(conn, config) -> Path:
    rows = conn.execute(
        """
        SELECT e.evidence_id, e.topic, e.evidence_type, e.confidence, e.review_status,
               d.filename, d.technology, c.page_start, c.page_end, c.content
          FROM evidence e
          JOIN chunks c ON c.chunk_id = e.chunk_id
          JOIN documents d ON d.document_id = c.document_id
         ORDER BY d.technology, d.filename, c.page_start, e.topic
        """
    ).fetchall()
    data = [
        [
            row["evidence_id"], row["technology"], row["filename"],
            row["page_start"] if row["page_start"] is not None else "unavailable",
            row["page_end"] if row["page_end"] is not None else "unavailable",
            row["topic"], row["evidence_type"], row["confidence"],
            row["review_status"], _snippet(row["content"], 200),
        ]
        for row in rows
    ]
    header = ["evidence_id", "technology", "source_file", "page_start", "page_end",
              "topic", "evidence_type", "confidence", "review_status", "snippet"]
    return _write_csv(Path(config.output_directory) / "evidence" / "evidence-register.csv", header, data)


def generate_ingestion_report_csv(conn, config) -> Path:
    rows = conn.execute(
        """
        SELECT d.document_id, d.filename, d.technology, d.file_type, d.file_size,
               d.sha256, d.processing_status, d.text_source_path, d.updated_at,
               (SELECT COUNT(*) FROM chunks c WHERE c.document_id = d.document_id) AS chunks,
               (SELECT COUNT(*) FROM pages p WHERE p.document_id = d.document_id) AS pages
          FROM documents d
         ORDER BY d.technology, d.filename
        """
    ).fetchall()
    data = [
        [
            row["document_id"], row["filename"], row["technology"], row["file_type"],
            row["file_size"], row["sha256"], row["processing_status"],
            row["text_source_path"] or "", row["pages"], row["chunks"], row["updated_at"],
        ]
        for row in rows
    ]
    header = ["document_id", "filename", "technology", "file_type", "file_size",
              "sha256", "processing_status", "text_source_path", "pages", "chunks", "updated_at"]
    return _write_csv(Path(config.output_directory) / "reports" / "ingestion-report.csv", header, data)


def generate_extraction_readiness_report_csv(conn, config) -> Path:
    rows = conn.execute(
        """
        SELECT filename, technology, source_path, sha256, processing_status, text_source_path
          FROM documents
         WHERE file_type = 'pdf'
         ORDER BY processing_status, technology, filename
        """
    ).fetchall()
    data = [
        [
            row["filename"], row["technology"] or "", row["source_path"], row["sha256"],
            row["processing_status"], row["text_source_path"] or "",
        ]
        for row in rows
    ]
    header = ["filename", "technology", "source_path", "sha256", "processing_status", "text_source_path"]
    return _write_csv(
        Path(config.output_directory) / "reports" / "extraction-readiness-report.csv", header, data
    )


def generate_quality_report_csv(conn, config) -> Path:
    def scalar(query: str, params: tuple = ()) -> int:
        return int(conn.execute(query, params).fetchone()[0])

    metrics = [
        ("documents_total", scalar("SELECT COUNT(*) FROM documents")),
        ("pdf_documents", scalar("SELECT COUNT(*) FROM documents WHERE file_type = 'pdf'")),
        ("text_documents", scalar("SELECT COUNT(*) FROM documents WHERE file_type = 'txt'")),
        ("pending_extraction", scalar(
            "SELECT COUNT(*) FROM documents WHERE processing_status = 'PENDING_EXTRACTION'")),
        ("ingested", scalar(
            "SELECT COUNT(*) FROM documents WHERE processing_status = 'INGESTED'")),
        ("needs_review", scalar(
            "SELECT COUNT(*) FROM documents WHERE processing_status = 'NEEDS_REVIEW'")),
        ("failed", scalar(
            "SELECT COUNT(*) FROM documents WHERE processing_status = 'FAILED'")),
        ("pages_total", scalar("SELECT COUNT(*) FROM pages")),
        ("chunks_total", scalar("SELECT COUNT(*) FROM chunks")),
        ("chunks_without_page_reference", scalar(
            "SELECT COUNT(*) FROM chunks WHERE page_start IS NULL AND page_end IS NULL")),
        ("evidence_total", scalar("SELECT COUNT(*) FROM evidence")),
        ("evidence_pending_review", scalar(
            "SELECT COUNT(*) FROM evidence WHERE review_status = 'Pending'")),
        ("duplicate_source_documents", scalar(
            "SELECT COUNT(*) FROM document_tags WHERE tag = 'duplicate'")),
    ]

    data = [[name, value] for name, value in metrics]
    return _write_csv(
        Path(config.output_directory) / "reports" / "quality-report.csv",
        ["metric", "value"], data,
    )


def generate_all(conn, config) -> Dict[str, object]:
    """Generate every documentation artefact and report."""

    documents = generate_technology_documents(conn, config)
    comparison = generate_cross_technology_comparison(conn, config)
    evidence = generate_evidence_register_csv(conn, config)
    ingestion_report = generate_ingestion_report_csv(conn, config)
    readiness = generate_extraction_readiness_report_csv(conn, config)
    quality = generate_quality_report_csv(conn, config)

    result = {
        "technology_documents": [str(path) for path in documents],
        "cross_technology_comparison": str(comparison),
        "evidence_register": str(evidence),
        "ingestion_report": str(ingestion_report),
        "extraction_readiness_report": str(readiness),
        "quality_report": str(quality),
    }
    logger.info("Documentation generation complete.")
    return result
