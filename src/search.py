"""Layer 5: keyword search, relevance ranking and evidence classification.

Search uses a custom TF-IDF-style algorithm built from the standard library
(``re`` for tokenisation, ``collections.Counter`` for term frequency and
``math`` for inverse document frequency and vector normalisation). The score
approximates textual relevance and is not a measure of factual correctness.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Dict, List, Optional

from .logging_setup import get_logger

logger = get_logger("search")

TOKEN_RE = re.compile(r"[a-z0-9]+")

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have",
    "in", "into", "is", "it", "its", "of", "on", "or", "that", "the", "this", "to",
    "was", "were", "will", "with", "which", "we", "you", "your", "our", "if", "not",
    "but", "can", "may", "must", "should",
}

SNIPPET_LENGTH = 400

ACRONYM_EXPANSION_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9]*(?:[\s\-][A-Za-z0-9]+){0,5})\s*\(([A-Z]{2,8})\)"
)
ACRONYM_REVERSE_RE = re.compile(
    r"\b([A-Z]{2,8})\s*\(([A-Za-z][A-Za-z0-9 ,\-/]{2,80})\)"
)


def tokenise(text: str) -> List[str]:
    """Return lower-case search tokens with stopwords removed."""

    return [
        token for token in TOKEN_RE.findall(text.lower())
        if len(token) > 1 and token not in STOPWORDS
    ]


def _load_chunks(conn, technology: Optional[str] = None, document: Optional[str] = None) -> List:
    query = """
        SELECT c.chunk_id, c.document_id, c.page_start, c.page_end, c.section_heading,
               c.content, d.filename, d.technology, d.source_path
          FROM chunks c
          JOIN documents d ON d.document_id = c.document_id
         WHERE 1 = 1
    """
    params: List[object] = []
    if technology:
        query += " AND LOWER(d.technology) = LOWER(?)"
        params.append(technology)
    if document:
        query += " AND LOWER(d.filename) LIKE LOWER(?)"
        params.append(f"%{document}%")
    return conn.execute(query, params).fetchall()


def search(conn, config, query: str, technology: Optional[str] = None,
           document: Optional[str] = None, limit: int = 10) -> List[Dict[str, object]]:
    """Return passages ranked by a TF-IDF-style relevance score."""

    tokens = tokenise(query)
    if not tokens:
        return []

    chunks = _load_chunks(conn, technology, document)
    if not chunks:
        return []

    contents = [(row, tokenise(row["content"])) for row in chunks]
    total = len(contents)
    unique_tokens = set(tokens)

    document_frequency: Dict[str, int] = {token: 0 for token in unique_tokens}
    for _, chunk_tokens in contents:
        present = unique_tokens.intersection(chunk_tokens)
        for token in present:
            document_frequency[token] += 1

    idf = {
        token: math.log((total + 1) / (document_frequency[token] + 1)) + 1.0
        for token in unique_tokens
    }

    results: List[Dict[str, object]] = []
    for row, chunk_tokens in contents:
        length = len(chunk_tokens)
        if length == 0:
            continue
        counts = Counter(chunk_tokens)
        norm = math.sqrt(sum(value * value for value in counts.values())) or 1.0
        score = 0.0
        matched = set()
        for token in unique_tokens:
            frequency = counts.get(token, 0)
            if frequency:
                score += (frequency / length) * idf[token]
                matched.add(token)
        if score <= 0.0:
            continue
        score /= norm
        if score < config.minimum_score:
            continue
        results.append(
            {
                "score": round(score, 4),
                "chunk_id": int(row["chunk_id"]),
                "filename": row["filename"],
                "technology": row["technology"],
                "page_start": row["page_start"],
                "page_end": row["page_end"],
                "section_heading": row["section_heading"],
                "matched_terms": sorted(matched),
                "snippet": _snippet(row["content"], SNIPPET_LENGTH),
            }
        )

    results.sort(key=lambda item: (item["score"], len(item["matched_terms"])), reverse=True)
    return results[:limit]


def _snippet(content: str, length: int) -> str:
    collapsed = re.sub(r"\s+", " ", content).strip()
    if len(collapsed) <= length:
        return collapsed
    return collapsed[:length].rsplit(" ", 1)[0] + " ..."


def classify_chunks(conn, config, limit_per_chunk: Optional[int] = None) -> Dict[str, int]:
    """Classify indexed passages into technical topics using configured keywords.

    Every matched topic is recorded unless ``limit_per_chunk`` caps the number
    of topics stored for each passage.
    """

    keywords = config.classification_keywords
    chunks = conn.execute("SELECT chunk_id, content FROM chunks").fetchall()

    stats = {"chunks_examined": 0, "evidence_created": 0}
    for chunk in chunks:
        stats["chunks_examined"] += 1
        text_lower = str(chunk["content"]).lower()
        scored: List[tuple] = []
        for topic, terms in keywords.items():
            hits = sum(text_lower.count(term.lower()) for term in terms)
            if hits > 0:
                confidence = min(1.0, hits / 5.0)
                scored.append((hits, confidence, topic))
        scored.sort(reverse=True)
        selected = scored if limit_per_chunk is None else scored[:limit_per_chunk]
        for _, confidence, topic in selected:
            conn.execute(
                """
                INSERT INTO evidence (chunk_id, topic, evidence_type, confidence, review_status)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(chunk_id, topic) DO UPDATE SET
                    confidence = excluded.confidence,
                    evidence_type = excluded.evidence_type
                """,
                (int(chunk["chunk_id"]), topic, "keyword_match", confidence, "Pending"),
            )
            stats["evidence_created"] += 1

    conn.commit()
    logger.info("Classification complete: %s", stats)
    return stats


def get_acronyms(conn, technology: Optional[str] = None) -> Dict[str, Dict[str, object]]:
    """Extract acronym candidates and their expansions from indexed passages.

    This is a best-effort heuristic and is labelled accordingly in generated
    documentation.
    """

    query = """
        SELECT c.content, c.page_start, d.filename, d.technology
          FROM chunks c JOIN documents d ON d.document_id = c.document_id
         WHERE 1 = 1
    """
    params: List[object] = []
    if technology:
        query += " AND LOWER(d.technology) = LOWER(?)"
        params.append(technology)

    acronyms: Dict[str, Dict[str, object]] = {}
    for row in conn.execute(query, params).fetchall():
        content = str(row["content"])
        for expansion, acronym in ACRONYM_EXPANSION_RE.findall(content):
            if len(expansion) < 4:
                continue
            acronyms.setdefault(
                acronym,
                {"expansion": expansion.strip(), "filename": row["filename"],
                 "page": row["page_start"], "technology": row["technology"]},
            )
        for acronym, expansion in ACRONYM_REVERSE_RE.findall(content):
            acronyms.setdefault(
                acronym,
                {"expansion": expansion.strip(), "filename": row["filename"],
                 "page": row["page_start"], "technology": row["technology"]},
            )
    return acronyms
