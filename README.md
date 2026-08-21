# ao3kit

Toolkit for scraping AO3 search results, wrangling tags, downloading native EPUBs, and cleaning metadata for Calibre import.

Keep the **CLI, web UI, REST API, and Calibre plugin** in relative feature parity (shared library core + aligned capabilities). See [AGENTS.md](AGENTS.md#interface-parity).

## Interfaces

| Interface | How to use |
|---|---|
| **CLI** | `python -m ao3kit scrape\|tags\|download\|config\|serve …` |
| **Web UI** | `python -m ao3kit serve` → http://127.0.0.1:8000 |
| **REST API** | Same server → http://127.0.0.1:8000/api/v1/docs |
| **Calibre plugin** | `calibre-plugin/` — import JSONL / `ao3-import.zip` |

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # optional: AO3_USERNAME / AO3_PASSWORD
python -m ao3kit config init
```

## Quick start

```bash
# Scrape search results to JSONL
python -m ao3kit scrape --url "https://archiveofourown.org/works?..." -o results.jsonl

# Tag cleanup
python -m ao3kit tags resolve --jsonl results.jsonl --work-id 50448730 --verbose
python -m ao3kit tags apply "Jegulus" "Fluff"

# EPUB download
python -m ao3kit download -i results.jsonl -o epubs/

# Web UI + REST API
python -m ao3kit serve --reload
```

REST example:

```bash
curl -s http://127.0.0.1:8000/api/v1/health
curl -s -X POST http://127.0.0.1:8000/api/v1/scrape \
  -H 'Content-Type: application/json' \
  -d '{"criteria":{"tag_id":"Doctor Who (2005)"},"max_results":5}'
```

## Docs

Full project notes (layout, tag rules, web HTMX/SSE, REST endpoint reference): **[AGENTS.md](AGENTS.md)**.

Related tools: [ao3downloader](https://github.com/nianeyna/ao3downloader), [FanFicFare](https://github.com/jimmxinu/fanficfare).
