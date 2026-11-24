# ACBS Knowledge Engine

A local, standard-library-only Python tool that catalogues technical documentation for
ACBS and related banking integration technologies (IMFT, Apache Airflow, Solace, IBM MQ),
ingests available plain-text exports, indexes searchable passages, records traceable
evidence and generates structured Markdown documentation plus CSV reports.

**Dependency policy:** Python standard library only. The project runs without `pip install`.

---

## 1. Requirements

| Item | Requirement |
|---|---|
| Operating system | Windows (commands below use PowerShell) |
| Python | 3.8 or newer (developed and tested on 3.12) |
| Third-party packages | None |
| Database | SQLite via the built-in `sqlite3` module |

Confirm Python is available:

```powershell
python --version
```

---

## 2. Setup

### 2.1 Get the project

Place the project folder anywhere on the local machine, for example:

```
C:\Users\<you>\Downloads\MAIN ENG
```

### 2.2 Open a terminal in the project root

```powershell
cd "C:\Users\<you>\Downloads\MAIN ENG"
```

### 2.3 No installation step is required

There is nothing to install. `requirements.txt` is intentionally empty. Do **not** run
`pip install`.

### 2.4 Create the directory structure (already present, recreate if needed)

```powershell
$dirs = @(
  "documents\PDF\ACBS","documents\PDF\IMFT","documents\PDF\Airflow","documents\PDF\Solace","documents\PDF\MQ",
  "documents\TEXT\ACBS","documents\TEXT\IMFT","documents\TEXT\Airflow","documents\TEXT\Solace","documents\TEXT\MQ",
  "data","src","templates","exports\documentation","exports\evidence","exports\reports","logs","tests"
)
foreach ($d in $dirs) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
```

The engine also creates any missing directories automatically when it runs.

### 2.5 Initialise the database (optional first step)

```powershell
python main.py init
```

This creates the SQLite schema at `data/knowledge.db`. Every other command also
initialises the schema automatically, so this step is only needed to verify setup.

### 2.6 Verify the setup

```powershell
python main.py --help
python -m unittest discover -s tests
```

---

## 3. Project layout

```
MAIN ENG/
├── main.py                    Command-line entrypoint
├── config.ini                 Configuration (paths, chunking, classification keywords)
├── requirements.txt           Empty - no third-party dependencies
├── documents/
│   ├── PDF/<Technology>/      Source PDF documents (catalogued, never modified)
│   └── TEXT/<Technology>/     Plain-text exports to ingest
├── data/knowledge.db          SQLite knowledge base (created on first run)
├── templates/
│   └── technology-document.md Markdown template populated during generation
├── exports/
│   ├── documentation/         Generated technology and comparison documents
│   ├── evidence/              evidence-register.csv
│   └── reports/               ingestion / readiness / quality CSV reports
├── logs/engine.log            Execution log
├── src/                       Engine modules
│   ├── config.py
│   ├── logging_setup.py
│   ├── database.py
│   ├── catalogue.py
│   ├── ingestion.py
│   ├── search.py
│   └── documentation.py
└── tests/test_engine.py       Unit tests
```

---

## 4. Adding source material

### 4.1 PDF documents

Copy PDFs into the technology folder, for example:

```
documents/PDF/IMFT/IMFT-Architecture.pdf
documents/PDF/ACBS/ACBS-Integration-Guide.pdf
```

PDFs are catalogued, hashed and tracked. They are **never** modified or deleted.

> The standard library cannot reliably read arbitrary PDF content. A PDF without a
> matching text export is marked `PENDING_EXTRACTION` rather than being treated as
> ingested content.

### 4.2 Plain-text exports

Place a text export beside the PDF it describes, in the matching `TEXT` folder, using the
**same base filename**:

```
documents/TEXT/IMFT/IMFT-Architecture.txt     ← matches IMFT-Architecture.pdf
```

Text files use a simple convention so page references can be preserved. Separate pages
with `===== PAGE n =====` markers and optionally declare the original filename:

```
DOCUMENT: IMFT-Architecture.pdf

===== PAGE 1 =====
Introduction
...

===== PAGE 2 =====
Architecture
...
```

Notes:

- If markers are absent, the content is still ingested but the document is marked
  `NEEDS_REVIEW` and page references are reported as unavailable (never fabricated).
- A text file with no matching PDF is catalogued as a text-only source.
- If the file is empty or unusually short it is marked `NEEDS_REVIEW`.

---

## 5. Commands

Run every command from the project root. Global options apply to all commands:

| Option | Meaning |
|---|---|
| `--config PATH` | Use a specific INI configuration file instead of `config.ini` |
| `--verbose` | Enable verbose (`DEBUG`) logging |
| `-h`, `--help` | Show help for the program or a specific command |

### `init` — create the database schema

```powershell
python main.py init
```

### `scan` — catalogue sources

Discovers PDF and text files, computes SHA-256 hashes, records metadata, links text
exports to their PDF, detects duplicate PDFs and marks PDFs without text.

```powershell
python main.py scan
```

### `readiness` — extraction-readiness report

Lists every catalogued PDF with its processing status and counts the PDFs awaiting
extraction.

```powershell
python main.py readiness
```

### `ingest` — ingest text sources

Reads available text files, validates content, parses page markers, detects headings,
normalises text and splits it into searchable passages stored in SQLite.

```powershell
python main.py ingest
```

### `classify` — classify passages into topics

Assigns configured technical topics (architecture, integration, messaging, security, …)
to indexed passages and records evidence rows for review. Re-running replaces existing
classification for the same passage and topic.

```powershell
python main.py classify
```

### `search` — query indexed passages

