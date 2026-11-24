#!/usr/bin/env python3
"""ACBS Knowledge Engine - command-line interface.

A local, standard-library-only documentation tool for ACBS and related banking
integration technologies. Run ``python main.py --help`` for usage.
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from src import catalogue, database, documentation, ingestion, search
from src.config import load_config
from src.logging_setup import configure_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="acbs-engine",
        description="Local knowledge engine for ACBS and integration technology documentation.",
    )
    parser.add_argument("--config", help="Path to an INI configuration file")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Create the SQLite database schema")
    subparsers.add_parser("scan", help="Catalogue PDF and text sources")
    subparsers.add_parser("readiness", help="Report extraction readiness for PDF sources")
    subparsers.add_parser("ingest", help="Ingest available text files")
    subparsers.add_parser("classify", help="Classify indexed passages into technical topics")
    subparsers.add_parser("generate", help="Generate Markdown documentation and CSV reports")
    subparsers.add_parser("status", help="Show a summary of the knowledge base")

    search_parser = subparsers.add_parser("search", help="Search indexed passages")
    search_parser.add_argument("query", help="Keyword query")
    search_parser.add_argument("--technology", help="Restrict to a technology, e.g. IMFT")
    search_parser.add_argument("--document", help="Restrict to documents whose name matches")
    search_parser.add_argument("--limit", type=int, default=10, help="Maximum results to show")

    subparsers.add_parser("pipeline", help="Run scan, ingest, classify and generate in sequence")
    return parser


def _print_scan(stats: dict) -> None:
    print("Catalogue scan")
    print(f"  PDF sources discovered : {stats['pdf_discovered']}")
    print(f"  Text sources discovered: {stats['text_discovered']}")
    print(f"  Text available         : {stats['text_available']}")
    print(f"  Pending extraction     : {stats['pending_extraction']}")
    print(f"  Duplicate PDFs         : {stats['duplicates']}")
    print(f"  Records created/updated: {stats['created']}/{stats['updated']}")


def _print_ingestion(stats: dict) -> None:
    print("Ingestion")
    print(f"  Documents processed : {stats['documents_processed']}")
    print(f"  Ingested            : {stats['documents_ingested']}")
    print(f"  Needs review        : {stats['documents_review']}")
    print(f"  Failed              : {stats['documents_failed']}")
    print(f"  Pages stored        : {stats['pages']}")
    print(f"  Passages stored     : {stats['chunks']}")


def _run_readiness(conn) -> int:
    records = catalogue.readiness_records(conn)
    if not records:
        print("No PDF sources are catalogued. Run the 'scan' command first.")
        return 0
    print(f"{'Status':<20}{'Technology':<12}Source")
    print("-" * 72)
    for row in records:
        print(f"{(row['processing_status'] or ''):<20}{(row['technology'] or ''):<12}{row['filename']}")
    pending = catalogue.pending_extraction_count(conn)
    print("-" * 72)
    print(f"{pending} PDF source(s) marked PENDING_EXTRACTION (no text available).")
    return 0


def _run_search(conn, config, args) -> int:
    results = search.search(
        conn, config, args.query, technology=args.technology,
        document=args.document, limit=args.limit,
    )
    if not results:
        print("No matching passages found.")
        return 0
    for index, result in enumerate(results, start=1):
        page = result["page_start"]
        page_label = f"p. {page}" if page is not None else "page unavailable"
        print(f"{index}. [{result['score']}] {result['filename']} ({page_label})")
        if result["section_heading"]:
            print(f"   Section: {result['section_heading']}")
        print(f"   {result['snippet']}")
        print()
    return 0


def _run_status(conn) -> int:
    def scalar(query: str) -> int:
        return int(conn.execute(query).fetchone()[0])

    documents = scalar("SELECT COUNT(*) FROM documents")
    pdf_sources = scalar("SELECT COUNT(*) FROM documents WHERE file_type = 'pdf'")
    ingested = scalar("SELECT COUNT(*) FROM documents WHERE processing_status = 'INGESTED'")
    review = scalar("SELECT COUNT(*) FROM documents WHERE processing_status = 'NEEDS_REVIEW'")
    pages = scalar("SELECT COUNT(*) FROM pages")
    chunks = scalar("SELECT COUNT(*) FROM chunks")
    evidence = scalar("SELECT COUNT(*) FROM evidence")

    print("Knowledge base status")
    print(f"  Documents          : {documents}")
    print(f"  PDF sources        : {pdf_sources}")
    print(f"  Pending extraction : {catalogue.pending_extraction_count(conn)}")
    print(f"  Ingested           : {ingested}")
    print(f"  Needs review       : {review}")
    print(f"  Pages stored       : {pages}")
    print(f"  Passages indexed   : {chunks}")
    print(f"  Evidence records   : {evidence}")
    technologies = conn.execute(
        "SELECT DISTINCT technology FROM documents WHERE technology IS NOT NULL ORDER BY technology"
    ).fetchall()
    print("  Technologies       : " + (", ".join(row["technology"] for row in technologies) or "none"))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    configure_logging(config.logs_directory, args.verbose)

    database.initialise(config.database_path)
    connection = database.connect(config.database_path)

    try:
        if args.command == "init":
            print(f"Initialised database schema at {config.database_path}")
            return 0

        if args.command == "scan":
            _print_scan(catalogue.scan_and_register(connection, config))
            return 0

        if args.command == "readiness":
            return _run_readiness(connection)

        if args.command == "ingest":
            _print_ingestion(ingestion.ingest_documents(connection, config))
            return 0

        if args.command == "classify":
            stats = search.classify_chunks(connection, config)
            print(f"Classified {stats['chunks_examined']} passage(s); "
                  f"{stats['evidence_created']} evidence record(s) written.")
            return 0

        if args.command == "search":
            return _run_search(connection, config, args)

        if args.command == "status":
            return _run_status(connection)

        if args.command == "generate":
            result = documentation.generate_all(connection, config)
            print("Generated artefacts:")
            for path in result["technology_documents"]:
                print(f"  {path}")
            print(f"  {result['cross_technology_comparison']}")
            print(f"  {result['evidence_register']}")
            print(f"  {result['ingestion_report']}")
            print(f"  {result['extraction_readiness_report']}")
            print(f"  {result['quality_report']}")
            return 0

        if args.command == "pipeline":
            print("== Scanning sources ==")
            _print_scan(catalogue.scan_and_register(connection, config))
            print("\n== Ingesting text ==")
            _print_ingestion(ingestion.ingest_documents(connection, config))
            print("\n== Classifying passages ==")
            stats = search.classify_chunks(connection, config)
            print(f"  Evidence records written: {stats['evidence_created']}")
            print("\n== Generating documentation ==")
            result = documentation.generate_all(connection, config)
            print(f"  Technology documents: {len(result['technology_documents'])}")
            print(f"  Reports in: {config.output_directory}")
            return 0

        print(f"Unknown command: {args.command}", file=sys.stderr)
        return 2
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
