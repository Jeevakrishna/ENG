"""Configuration handling for the ACBS Knowledge Engine.

Configuration is loaded from an INI file using the standard library
``configparser`` module. Sensible defaults are applied when a value is absent so
that the engine can run with a minimal configuration file.
"""

from __future__ import annotations

import configparser
from pathlib import Path
from typing import Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = BASE_DIR / "config.ini"

DEFAULT_CLASSIFICATION_KEYWORDS: Dict[str, List[str]] = {
    "Architecture": ["architecture", "layer", "topology", "framework", "component diagram"],
    "Components": ["component", "module", "service", "subsystem", "engine", "processor"],
    "Integration": ["integration", "interface", "api", "endpoint", "adapter", "connector", "protocol"],
    "Batch processing": ["batch", "job", "schedule", "cron", "dag", "workflow", "pipeline"],
    "File transfer": ["file transfer", "sftp", "ftps", "managed file transfer", "imft", "upload", "download"],
    "Messaging": ["message", "queue", "topic", "broker", "solace", "mq", "amqp", "jms", "publish", "subscribe"],
    "Configuration": ["configuration", "config", "parameter", "setting", "property", "environment"],
    "Security": [
        "security", "authentication", "authorisation", "authorization",
        "encryption", "certificate", "tls", "ssl", "credential",
    ],
    "Monitoring": ["monitoring", "metric", "dashboard", "alert", "health check", "log"],
    "Error handling": ["error", "exception", "failure", "retry", "dead-letter", "dead letter"],
    "Troubleshooting": ["troubleshoot", "diagnose", "recovery", "restart", "resolution"],
    "Operational procedures": ["operational", "operation", "runbook", "procedure", "maintenance", "backup"],
}


class Config:
    """Resolved engine configuration with absolute paths."""

    def __init__(self, parser: configparser.ConfigParser, config_path: Path):
        self.parser = parser
        self.config_path = config_path

        self.pdf_directory = self._path("paths", "pdf_directory", "documents/PDF")
        self.text_directory = self._path("paths", "text_directory", "documents/TEXT")
        self.database_path = self._path("paths", "database", "data/knowledge.db")
        self.output_directory = self._path("paths", "output_directory", "exports")
        self.logs_directory = self._path("paths", "logs_directory", "logs")
        self.templates_directory = self._path("paths", "templates_directory", "templates")

        self.chunk_size = self._int("processing", "chunk_size", 1200)
        self.chunk_overlap = self._int("processing", "chunk_overlap", 150)
        self.require_page_markers = self._bool("processing", "require_page_markers", True)
        self.encodings = self._list("processing", "encodings", ["utf-8", "cp1252", "latin-1"])

        self.minimum_score = self._float("search", "minimum_score", 0.0)

        self.classification_keywords = self._classification()

    # -- helpers ---------------------------------------------------------
    def _path(self, section: str, option: str, fallback: str) -> Path:
        value = self.parser.get(section, option, fallback=fallback).strip()
        candidate = Path(value)
        if candidate.is_absolute():
            return candidate
        return (BASE_DIR / candidate).resolve()

    def _int(self, section: str, option: str, fallback: int) -> int:
        try:
            return self.parser.getint(section, option, fallback=fallback)
        except ValueError:
            return fallback

    def _float(self, section: str, option: str, fallback: float) -> float:
        try:
            return self.parser.getfloat(section, option, fallback=fallback)
        except ValueError:
            return fallback

    def _bool(self, section: str, option: str, fallback: bool) -> bool:
        try:
            return self.parser.getboolean(section, option, fallback=fallback)
        except ValueError:
            return fallback

    def _list(self, section: str, option: str, fallback: List[str]) -> List[str]:
        raw = self.parser.get(section, option, fallback=None)
        if not raw:
            return list(fallback)
        return [item.strip() for item in raw.split(",") if item.strip()]

    def _classification(self) -> Dict[str, List[str]]:
        if not self.parser.has_section("classification"):
            return {topic: list(terms) for topic, terms in DEFAULT_CLASSIFICATION_KEYWORDS.items()}
        result: Dict[str, List[str]] = {}
        for topic, raw in self.parser.items("classification"):
            terms = [term.strip().lower() for term in raw.split(",") if term.strip()]
            if terms:
                result[topic] = terms
        return result or {topic: list(terms) for topic, terms in DEFAULT_CLASSIFICATION_KEYWORDS.items()}

    def ensure_directories(self) -> None:
        """Create the directories referenced by the configuration."""

        for directory in (
            self.pdf_directory,
            self.text_directory,
            self.database_path.parent,
            self.output_directory,
            self.output_directory / "documentation",
            self.output_directory / "evidence",
            self.output_directory / "reports",
            self.logs_directory,
        ):
            directory.mkdir(parents=True, exist_ok=True)


def load_config(path: Optional[str] = None) -> Config:
    """Load configuration from ``path`` (or the default ``config.ini``)."""

    parser = configparser.ConfigParser()
    # Preserve key case so that classification topic names keep their casing.
    parser.optionxform = str  # type: ignore[assignment]

    config_path = Path(path).resolve() if path else DEFAULT_CONFIG_PATH
    if config_path.exists():
        parser.read(config_path, encoding="utf-8")

    config = Config(parser, config_path)
    config.ensure_directories()
    return config