Ranks passages with a custom TF-IDF-style score and shows the source file, page
reference and matching text.

```powershell
python main.py search "managed file transfer"
python main.py search "retry policy" --technology IMFT
python main.py search "certificate" --document "IMFT-Architecture"
python main.py search "batch schedule" --limit 5
```

| Argument / option | Required | Meaning |
|---|---|---|
| `query` | Yes | Keywords to search for |
| `--technology` | No | Restrict to a technology, e.g. `IMFT` |
| `--document` | No | Restrict to documents whose filename matches |
| `--limit` | No | Maximum results to display (default `10`) |

> Search scores indicate textual relevance only. They are not a measure of factual
> correctness.

### `generate` — build documentation and reports

Populates the Markdown template from indexed evidence and writes all reports.

```powershell
python main.py generate
```

Produces:

| Artefact | Location |
|---|---|
| Technology document | `exports/documentation/<Technology>/<Technology>-technical-documentation.md` |
| Cross-technology comparison | `exports/documentation/cross-technology-comparison.md` |
| Evidence register | `exports/evidence/evidence-register.csv` |
| Ingestion report | `exports/reports/ingestion-report.csv` |
| Extraction-readiness report | `exports/reports/extraction-readiness-report.csv` |
| Quality report | `exports/reports/quality-report.csv` |

### `status` — knowledge-base summary

```powershell
python main.py status
```

### `pipeline` — run the full workflow

Runs `scan` → `ingest` → `classify` → `generate` in one step:

```powershell
python main.py pipeline
```

### Using a different configuration file

```powershell
python main.py --config "C:\path\to\other-config.ini" scan
```

---

## 6. Typical workflow

```powershell
# 1. Verify setup
python --version
python main.py --help

# 2. Add sources
#    Copy PDFs into documents/PDF/<Technology>/
#    Copy text exports into documents/TEXT/<Technology>/

# 3. Catalogue and process
python main.py scan
python main.py readiness
python main.py ingest
python main.py classify

# 4. Search and review
python main.py search "integration interface" --technology IMFT
python main.py status

# 5. Generate documentation
python main.py generate
```

Or in a single command:

```powershell
python main.py pipeline
```

---

## 7. Configuration (`config.ini`)

```ini
[paths]
pdf_directory = documents/PDF
text_directory = documents/TEXT
database = data/knowledge.db
output_directory = exports
logs_directory = logs
templates_directory = templates

[processing]
chunk_size = 1200
chunk_overlap = 150
require_page_markers = true
encodings = utf-8, cp1252, latin-1

[search]
minimum_score = 0.0

[classification]
Architecture = architecture, layer, topology, framework, component diagram
Security = security, authentication, authorisation, encryption, certificate, tls, ssl
...
```

| Section | Key | Meaning |
|---|---|---|
| `paths` | `pdf_directory` | Root folder scanned for PDFs |
| `paths` | `text_directory` | Root folder scanned for text exports |
| `paths` | `database` | SQLite database file |
| `paths` | `output_directory` | Root folder for generated artefacts |
| `paths` | `logs_directory` | Log file folder |
| `paths` | `templates_directory` | Folder containing Markdown templates |
| `processing` | `chunk_size` | Target passage size in characters |
| `processing` | `chunk_overlap` | Overlap between consecutive passages |
| `processing` | `require_page_markers` | Flag missing page markers for review |
| `processing` | `encodings` | Decoding fallbacks tried in order |
| `search` | `minimum_score` | Minimum relevance score to display |
| `classification` | `<Topic>` | Comma-separated keywords that map to a topic |

Relative paths are resolved against the project root. Values are initial examples and
should be tuned against real documents.

---

## 8. Document status values

| Status | Meaning |
|---|---|
| `DISCOVERED` | Source file found |
| `TEXT_AVAILABLE` | A matching text export was found |
| `PENDING_EXTRACTION` | PDF exists but no text is available |
| `INGESTED` | Text successfully indexed |
| `NEEDS_REVIEW` | Text quality or page references require review |
| `FAILED` | Processing failed |

---

## 9. Running the tests

```powershell
python -m unittest discover -s tests -v
```

The suite covers text normalisation, page-marker parsing, heading detection, chunking,
search tokenisation and an end-to-end pipeline over a temporary workspace.

---

## 10. Logs and troubleshooting

- Execution logs are written to `logs/engine.log` and echoed to the console.
- Use `--verbose` for detailed diagnostics.

| Symptom | Likely cause | Action |
|---|---|---|
| `readiness` reports no PDFs | No PDFs placed in `documents/PDF/<Technology>/` | Add PDFs, then run `scan` |
| Everything is `PENDING_EXTRACTION` | No matching text exports supplied | Add `.txt` exports beside the PDFs, then `scan` |
| `No matching passages found.` | Query terms absent, or nothing ingested | Run `ingest`, then `classify`; broaden the query |
| Document marked `NEEDS_REVIEW` | Missing page markers or very short text | Add `===== PAGE n =====` markers and re-run `ingest` |
| Document marked `FAILED` | Text source missing or undecodable | Check the file and the `encodings` setting |
| Sections show "Not found in the available source material." | No indexed evidence for that topic | Add source text covering the topic; adjust `[classification]` keywords |

---

## 11. Limitations

- No native general-purpose PDF text extraction; no OCR.
- No semantic embeddings or AI summarisation.
- Diagrams and screenshots cannot be interpreted.
- Exported text may not preserve tables or complex layouts exactly.
- Heading detection and acronym extraction are heuristics and must be reviewed.
- Search ranking approximates relevance and does not validate factual correctness.

All source PDFs are left unchanged. Back up `data/knowledge.db` and generated
documentation according to approved corporate procedures.
