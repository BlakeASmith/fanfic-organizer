# ao3kit

Toolkit for scraping AO3 search results, wrangling tags, downloading native EPUBs, and cleaning metadata for Calibre import.

Supported surfaces are the **CLI** and the **Calibre plugin** (shared library core + aligned capabilities). See [AGENTS.md](AGENTS.md#interface-parity). The web UI and REST API are **deprecated and frozen**.

## Calibre plugin (GitHub Releases)

Download **AO3Scraper.zip** from [Releases](https://github.com/BlakeASmith/wranglekit/releases). That zip is the whole plugin: UI, ao3kit, and Python libraries. You do not need a git checkout or `pip install`.

1. In Calibre: **Preferences → Plugins → Load plugin from file** → choose `AO3Scraper.zip`.
2. Restart Calibre.
3. Open a **new** fanfic library (or one you are happy to write). Plugin settings → optional AO3 login.

To build that zip from this repo: `python makeplugin.py zip`.

## Interfaces

| Interface | How to use |
|---|---|
| **CLI** | `python -m ao3kit scrape\|tags\|download\|cover\|job\|config\|login\|rate …` |
| **Calibre plugin** | GitHub **AO3Scraper.zip** (Preferences → Plugins → Load plugin from file), or `python makeplugin.py install` from this checkout. Search AO3, search similar, import JSONL / `ao3-import.zip`. **Selected books**: **Complete selected** (series + missing EPUBs + simplify tags), series, missing EPUBs, generate covers, simplify tags, collections. **Tags and collections**: rules, graph, purge, tag cache. **Running jobs…** to attach logs / stop. AO3 login, cover style, and import defaults live in plugin settings. |
| **Web UI** (deprecated) | `python -m ao3kit serve` → http://127.0.0.1:8000 — frozen, not updated |
| **REST API** (deprecated) | Same server → http://127.0.0.1:8000/api/v1/docs — frozen, not updated |

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # optional: AO3_USERNAME / AO3_PASSWORD
python -m ao3kit config init
python makeplugin.py install   # Calibre plugin from this checkout; add --restart only when iterating on plugin UI
python makeplugin.py zip       # Self-contained AO3Scraper.zip for GitHub Releases
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
python -m ao3kit tags graph serve
python -m ao3kit tags graph reload
python -m ao3kit job start -- scrape -o results.jsonl --verbose
python -m ao3kit job list
python -m ao3kit job log <id> --follow
python -m ao3kit job stop <id>
python -m ao3kit job retry <id>
python -m ao3kit job delete <id>
python -m ao3kit job clear --finished
python -m ao3kit config mappings add --values Jegulus --action keep_separate --stop

# Test AO3 login (or AO3_USERNAME / AO3_PASSWORD in .env)
python -m ao3kit login

# EPUB download
python -m ao3kit download -i results.jsonl -o epubs/
python -m ao3kit download -i results.jsonl -o epubs/ --no-cover
python -m ao3kit cover --dir epubs/
python -m ao3kit cover preview --title "Ship Happens" --author "Ann Thology" --fandom "Star Wars" -o cover.png
python -m ao3kit config set cover.fields title,author,wordcount,score,fandom

# Rate-limit snapshot + long-term request log (429 / Retry-After / hourly rollups)
python -m ao3kit rate
python -m ao3kit rate --hours 24
python -m ao3kit rate export --hourly --days 30 -o rate-hourly.jsonl
```

## Docs

Full project notes (layout, tag rules, CLI, plugin): **[AGENTS.md](AGENTS.md)**.

The web UI and REST API still start with `python -m ao3kit serve` but are frozen; new work belongs on the CLI or Calibre plugin.

Related tools: [ao3downloader](https://github.com/nianeyna/ao3downloader), [FanFicFare](https://github.com/jimmxinu/fanficfare).
