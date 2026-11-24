"""Unit tests for the ACBS Knowledge Engine.

Run with: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src import catalogue, database, documentation, ingestion, search  # noqa: E402
from src.config import Config  # noqa: E402
import configparser  # noqa: E402


class IngestionParsingTests(unittest.TestCase):
    def test_normalise_text_collapses_blank_lines(self):
        raw = "Heading\r\n\r\n\r\nBody line\r\n"
        self.assertEqual(ingestion.normalise_text(raw), "Heading\n\nBody line")

    def test_split_pages_detects_markers(self):
        text = (
            "DOCUMENT: Sample.pdf\n\n"
            "===== PAGE 1 =====\nFirst page text\n\n"
            "===== PAGE 2 =====\nSecond page text\n"
        )
        pages, found, declared = ingestion.split_pages(text)
        self.assertTrue(found)
        self.assertEqual(declared, "Sample.pdf")
        numbers = [page["page_number"] for page in pages if page["page_number"] is not None]
        self.assertEqual(numbers, [1, 2])

    def test_split_pages_without_markers(self):
        pages, found, declared = ingestion.split_pages("No markers here at all.")
        self.assertFalse(found)
        self.assertIsNone(declared)
        self.assertEqual(len(pages), 1)

    def test_heading_detection(self):
        self.assertTrue(ingestion.is_heading_line("Architecture"))
        self.assertTrue(ingestion.is_heading_line("## Overview"))
        self.assertTrue(ingestion.is_heading_line("3.2 Scope"))
        self.assertFalse(ingestion.is_heading_line(
            "This is a normal sentence that is clearly not a heading."))

    def test_chunk_pages_respects_size_and_overlap(self):
        pages = [{"page_number": 1, "text": ("word " * 400).strip()}]
        chunks = ingestion.chunk_pages(pages, chunk_size=300, overlap=50)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertEqual(chunk["page_start"], 1)
            self.assertLessEqual(len(chunk["content"]), 420)

    def test_chunk_pages_records_page_range(self):
        pages = [
            {"page_number": 1, "text": "A" * 500},
            {"page_number": 2, "text": "B" * 500},
        ]
        chunks = ingestion.chunk_pages(pages, chunk_size=200, overlap=0)
        self.assertEqual(chunks[0]["page_start"], 1)
        self.assertEqual(chunks[-1]["page_end"], 2)


class SearchTests(unittest.TestCase):
    def test_tokenise_removes_stopwords(self):
        tokens = search.tokenise("The transfer of the file to the remote host")
        self.assertIn("transfer", tokens)
        self.assertNotIn("the", tokens)

    def test_tokenise_handles_punctuation(self):
        self.assertEqual(search.tokenise("TLS, certificates; retry!"),
                         ["tls", "certificates", "retry"])


class PipelineTests(unittest.TestCase):
    """End-to-end test over a temporary workspace."""

    def _config(self, root: Path) -> Config:
        parser = configparser.ConfigParser()
        parser.optionxform = str  # type: ignore[assignment]
        parser.read_dict(
            {
                "paths": {
                    "pdf_directory": str(root / "PDF"),
                    "text_directory": str(root / "TEXT"),
                    "database": str(root / "data" / "test.db"),
                    "output_directory": str(root / "exports"),
                    "logs_directory": str(root / "logs"),
                    "templates_directory": str(PROJECT_ROOT / "templates"),
                },
                "processing": {
                    "chunk_size": "600",
                    "chunk_overlap": "80",
                    "require_page_markers": "true",
                    "encodings": "utf-8, cp1252, latin-1",
                },
                "search": {"minimum_score": "0.0"},
            }
        )
        config = Config(parser, root / "config.ini")
        config.ensure_directories()
        return config

    def test_end_to_end_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self._config(root)

            text_dir = config.text_directory / "IMFT"
            text_dir.mkdir(parents=True, exist_ok=True)
            (text_dir / "Sample-Architecture.txt").write_text(
                "DOCUMENT: Sample-Architecture.pdf\n\n"
                "===== PAGE 1 =====\n"
                "Managed File Transfer architecture overview for the integration platform.\n\n"
                "The transfer engine executes outbound and inbound file transfers and stores\n"
                "transfer metadata for traceability and operational reporting.\n\n"
                "===== PAGE 2 =====\n"
                "Messaging and batch schedule description with queue details and retry policy.\n\n"
                "Transfer jobs run on a fixed schedule and declare dependencies on inbound file\n"
                "arrival and on the completion of upstream transfers before they start.\n",
                encoding="utf-8",
            )
            # A PDF placeholder so discovery and readiness can be exercised.
            (config.pdf_directory / "IMFT").mkdir(parents=True, exist_ok=True)
            (config.pdf_directory / "IMFT" / "Sample-Architecture.pdf").write_bytes(b"%PDF-1.4 dummy")
            (config.pdf_directory / "IMFT" / "Pending-Only.pdf").write_bytes(b"%PDF-1.4 pending")

            database.initialise(config.database_path)
            conn = database.connect(config.database_path)
            try:
                scan = catalogue.scan_and_register(conn, config)
                self.assertEqual(scan["pdf_discovered"], 2)
                self.assertEqual(scan["pending_extraction"], 1)
                self.assertEqual(scan["text_available"], 1)

                ingest = ingestion.ingest_documents(conn, config)
                self.assertEqual(ingest["documents_ingested"], 1)
                self.assertGreaterEqual(ingest["chunks"], 1)

                search.classify_chunks(conn, config)
                self.assertGreaterEqual(
                    int(conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]), 1)

                results = search.search(conn, config, "messaging queue")
                self.assertTrue(results)
                self.assertEqual(results[0]["filename"], "Sample-Architecture.pdf")

                artefacts = documentation.generate_all(conn, config)
                self.assertEqual(len(artefacts["technology_documents"]), 1)
                self.assertTrue(Path(artefacts["evidence_register"]).exists())
                self.assertTrue(Path(artefacts["quality_report"]).exists())

                readiness = catalogue.readiness_records(conn)
                self.assertEqual(len(readiness), 2)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
