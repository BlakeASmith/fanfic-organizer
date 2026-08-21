# AO3 downloader metadata scraper

Making this for my wife. She is using multiple different tools and having to do a lot of manual
work to manage fan-fiction and metadata cleaning etc.

**Interfaces:** CLI (`python -m ao3kit …`), web UI, JSON REST API (`/api/v1`), and the Calibre plugin — keep them in [relative feature parity](#interface-parity).

## Resources

- [Ao3 downloader](https://github.com/nianeyna/ao3downloader)
- [FanFicFare](https://github.com/jimmxinu/fanficfare)

## Project layout

```
ao3kit/                 # Application package
  http.py / rate.py / rate_store.py / scrape.py / epubs.py / webapp.py / api.py
  config.py             # User settings + rule file storage (.ao3kit/)
  tags/                 # Tag profiles, resolver, SQLite cache, code-first rules
  cli.py                # Unified CLI (python -m ao3kit …)
.ao3kit/                # User config home (gitignored; AO3KIT_HOME override)
.cache/                 # Tag cache + host-wide rate limiter DB (gitignored)
templates/              # Jinja2 templates for the web UI
calibre-plugin/         # Calibre importer (raw + cleaned metadata columns)
```

Root `scrape_ao3.py`, `tag_metadata.py`, `download_epubs.py`, `web.py`, `ao3_http.py`,
and `ao3_rate.py` are thin compatibility shims. Prefer importing from `ao3kit` and
running `python -m ao3kit …`.

## Interface parity

Four surfaces share one core. Prefer implementing behavior in `ao3kit` library modules, then wiring every relevant surface. Cursor agents: see `.cursor/rules/interface-parity.mdc`.

| Surface | Entry | Owns |
|---|---|---|
| CLI | `python -m ao3kit …` | Full toolkit (scrape, tags, download, config, serve) |
| Web UI | `ao3kit.webapp` `/` | Interactive scrape/download/settings (HTMX + SSE) |
| REST API | `ao3kit.api` `/api/v1` | Same capabilities as JSON (jobs for long runs) |
| Calibre plugin | `calibre-plugin/` | Import JSONL/zip; raw + cleaned metadata columns |

**Relative parity** means the same capabilities and data contracts, not the same UX:

- Put new logic in `scrape` / `tags` / `epubs` / `config` / `http` / `rate`, not only in one UI.
- When adding a user-facing feature, update CLI, API, and web where it applies; update the plugin when import, columns, enrich-on-import, or zip/JSONL layout changes.
- Work record JSON, `cleaned` enrich shape, and `ao3-import.zip` layout are shared contracts — do not diverge them per surface.
- AO3 traffic must use `ao3kit.http` so host-wide rate limiting (`.cache/ao3_rate.sqlite`) applies across every interface.
- OK: SSE HTML vs JSON job polling vs CLI stdout. Not OK: a scrape filter or tag resolve path that exists on only one of CLI / API / web.

## Setup

```bash
pip install -r requirements.txt
```

## CLI

Unified entry point:

```bash
python -m ao3kit scrape --url "https://archiveofourown.org/works?..." -o results.jsonl
python -m ao3kit scrape --criteria-file example_criteria.json -o results.jsonl --verbose
python -m ao3kit tags tag "Doctor Who (2005)"
python -m ao3kit tags resolve --jsonl results.jsonl --work-id 50448730 --verbose
python -m ao3kit download -i results.jsonl -o epubs/
python -m ao3kit config init
python -m ao3kit serve --reload
```

Optional AO3 login via `--username` / `--password`, or a local gitignored `.env` file:

```bash
cp .env.example .env   # then edit AO3_USERNAME / AO3_PASSWORD
```

Env vars `AO3_USERNAME` / `AO3_PASSWORD` are loaded automatically by `ao3kit.http`.

### Tag wrangling metadata

AO3 tag wranglers curate canonical tags and synonym maps. `ao3kit.tags` extracts that
data for work-tag cleanup (collapsing synonyms onto the canonical form).

```bash
python -m ao3kit tags tag "Doctor Who (2005)"
python -m ao3kit tags tag "https://archiveofourown.org/tags/Kissing"
python -m ao3kit tags tag "Kisses" --synonym-map

python -m ao3kit tags search --name "Amy Pond" --type Character --status canonical
python -m ao3kit tags search --url "https://archiveofourown.org/tags/search?..."

python -m ao3kit tags tag-sets Naruto
python -m ao3kit tags tag-set 3937
```

`parse_tag_page()` returns category, canonical/filterable flags, `synonym_of`, parents, synonyms, metatags, subtags, and typed children. Use `TagProfile.synonym_map()` for name→canonical cleanup maps. Tag search and tag-set helpers mirror AO3’s `/tags/search` and `/tag_sets` URLs.

Resolve a work’s tags (or ad-hoc names) onto the canonical set via `TagResolver` / the `resolve` subcommand. Synonyms collapse to their canonical; unmarked tags are kept unless `--drop-unmarked`.

**Caching strategy** (SQLite; avoids repeat AO3 hits):

1. Raw tag miss → fetch that tag’s profile.
2. If it’s a synonym, follow the merger link and fetch the **canonical** page once.
3. Index every synonym listed on the canonical page as one *tree* (shared `root` + `fetched_at`) → later raw forms resolve with **zero** fetches.
4. Persist to `.cache/ao3_tag_cache.sqlite` (gitignored; legacy `.json` is imported once on open).
5. Trees older than `tag_cache_ttl_days` (default **90**; `0` = never) are purged automatically so wrangling stays fresh.

```bash
python -m ao3kit tags resolve "wolfstar" "Kisses" "Slow Burn"
python -m ao3kit tags resolve --jsonl results.jsonl --work-id 50448730 --verbose
python -m ao3kit tags resolve --jsonl results.jsonl --work-id 50448730 --drop-unmarked --include-fandoms
python -m ao3kit tags resolve "Kisses" --cache .cache/ao3_tag_cache.sqlite --verbose
python -m ao3kit tags resolve "Kisses" --cache-ttl-days 30 --verbose
python -m ao3kit tags resolve "Kisses" --no-cache
python -m ao3kit config set tag_cache_ttl_days 90
python -m ao3kit tags apply --rules example_tag_rules.py \
  "Jegulus" "Melody Pond" "River Song - Freeform" "Fluff"
python -m ao3kit tags apply --rules example_tag_rules.py \
  --jsonl results.jsonl --work-id 50448730 --verbose
```

### User config & rules storage

User settings and rule modules live in **`.ao3kit/`** (project-local; override with
`AO3KIT_HOME`). Gitignored.

```
.ao3kit/
  config.yaml           # delay, resolve flags, active_rules, …
  rules/
    default.py          # active code-first rules (editable)
```

```bash
python -m ao3kit config init
python -m ao3kit config show
python -m ao3kit config set request_delay 6
python -m ao3kit config rules list
python -m ao3kit config rules new river_song
python -m ao3kit config rules use river_song
python -m ao3kit config rules install-example
python -m ao3kit tags apply "Jegulus" "Fluff"   # uses active rules from config
```

Web UI: open **Settings** (`/settings`) to edit preferences, create rule modules,
and edit Python rule source (validated on save).

### Tag mapping rules & collections (code-first)

Rules are **Python** (`TagRule.apply` or `@rule` functions). YAML is optional wiring for simple built-ins.

| Concern | Behavior |
|---|---|
| Precedence | Higher `priority` first; file/list order breaks ties |
| Mapping (`keep_separate` / `map_to` / `drop`) | First applying rule that sets one wins |
| Collections | Every applying rule contributes (union) |
| `stop: true` | End rule evaluation for that tag |

Preferred config module (`example_tag_rules.py`):

```python
from ao3kit.tags.rules import TagRule, RuleContext, RuleEffect, TagRulesConfig

class RiverSongCollection(TagRule):
    id = "river-song-collection"
    priority = 50

    def apply(self, ctx: RuleContext) -> RuleEffect | None:
        if "river song" in ctx.original.lower() or ctx.canonical == "River Song":
            return RuleEffect(collections=["River Song"])
        return None

RULES = TagRulesConfig(resolve_canonical=True, rules=[RiverSongCollection()])
```

Built-ins (`KeepSeparateRule`, `MapToRule`, `CollectRule`, `DropRule`) cover common cases and can be referenced from YAML via `use: keep_separate` / `use: module:Class`.

---

## REST API

JSON API implemented in `ao3kit.api` and mounted at **`/api/v1`** on the same FastAPI process as the web UI. One server serves HTML + JSON.

```bash
python -m ao3kit serve --reload
```

| Surface | URL |
|---|---|
| Interactive OpenAPI docs | http://127.0.0.1:8000/api/v1/docs |
| OpenAPI schema | http://127.0.0.1:8000/api/v1/openapi.json |
| API-only (no web UI) | `uvicorn ao3kit.api:app --port 8001` — routes have **no** `/api/v1` prefix |

### Authentication

Optional AO3 login on endpoints that hit the site: JSON fields `username` / `password`, or env `AO3_USERNAME` / `AO3_PASSWORD` (both required if either is set). Config endpoints are local only and do not need AO3 credentials.

### Jobs (scrape & download)

Long-running work returns **`202`** with `{ "job_id": "…" }`. Poll status until finished:

| `status` | Meaning |
|---|---|
| `queued` | Accepted, not started |
| `running` | In progress (`message` updates) |
| `done` | Finished; results in the status payload |
| `error` | Failed; see `error` / `message` |

Jobs live in memory for the process lifetime (restart clears them). Download zips are available at `GET /api/v1/download/{job_id}/zip` when `zip_ready` is true.

### Endpoints

Paths below are relative to `/api/v1` when using `serve` / `webapp:app`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | `{ "status": "ok", "version": "…" }` |
| `POST` | `/scrape/parse-url` | Body `{ "url" }` → `criteria`, `start_page`, `search_url` |
| `POST` | `/scrape` | Start scrape (`url` **or** `criteria` + filters) → job |
| `GET` | `/scrape/{job_id}` | Job status + matched `works[]` |
| `GET` | `/tags/{name}` | Tag profile (`?synonym_map=true` for name→canonical map) |
| `POST` | `/tags/search` | Tag search (`name`/`type`/`status`/… or `url`) |
| `POST` | `/tags/resolve` | Canonicalize `tags[]` |
| `POST` | `/tags/apply` | Resolve + active (or named) rules |
| `POST` | `/tags/enrich` | Enrich scrape `records` / `jsonl` with `cleaned` |
| `GET` | `/tag-sets?q=` | Search owned tag sets |
| `GET` | `/tag-sets/{id}` | Tag set detail |
| `POST` | `/download` | Body `records[]` or `jsonl` → download job |
| `GET` | `/download/{job_id}` | Status, counts, enriched records, `zip_ready` |
| `GET` | `/download/{job_id}/zip` | Import zip when ready |
| `GET` | `/config` | User settings + config home path |
| `PATCH` | `/config` | Partial update of `UserSettings` fields |
| `GET` | `/config/rules` | List rule modules |
| `POST` | `/config/rules` | Create rule (`name`, optional `source`, `make_active`) |
| `GET`/`PUT` | `/config/rules/{name}` | Read / write rule source (PUT validates load) |
| `POST` | `/config/rules/{name}/activate` | Set active rules module |
| `POST` | `/config/rules/install-example` | Copy bundled `example_tag_rules.py` |

**Scrape request** (either `url` or `criteria` with `tag_id` / `query`):

```json
{
  "url": "https://archiveofourown.org/works?…",
  "max_results": 25,
  "min_score": 8,
  "min_kudos": 50,
  "min_words": 1000,
  "complete_only": true,
  "delay": 5.0,
  "username": null,
  "password": null
}
```

Or structured criteria:

```json
{
  "criteria": {
    "tag_id": "Doctor Who (2005)",
    "sort_column": "kudos_count",
    "language_id": "en",
    "query": "amy/rory"
  },
  "start_page": 1,
  "max_results": 10
}
```

### Examples

```bash
# Health
curl -s http://127.0.0.1:8000/api/v1/health

# Parse a search URL into criteria
curl -s -X POST http://127.0.0.1:8000/api/v1/scrape/parse-url \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://archiveofourown.org/works?work_search%5Bsort_column%5D=kudos_count&tag_id=Doctor+Who+%282005%29"}'

# Start a scrape, then poll
JOB=$(curl -s -X POST http://127.0.0.1:8000/api/v1/scrape \
  -H 'Content-Type: application/json' \
  -d '{"criteria":{"tag_id":"Doctor Who (2005)"},"max_results":5}' | jq -r .job_id)
curl -s "http://127.0.0.1:8000/api/v1/scrape/$JOB"

# Resolve tags
curl -s -X POST http://127.0.0.1:8000/api/v1/tags/resolve \
  -H 'Content-Type: application/json' \
  -d '{"tags":["Kisses","wolfstar"],"use_cache":true}'

# Apply active rules
curl -s -X POST http://127.0.0.1:8000/api/v1/tags/apply \
  -H 'Content-Type: application/json' \
  -d '{"tags":["Jegulus","Fluff"]}'

# Config
curl -s http://127.0.0.1:8000/api/v1/config
curl -s -X PATCH http://127.0.0.1:8000/api/v1/config \
  -H 'Content-Type: application/json' \
  -d '{"request_delay":6,"notes":"via API"}'
```

---

## Web UI

The web UI is a single-page form at `/` served by `ao3kit.webapp`. It uses **HTMX** for partial page updates and the **HTMX SSE extension** for live scrape progress. There is almost no custom JavaScript beyond copy-to-clipboard buttons. The same process also serves the [REST API](#rest-api) at `/api/v1`.

### Run the server

```bash
python -m ao3kit serve --reload
# or:
uvicorn ao3kit.webapp:app --reload
```

| Surface | URL |
|---|---|
| Web UI | http://127.0.0.1:8000 |
| REST docs | http://127.0.0.1:8000/api/v1/docs |

### User flow

1. **Paste an AO3 search URL** (optional) — empty criteria fields are auto-filled from the URL.
2. **Edit criteria** (optional) — changing any criteria field switches the form to “source of truth” mode so URL parsing no longer overwrites your edits.
3. **Set result filters** — max results, min quality score, min kudos/words, complete-only, page delay, etc.
4. **Submit** — results stream in live: status messages, a results table, and JSONL output.
5. **Copy** — “Copy links” (one URL per line) or “Copy full data” (JSONL).

### Search source: URL vs form

The hidden field `use_form_criteria` controls which input wins:

| Value | Behavior |
|---|---|
| `0` (default) | If a search URL is present, parse it and merge into empty/default criteria fields |
| `1` | Ignore URL parsing; build the search from the form fields only |

Editing any field with class `criteria-field` sets `use_form_criteria` to `1`.

### HTMX interactions

**URL auto-fill** — the URL input posts to `/parse-url/fill` on paste or after 400 ms of typing:

```
hx-post="/parse-url/fill"
hx-target="#search-criteria"
hx-swap="outerHTML"
```

The server returns the `search_criteria` partial with merged values. On parse error, it returns the criteria partial plus an out-of-band (OOB) status panel update.

**Start scrape** — the form posts to `/scrape/start`:

```
hx-post="/scrape/start"
hx-target="#scrape-live"
hx-swap="innerHTML"
```

The response is a `scrape_session` partial containing an SSE connection to `/scrape/events/{scrape_id}`.

### Live results via Server-Sent Events

When a scrape starts, the server:

1. Stores the form data in an in-memory job dict keyed by `scrape_id`.
2. Returns HTML that opens an SSE connection (`sse-connect="/scrape/events/{scrape_id}"`).
3. On connect, starts a background thread that runs `scrape_search()` from `ao3kit.scrape`.
4. Streams **HTML fragments** (not JSON) as named SSE events.

SSE event types:

| Event | Template | What it updates |
|---|---|---|
| `start` | `partials/scrape_start.html` | Status panel, search URL summary, clears results table/JSONL |
| `status` | `partials/status_oob.html` | Status panel (page progress, login messages) |
| `work` | `partials/work_row.html` | Appends a table row; OOB updates result count and JSONL |
| `done` | `partials/status_oob.html` | Final “Done — N work(s) matched” message; closes SSE |
| `error` | `partials/status_oob.html` | Error message in status panel |

OOB swaps (`hx-swap-oob`) update elements outside the SSE container:

- `#status-panel` — progress and errors
- `#results-body` — table rows (`sse-swap="work"` on tbody)
- `#result-count` — matched work count
- `#jsonl-output` — appended JSONL lines
- `#results-summary` — resolved search URL link

A hidden element listens for `start status error done` events so OOB-only fragments are processed.

### HTTP endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Main form page (`templates/index.html`) |
| `POST` | `/parse-url/fill` | Parse AO3 URL, return updated criteria partial |
| `POST` | `/scrape/start` | Create scrape job, return session partial with SSE hook |
| `GET` | `/scrape/events/{scrape_id}` | SSE stream of HTML fragments for a job |

Scrape jobs live in memory (`SCRAPE_JOBS` dict) and are removed when the job finishes. Restarting the server clears in-progress jobs.

### Templates

```
templates/
├── index.html                      # Full page: form, status panel, copy buttons
└── partials/
    ├── search_criteria.html        # Criteria fieldset (HTMX-swapped on URL paste)
    ├── scrape_session.html         # Results area + SSE connection
    ├── scrape_start.html           # OOB reset on scrape start
    ├── status_oob.html             # OOB status message
    └── work_row.html               # Table row + OOB count/jsonl append
```

HTMX and the SSE extension are loaded from CDN in `index.html` (no npm build step).

### Copy buttons

Copy buttons use a small inline script because the Clipboard API is not available through HTMX alone. The script listens for `htmx:oobAfterSwap` and `htmx:sseMessage` to enable/disable buttons and wire click handlers once rows or JSONL content exist.

### Quality score display

Works with a computed quality score show a color-coded badge in the results table:

- **Green** — raw score ≥ 14
- **Yellow** — raw score ≥ 8
- **Red** — raw score < 8

Score formula (from the Tampermonkey script):

```
eff = max(1, words / 5000)
adjusted_hits = hits / (eff ** 0.4)
raw_score = round(100 * kudos / adjusted_hits, 1)
```

Requires kudos ≥ 50 (default) to compute; otherwise shown as “—”.

### AO3 login (optional)

Username/password fields on the form, a local gitignored `.env` (`AO3_USERNAME` / `AO3_PASSWORD`), or the same env vars in the shell. Both must be set to log in. Password is kept in the in-memory job only for the duration of the scrape and is not stored server-side after completion. The form password may be left blank when `.env` provides it.

### Shared HTTP layer (`ao3kit.http`)

Scrape, tag resolve, and EPUB download share one request path. Rate limiting is
**host-wide** (not just process-wide): CLI, web UI, REST API, and the Calibre
plugin’s `ao3kit` subprocess all coordinate through ``ao3kit.rate`` /
``ao3kit.rate_store`` (SQLite at ``.cache/ao3_rate.sqlite``, override with
``AO3KIT_RATE_DB``). If the web UI is hammering AO3, the CLI waits its turn.

- **Login** — form POST with authenticity token (same flow as ao3downloader)
- **Cloudflare** — detect challenge/block HTML markers; exponential backoff retries; clear error if still blocked
- **Rate limits** — shared slot reservation before each request; honor `429` + `Retry-After` (raises the shared floor for every interface)
- **Adult gate** — append `view_adult=true` on work/search fetches so mature/explicit confirmation is skipped; EPUB download still falls back to the Proceed link if needed
- **Status callback** — optional `on_status` for live UI messages during waits/retries

Do not bypass ``ao3kit.http`` / ``wait_for_request`` with raw ``requests`` calls
from a surface — that breaks shared pacing.

