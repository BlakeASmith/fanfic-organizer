# ao3kit

Toolkit for scraping AO3 search results, wrangling tags, downloading native EPUBs, and cleaning metadata for Calibre import.

Supported surfaces are the **CLI** and the **Calibre plugin** (shared library core + aligned capabilities). See [AGENTS.md](AGENTS.md#interface-parity). The web UI and REST API are **deprecated and frozen**.

## Interfaces

| Interface | How to use |
|---|---|
| **CLI** | `python -m ao3kit scrape\|tags\|download\|config\|login\|rate …` |
| **Calibre plugin** | `calibre-plugin/` — search AO3, search similar from selected books, import JSONL / `ao3-import.zip`, import a series, fill Series on existing books, download missing EPUBs, simplify tags/fandoms/relationships, edit collections of selected books, recompute collections from rules, warm the tag cache in the background, graph tag relationships, set up collection and tag rules, or purge rare Tags. AO3 login and import defaults live in plugin settings. |
| **Web UI** (deprecated) | `python -m ao3kit serve` → http://127.0.0.1:8000 — frozen, not updated |
| **REST API** (deprecated) | Same server → http://127.0.0.1:8000/api/v1/docs — frozen, not updated |

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
python -m ao3kit scrape --url "https://archiveofourown.org/works?..." -o results.jsonl --download
python -m ao3kit scrape --url "https://archiveofourown.org/series/6133236" -o series.jsonl
python -m ao3kit scrape --include-series --url "https://archiveofourown.org/works?..." -o results.jsonl
python -m ao3kit scrape --fill-series-from results.jsonl -o filled.jsonl
python -m ao3kit scrape --parse-similar --similar-from results.jsonl

# Tag cleanup
python -m ao3kit tags resolve --jsonl results.jsonl --work-id 50448730 --verbose
python -m ao3kit config collections add --match mentions --values "River Song" --collection "River Song"
python -m ao3kit config collections pin --work-id 50448730 --collection Jegulus
python -m ao3kit config collections unpin --work-id 50448730 --collection Jegulus
python -m ao3kit tags collections --jsonl results.jsonl -o cleaned.jsonl
python -m ao3kit tags collections --jsonl results.jsonl -o explain.json --explain
python -m ao3kit tags apply "Jegulus" "Fluff"
python -m ao3kit tags apply --collections-only --jsonl results.jsonl
python -m ao3kit tags warm start --jsonl results.jsonl
python -m ao3kit tags warm status
python -m ao3kit tags warm log
python -m ao3kit tags warm log --follow
python -m ao3kit tags warm stop
python -m ao3kit tags graph --names-file tags.txt -o tag-graph.html --open
python -m ao3kit config mappings add --values Jegulus --action keep_separate --stop

# Test AO3 login (or AO3_USERNAME / AO3_PASSWORD in .env)
python -m ao3kit login

# EPUB download
python -m ao3kit download -i results.jsonl -o epubs/

# Rate-limit snapshot + long-term request log (429 / Retry-After / hourly rollups)
python -m ao3kit rate
python -m ao3kit rate --hours 24
python -m ao3kit rate export --hourly --days 30 -o rate-hourly.jsonl
```

## Docs

Full project notes (layout, tag rules, CLI, plugin): **[AGENTS.md](AGENTS.md)**.

The web UI and REST API still start with `python -m ao3kit serve` but are frozen; new work belongs on the CLI or Calibre plugin.

Related tools: [ao3downloader](https://github.com/nianeyna/ao3downloader), [FanFicFare](https://github.com/jimmxinu/fanficfare).
