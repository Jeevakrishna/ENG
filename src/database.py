"""SQLite storage layer for the ACBS Knowledge Engine.

The database schema follows the data model described in the system design:
documents, pages, chunks, evidence, ingestion_runs and document_tags.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS documents (
        document_id       INTEGER PRIMARY KEY AUTOINCREMENT,
        filename          TEXT    NOT NULL,
        source_path       TEXT    NOT NULL UNIQUE,
        technology        TEXT,
        file_type         TEXT,
        sha256            TEXT,
        file_size         INTEGER,
        modified_at       TEXT,
        text_source_path  TEXT,
        processing_status TEXT    NOT NULL DEFAULT 'DISCOVERED',
        created_at        TEXT,
        updated_at        TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pages (
        page_id           INTEGER PRIMARY KEY AUTOINCREMENT,
        document_id       INTEGER NOT NULL,
        page_number       INTEGER,
        page_text         TEXT,
        extraction_status TEXT,
        FOREIGN KEY (document_id) REFERENCES documents (document_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chunks (
        chunk_id        INTEGER PRIMARY KEY AUTOINCREMENT,
        document_id     INTEGER NOT NULL,
        page_start      INTEGER,
        page_end        INTEGER,
        section_heading TEXT,
        content         TEXT NOT NULL,
        content_hash    TEXT,
        FOREIGN KEY (document_id) REFERENCES documents (document_id) ON DELETE CASCADE,
        UNIQUE (document_id, content_hash)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS evidence (
        evidence_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        chunk_id      INTEGER NOT NULL,
        topic         TEXT,
        evidence_type TEXT,
        confidence    REAL,
        review_status TEXT DEFAULT 'Pending',
        FOREIGN KEY (chunk_id) REFERENCES chunks (chunk_id) ON DELETE CASCADE,
        UNIQUE (chunk_id, topic)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ingestion_runs (
        run_id           INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at       TEXT,
        completed_at     TEXT,
        files_discovered INTEGER DEFAULT 0,
        files_ingested   INTEGER DEFAULT 0,
        files_pending    INTEGER DEFAULT 0,
        files_failed     INTEGER DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS document_tags (
        tag_id      INTEGER PRIMARY KEY AUTOINCREMENT,
        document_id INTEGER NOT NULL,
        tag         TEXT NOT NULL,
        FOREIGN KEY (document_id) REFERENCES documents (document_id) ON DELETE CASCADE,
        UNIQUE (document_id, tag)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_documents_technology ON documents (technology)",
    "CREATE INDEX IF NOT EXISTS idx_documents_status ON documents (processing_status)",
    "CREATE INDEX IF NOT EXISTS idx_documents_sha256 ON documents (sha256)",
    "CREATE INDEX IF NOT EXISTS idx_documents_filename ON documents (filename)",
    "CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks (document_id)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_topic ON evidence (topic)",
    "CREATE INDEX IF NOT EXISTS idx_document_tags_tag ON document_tags (tag)",
)


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a connection with row access by column name and foreign keys on."""

    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialise(db_path: Path) -> None:
    """Create the schema if it does not already exist."""

    connection = connect(db_path)
    try:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        connection.commit()
    finally:
        connection.close()
