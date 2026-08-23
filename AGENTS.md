# AO3 downloader metadata scraper

Making this for my wife. She is using multiple different tools and having to do a lot of manual
work to manage fan-fiction and metadata cleaning etc.

**Interfaces:** CLI (`python -m ao3kit …`) and the Calibre plugin — keep them in [relative feature parity](#interface-parity). The web UI and REST API are [deprecated and frozen](#deprecated-web-ui-and-rest-api).

## Resources

- [Ao3 downloader](https://github.com/nianeyna/ao3downloader)
- [FanFicFare](https://github.com/jimmxinu/fanficfare)

## Project layout

```
ao3kit/                 # Application package
  http.py / rate.py / rate_store.py / scrape.py / series.py / similar.py / epubs.py
  covers.py # generated EPUB covers (title/author, fandom-coloured)
  jobs.py / proc.py     # detachable background jobs (pid, log, start/stop)
  webapp.py / api.py     # deprecated frozen web UI + REST API
  config.py             # User settings + rule file storage (.ao3kit/)
  tags/                 # Tag profiles, resolver, SQLite cache, code-first rules
  cli.py                # Unified CLI (python -m ao3kit …)
.ao3kit/                # User config home (gitignored; AO3KIT_HOME override)
                          # config.yaml, mappings.yaml, collections.yaml, ao3_session.json, rules/*.py
.cache/                 # Tag cache + host-wide rate limiter DB + jobs/ (gitignored)
templates/              # Deprecated Jinja2 templates for the frozen web UI
calibre-plugin/         # Calibre plugin (search/scrape via ao3kit + JSONL/zip import)
calibre_dev/            # Dev: lock-aware Calibre restart + FastMCP server
makeplugin.py           # Zip (self-contained release) / install plugin; optional --restart
.cursor/mcp.json        # calibre-dev MCP (stdio)
```

Root `scrape_ao3.py`, `tag_metadata.py`, `download_epubs.py`, `web.py`, `ao3_http.py`,
and `ao3_rate.py` are thin compatibility shims. Prefer importing from `ao3kit` and
running `python -m ao3kit …`.

## Interface parity

The CLI and Calibre plugin share one core. Prefer implementing behavior in `ao3kit` library modules, then wiring those two surfaces. Cursor agents: see `.cursor/rules/interface-parity.mdc`.

| Surface | Entry | Owns |
|---|---|---|
| CLI | `python -m ao3kit …` | Full toolkit (scrape, tags, download, job, config, login, rate) |
| Calibre plugin | `calibre-plugin/` | Search/scrape AO3 via ao3kit subprocess; import JSONL/zip; import series; simplify selected tags, fandoms, and relationships; edit collections of selected books (which rules, pins, excludes); recompute collections from rules (hand-adds become per-work pins); **Running jobs** (attach logs / stop / retry / delete / clear / background); background tag cache; tag graph; collections & tag rules; Tag Purge of rare Tags-column values; generated EPUB covers |
| Web UI (deprecated) | `ao3kit.webapp` `/` | Frozen HTMX scrape/download/settings — do not extend |
| REST API (deprecated) | `ao3kit.api` `/api/v1` | Frozen JSON jobs — do not extend |

**Relative parity** means the same capabilities and data contracts on the **supported** surfaces, not the same UX:

- Put new logic in `scrape` / `tags` / `epubs` / `config` / `http` / `rate` / `jobs`, not only in one UI.
- When adding a user-facing feature, update CLI and the plugin where it applies. Do **not** add matching web UI controls or REST endpoints.
- Work record JSON, `cleaned` enrich shape, and `ao3-import.zip` layout are shared contracts — do not diverge them per surface.
- AO3 traffic must use `ao3kit.http` so host-wide rate limiting (`.cache/ao3_rate.sqlite`) applies across every interface, including the frozen web/API if someone still runs `serve`.
- OK: CLI stdout vs Qt dialogs. Not OK: a scrape filter or tag resolve path that exists on only one of CLI / plugin.

## Calibre plugin (new library)

End users install **wranglekit.zip** from GitHub Releases (Preferences → Plugins → Load plugin from file). The plugin shows up in Calibre as **Wranglekit**. That zip includes the Qt UI, `ao3kit/`, and vendored pure-Python deps (`requests`, PyYAML, …). Calibre already has `lxml`. Jobs run with `calibre-debug -e run_ao3kit.py` so there is no separate Python or git checkout. Config/cache for that bundled copy live next to the extracted runtime under Calibre’s plugins folder (`.ao3kit` / `.cache` are kept across upgrades).

A git checkout still wins when `ao3kit_project` is set or `~/emily/ao3` exists — that is the dev path. `python makeplugin.py install` stays `calibre-customize -b` (plugin UI only, shells out to the checkout). `python makeplugin.py zip` is the fat release artifact.

**Always reinstall after every change.** When you finish work in this repo, run `python makeplugin.py install` before you stop — even if you only touched `ao3kit/` (the plugin shells out to that checkout) or docs. Calibre keeps the old plugin zip until you install again. Tell the user to **restart Calibre** so it loads the new zip. Do not wait for them to ask.

### Plugin reinstall and Calibre restart (dev)

Installing and restarting are separate. **Default is install only.** Do not quit Calibre on every change — that fights other agents and dumps unsaved GUI state. Restart only when this session needs the GUI to load the new zip *now* (plugin UI iteration).

| Command | What it does |
|---|---|
| `python makeplugin.py zip` | Self-contained `wranglekit.zip` (plugin + ao3kit + vendor) for GitHub Releases. |
| `python makeplugin.py install` | `calibre-customize -b` only. Then tell the user to restart. |
| `python makeplugin.py install --restart` | Install, then quit and start Calibre. |
| `python makeplugin.py restart` | Quit and start Calibre (no install). |
| `python makeplugin.py status` | GUI running? Restart lock held? |

`--restart` / `restart` take a **host-wide lock** (`.cache/calibre_restart.lock`, override `AO3KIT_CALIBRE_LOCK`). Only one agent can force-restart at a time. Pass `--agent-id <short-name>` so the holder is visible. CLI waits up to `--lock-timeout` seconds (default 15); if still busy, it exits without killing Calibre.

**Prefer the `calibre-dev` FastMCP tools** over `killall` / `osascript` / `open -a calibre`. Project config is `.cursor/mcp.json` (`python3 -m calibre_dev`). Tools:

| Tool | When |
|---|---|
| `install_plugin` | After repo changes. `restart` defaults to **false**. |
| `restart_calibre` | Destructive. Only for in-session GUI reload. |
| `calibre_status` | Check running / lock before touching the GUI. |

MCP restart uses `lock_timeout=0` by default: if another agent holds the lock, the tool returns `error: locked` — **skip**, do not kill Calibre yourself. Optional shared coordinator (one process for every agent, still uses the same file lock): `python -m calibre_dev --http --port 8765`.

The plugin is for a **new** Calibre library that should look like the existing FanFicFare fanfic library. Do not run Search AO3 / Import / Download EPUB / Import series / Fill series / Simplify / Tag Purge / “create columns” against a library you want left untouched — those actions write to whichever library is currently open. Installing the plugin does not modify a library by itself.

The toolbar button opens the plugin menu. The menu is grouped:

- **Search AO3 and import…** / **Search similar…** / **Import JSONL or zip…**
- **Selected books** — **Complete selected** (series + missing EPUBs + simplify tags), download EPUB, generate covers, import rest of series, fill series, simplify tags, edit / recompute / add collections (disabled when nothing is selected; the label shows the selection count)
- **Running jobs…**
- **Tags and collections** — Collections & tag rules, tag graph, tag purge, warm / log / stop tag cache
- **Plugin settings…**

**Search similar** (menu; needs a selection) builds an AO3 search from the selected library book(s). Fandoms, authors, relationships, characters, and additional tags are merged across the selection and shown in dropdowns so you can add a fandom plus a ship or two. AO3 ANDs every selected tag — pick a few. Then Search and import runs the same scrape/download path as Search AO3. Work id is optional; FanFicFare `#characters` is used when that column exists.

**Search AO3 and import** (menu) uses the same scrape criteria and filters as the CLI. It starts a detachable **background job** (`python -m ao3kit job start`) whose steps are `scrape` (with `--download` when EPUBs are requested) and optional `tags enrich`. Search, native EPUB download, and simplify share one job, one login, and the host-wide rate limiter. Matched works appear in the library **as soon as they are scraped**; native EPUBs attach to those rows as each file finishes downloading. Simplify still runs after the scrape (and rewrites tags on the books already added). The log window is an attach view: **Background** (or closing the window) detaches without stopping work; **Cancel** stops the process; **Retry** on a failed or stopped job re-runs it from the start (existing EPUBs and cached tags are skipped). If the log is closed, the completion popup also has **Retry**. **Running jobs…** lists every job (including the tag-cache warmer and the live **tag graph viewer**) so you can reopen the log, stop it, retry a failure, delete selected jobs (Shift-click or ⌘-click for more than one), or clear finished / failed jobs from the list (the library is unchanged). Paste a works-search URL or an AO3 **series** URL (`/series/ID`), or fill the form; **Fill from URL** runs `scrape --parse-only` (no network scrape) to populate fields. A series URL imports every work on that series page. JSONL/zip import remains available for files produced outside Calibre. Download, import-series, fill-series, simplify, and recompute collections use the same job wrapper.

**Complete selected** (Selected books) runs the usual selected-book work in one job: fill Series, import the rest of each AO3 series, download missing native EPUBs, simplify tags/fandoms/relationships, and recompute collections. Existing library EPUBs are copied into the job so AO3 is not hit again for them. New series-mates appear in the library as they are found. Same steps as `scrape --series-from --download` then `tags enrich`.

**Download EPUB** (Selected books) downloads the native AO3 EPUB for selected library books that have an AO3 work id / URL and do not already have an EPUB format. It runs as a background job whose step is `python -m ao3kit download` (same host-wide limiter) and attaches each file with Calibre `add_format` as soon as that download finishes, so earlier books are readable in the library while later ones are still fetching. Books that already have an EPUB are skipped; existing files are never replaced. Metadata and tags are left unchanged. Generated covers follow plugin settings / `.ao3kit` `cover.enabled` (on by default).

**Generate covers** (Selected books) stamps an auto-generated cover (title, author, word count, and quality score on a fandom-coloured field, like [add-cover-to-ao3-files](https://github.com/alexwlchan/add-cover-to-ao3-files)) into each selected EPUB and sets the Calibre thumbnail. Books without an EPUB still get a Calibre cover from library metadata. Style (fields, colours, font, size) is in Plugin settings → Cover style and `python -m ao3kit config set cover.<key>`.

**Import rest of series** (Selected books) looks up the AO3 series for the selection (from stored series metadata, the `ao3series` identifier, or a work-page fetch) and imports every other part. It runs as a background job whose step is `python -m ao3kit scrape --series-from`. New parts show in the library as they are found; EPUBs attach as they download. Existing books are updated with series metadata; existing EPUBs are left unchanged. Native EPUB download and tag simplify follow plugin settings.

**Fill series** (Selected books) looks up AO3 series membership for books already in the library and writes Calibre’s Series field, series index, and `ao3series`. It runs as a background job whose step is `python -m ao3kit scrape --fill-series-from`. Other works in the series are not imported. Tags and EPUBs are left unchanged. Books that are not in a series are left as they are. Works that already have a complete series id + name + part number are skipped on AO3 (no extra fetch).

**Plugin settings** hold optional AO3 login and import defaults (download native EPUBs, generate covers, simplify tags/fandoms/relationships, update existing books, **always import the rest of the series**). Cover style (which lines to show, fandom colour overrides, font, size) is on that page. When that series option is on, Search AO3, Search similar, a series URL, and JSONL/zip import also fetch every other work in the same series (`scrape --include-series` / `--series-from`). Search filters apply only to the original matches; series-mates are added in full. **Test login** on that page checks the username and password against AO3 (via `python -m ao3kit login`) without saving yet. Leave login blank for anonymous access or to use the ao3kit project `.env`. Request pacing is the host-wide rate limiter, not a plugin delay setting. Search / Import dialogs still let you override download, simplify, and update-existing for that run. Download native EPUBs and generated covers are on by default; simplification and full-series auto-import stay off unless enabled in settings (or simplify is checked on the dialog).

On first import into an empty library (or via the settings checkbox) it creates and fills:

| Lookup | Name | Source |
|---|---|---|
| `#fandom` | Fandom | cleaned fandoms (canonical + metatags) |
| `#relationships` | Relationships | cleaned AO3 Relationship tags |
| `#collections` | Collections | computed from collection rules |
| `#originaltags` | Original Tags | pre-clean AO3 tags |
| `#wordcount` | word count | AO3 word count |
| Series | Series | first AO3 series name (built-in Calibre field) |
| series index | | AO3 part number |
| Tags | Tags | remaining cleaned tags + `Completed` |
| identifiers | `url`, `ao3`, `ao3series` | work URL, work id, first series id |

Count Pages columns (page count / readability) are not created. There is no separate cleaned-tags custom column — Calibre's standard Tags field is the cleaned set. Series is always written from work metadata when present, even if auto-import of the rest of the series is off. JSONL/zip extra parts are metadata (EPUBs already in the zip stay attached); Search/series-URL with download fetches EPUBs for extra parts too.

After **Import** (with simplify) or **Simplify tags, fandoms & relationships for selected books**, the plugin prints a unique `before → after` remapping summary in the progress log and in the completion dialog (Show details). The same AO3 synonym collapse and user tag rules run on Tags, Fandom, and Relationships. Fandom metatags (e.g. Marvel) are appended to Fandom only.

**Edit collections** (Selected books, and a button on **Collections & tag rules**) lists each selected book and collection, shows which rules put it there or keep it out, and lets you Always / Never pin, add a book to an existing or new collection (even when no rule matches, or it already belongs to a different one), keep an unexplained membership as a pin, edit or turn off shared rules, or write the computed set back to Calibre. It does not fetch AO3 or change tags. **Recompute collections** and **Add to a collection** remain shortcuts under Selected books. **Collections & tag rules** has two tabs. **Collections** are computed from rules (tag contains / is exactly, fandom, author, or a single AO3 work). Recompute and add use those rules only — they do not fetch AO3 or run tag keep/rename/drop. Recompute replaces the Collections column. Adding a book to a collection by hand — in Calibre, or **Add selected books to a collection** — is saved as a per-work pin so the next recompute puts it back. Use a **Never** rule to keep matching books out; removing a collection in Calibre alone does not stick. Plugin settings can turn off remembering hand-adds (`collections_remember_manual_adds`). **Tag rules** keep / rename / remove tags. Uncheck **On** to ignore a rule; double-click a row to edit. **Try a tag** shows AO3’s usual name plus tag rules. Older mapping rows that named a collection still apply on Simplify until you move them to the Collections tab.

**Warm tag cache** (Tags and collections) collects unique tags / fandoms / ships / characters from the **whole open library**, writes them to `.cache/tag_warm_names.txt`, and shells out to `python -m ao3kit tags warm start`. The daemon fetches uncached AO3 mappings slowly so Search / Download / Simplify can still run; it does not write Calibre metadata. If a warmer is already running, this updates the names file and the daemon picks it up on the next poll. It shows up as job id `warm` in **Running jobs…**. **Tag cache log** attaches to `.cache/tag_warm.log` (same as `python -m ao3kit tags warm log --follow` or `python -m ao3kit job attach warm`). **Stop tag cache** sends `tags warm stop` (or `job stop warm`) and shows which tags were cached this run (Show details for the full list).

**Tag graph** (Tags and collections) starts a long-running **Tag graph viewer** job (`python -m ao3kit tags graph serve`, job id `graph` in **Running jobs…**). If more than one book is selected, only those books seed the dump; otherwise it uses the whole open library. Each book is a work node linked to all of its tags, fandoms, ships, and characters; tags that share a work are connected through that work. Synonym and metatag links from the cache are included. Uncached names show as missing. Layout is a stable packing (no heavy force simulation). Default grouping is fandom; the viewer can switch to largest fandom, **crossovers (mixed franchises)** (related tags like Doctor Who / Doctor Who (2005) count as one), relationship, or one group, then optionally settle the focused neighborhood. Shared tags sit between groups. Warm the tag cache first for a fuller picture. **Find similar** on a work or a tag opens the same search-tuning controls as the plugin Search similar dialog. Works start with fandoms pre-checked; tags start with that tag checked (optional authors, ships, characters, tags, excludes, extra query, sort, complete, max results). It queues that scrape/import using plugin download/simplify defaults; new works are written into the library as they scrape and the viewer reloads from `.cache/tag_graph_works.jsonl`. Refresh the page after viewer code changes. `python -m ao3kit tags graph serve` is the same server if you start it yourself.

**Tag purge** (Tags and collections) lists Tags-column values that appear on at most *N* works **in the whole library** (default *N* = 1). The opening list can be *seeded* from the current selection or currently shown books; the count beside each tag is still library-wide, and **Purge removes those tags from every book in the open library**. A **Filter tags** box fuzzy-matches tag names (comma-separated terms are OR). The dialog states that purge is library-wide. **Show tags** refreshes the checklist after changing the max-works seed. Fandom / Relationships / Collections / Original Tags are unchanged.

## Setup

```bash
pip install -r requirements.txt
```

## CLI

Unified entry point:

```bash
python -m ao3kit scrape --url "https://archiveofourown.org/works?..." -o results.jsonl
python -m ao3kit scrape --url "https://archiveofourown.org/works?..." -o results.jsonl --download
python -m ao3kit scrape --url "https://archiveofourown.org/works?..." -o results.jsonl --include-series
python -m ao3kit scrape --url "https://archiveofourown.org/series/6133236" -o series.jsonl
python -m ao3kit scrape --series-id 6133236 -o series.jsonl --download
python -m ao3kit scrape --series-from results.jsonl -o series.jsonl
python -m ao3kit scrape --fill-series-from results.jsonl -o filled.jsonl
python -m ao3kit scrape --parse-only --url "https://archiveofourown.org/works?..."
python -m ao3kit scrape --parse-only --url "https://archiveofourown.org/series/6133236"
python -m ao3kit scrape --parse-similar --similar-from results.jsonl
python -m ao3kit scrape --similar-from results.jsonl --similar-work-id 50448730 --similar-include relationships -o similar.jsonl
python -m ao3kit scrape --criteria-file example_criteria.json -o results.jsonl --verbose
python -m ao3kit tags tag "Doctor Who (2005)"
python -m ao3kit tags resolve --jsonl results.jsonl --work-id 50448730 --verbose
python -m ao3kit tags warm start --jsonl results.jsonl
python -m ao3kit tags warm status
python -m ao3kit tags warm log
python -m ao3kit tags warm log --follow
python -m ao3kit tags warm stop
python -m ao3kit tags graph --names-file tags.txt -o tag-graph.html --open
python -m ao3kit tags graph --jsonl results.jsonl -o tag-graph.html
python -m ao3kit tags graph serve
python -m ao3kit tags graph reload
python -m ao3kit job start --title "Search" -- scrape -o results.jsonl --verbose
python -m ao3kit job start -- scrape -o results.jsonl --and tags enrich --jsonl results.jsonl -o cleaned.jsonl
python -m ao3kit job list
python -m ao3kit job status
python -m ao3kit job log <id> --follow
python -m ao3kit job attach <id>
python -m ao3kit job stop <id>
python -m ao3kit job retry <id>
python -m ao3kit job delete <id>
python -m ao3kit job clear --finished
python -m ao3kit download -i results.jsonl -o epubs/
python -m ao3kit download -i results.jsonl -o epubs/ --no-cover
python -m ao3kit cover epubs/50448730.epub
python -m ao3kit cover --dir epubs/
python -m ao3kit cover --jsonl results.jsonl --dir epubs/
python -m ao3kit cover preview --title "Ship Happens" --author "Ann Thology" --fandom "Star Wars" -o cover.png
python -m ao3kit config set cover.enabled true
python -m ao3kit config set cover.fields title,author,wordcount,score,fandom
python -m ao3kit config init
python -m ao3kit config collections add --match mentions --values "River Song" --collection "River Song"
python -m ao3kit config collections pin --work-id 50448730 --collection Jegulus
python -m ao3kit config collections unpin --work-id 50448730 --collection Jegulus
python -m ao3kit config mappings add --values Jegulus --action keep_separate --stop
python -m ao3kit config mappings add --values "Melody Pond" --action map_to --map-to "River Song"
python -m ao3kit config mappings list
python -m ao3kit config mappings preview "Jegulus"
python -m ao3kit tags collections --jsonl results.jsonl -o cleaned.jsonl
python -m ao3kit tags collections --jsonl results.jsonl -o explain.json --explain
python -m ao3kit login --username YOUR_NAME --password YOUR_PASSWORD
python -m ao3kit rate
python -m ao3kit rate --hours 24
python -m ao3kit rate export -o rate-events.jsonl
python -m ao3kit rate export --hourly --days 30 -o rate-hourly.jsonl
```

Optional AO3 login via `--username` / `--password`, or a local gitignored `.env` file:

```bash
cp .env.example .env   # then edit AO3_USERNAME / AO3_PASSWORD
```

Env vars `AO3_USERNAME` / `AO3_PASSWORD` are loaded automatically by `ao3kit.http`. Test them with `python -m ao3kit login` (or the plugin settings **Test login** button).

### Background jobs

Long-running CLI and plugin work is a **detached process plus a log**. Attach is only a tail; detaching does not stop the work.

Layout (under `.cache/jobs/<id>/`, override with `AO3KIT_JOBS_DIR`):

| File | Role |
|---|---|
| `spec.json` | Title, kind, argv **steps**, **result** (how to compute the return value), optional plugin ingest hints |
| `status.json` | pid, running, last log line, exit code, ingest state, **result** (one-line outcome), retry count |
| `job.pid` / `job.log` | Worker pid and combined stdout/stderr |
| `work/` | JSONL, EPUBs, criteria files for that run |

```bash
python -m ao3kit job start --title "Search" --kind scrape -- scrape -o results.jsonl --verbose
python -m ao3kit job start --dir .cache/jobs/<id>          # plugin: spec.json already written
python -m ao3kit job start --result-json '{"source":"jsonl_count","path":"results.jsonl","label":"work"}' -- scrape -o results.jsonl
python -m ao3kit job list
python -m ao3kit job status <id>
python -m ao3kit job log <id> --follow
python -m ao3kit job attach <id>                           # alias of log --follow
python -m ao3kit job stop <id>
python -m ao3kit job retry <id>                            # re-run from spec; keeps work/ and log
python -m ao3kit job delete <id>
python -m ao3kit job clear --finished
python -m ao3kit job clear --failed --stopped
python -m ao3kit job clear --inactive                      # finished + failed + stopped
```

Separate steps with `--and`. JSON is on stdout; a one-line summary is on stderr. Each job declares its own **result** in `spec.result` (`jsonl_count`, `log_match`, `json_field`, or `last_log`). The worker writes that to `status.result`; `job list` / **Running jobs…** show it in a Result column. After Calibre ingest the plugin replaces the result with the library summary (imported N books, simplified tags, …). `tags warm` stays a named singleton (id `warm`) and appears in `job list` for the default jobs dir. The Calibre plugin starts every search / download / simplify / series / collections run this way, shows the log while you are attached, and **Running jobs…** lists them all. Failed or stopped jobs can be retried from that list, the log window, or the completion popup if the log is closed (**Retry**): the same steps run again from the start; `work/` (EPUBs, JSONL) and the tag cache are kept, so downloads skip files already on disk. Existing library EPUBs are not replaced. **Delete** / **Clear finished** / **Clear failed** (and `job delete` / `job clear`) remove job directories from the list only; they do not change the Calibre library. Search and series import write books into the library **while the job is still running** (metadata as each work is scraped, EPUB as each file lands). A final ingest after the process exits (`status.ingest: pending`) applies simplified tags and catches anything missed. Closing Calibre before that final ingest is safe: the next plugin launch picks up pending ingest from disk. The frozen web UI / REST API still use their own in-memory jobs and are not this store.

### EPUB covers

Native AO3 EPUBs have no cover image. `ao3kit.covers` generates one (title, author, word count, and quality score on a dark fandom-coloured gradient, same idea as [add-cover-to-ao3-files](https://github.com/alexwlchan/add-cover-to-ao3-files)) and stamps it into the EPUB OPF. Same-fandom works get the same colour so a grid view groups them. Style lives in `.ao3kit/config.yaml` under `cover:` (fields, font, size, palette, per-fandom colour map). Download/scrape use `cover.enabled` (default on); pass `--no-cover` to skip. The Calibre plugin settings page has the same knobs plus **Selected books → Generate covers**.

```bash
python -m ao3kit config set cover.enabled true
python -m ao3kit config set cover.fields title,author,wordcount,score,fandom
python -m ao3kit config set cover.color_seed fandom
python -m ao3kit config set cover.fandom_colors "Star Wars = #c41e3a"
python -m ao3kit cover --dir epubs/
```

### Series

Work JSONL may include a `series` array (omitted when empty). Each entry is `{ "series_id", "name", "url", "position" }` from the blurb (`ul.series`) or work page. Calibre uses the first membership for the built-in Series field and `series_index`, and stores the id as identifier `ao3series`.

| Flag | Purpose |
|---|---|
| `--url …/series/ID` or `--series-id ID` | Scrape every work on that series page |
| `--include-series` | After a filtered search, also fetch every other part of each matched work’s series (mates are unfiltered) |
| `--series-from JSONL` | Expand seed works to all series-mates (fetches work pages when `series` is missing) |
| `--fill-series-from JSONL` | Look up series membership on seed works only (no extra parts) |

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

Resolve a work’s tags (or ad-hoc names) onto the canonical set via `TagResolver` / the `resolve` subcommand. Synonyms collapse to their canonical; unmarked tags are kept unless `--drop-unmarked`. **Fandoms** and **relationships** get the same treatment during `tags enrich` (and the plugin Simplify checkbox / selected-books action): AO3 synonym collapse plus user keep/rename/drop rules. Relationship tags that AO3 classifies as Relationship are stored on `cleaned.relationships` (Calibre `#relationships`) and omitted from the Tags column. After that, **fandom metatags** listed on each kept **fandom** tag’s AO3 profile (the Metatags tree, e.g. Marvel on [Spider-Man - All Media Types](https://archiveofourown.org/tags/Spider-Man%20-%20All%20Media%20Types)) are **appended to the fandom list** (Calibre `#fandom`) if they are not already present. Character and freeform metatags are ignored; nothing is added to the main Tags column. Disable fandom metatags with `python -m ao3kit config set include_metatags false` or `--no-metatags`. Skip fandom or relationship passes with `tags enrich --no-fandoms` / `--no-relationships`.

**Caching strategy** (SQLite; avoids repeat AO3 hits):

1. Raw tag miss → fetch that tag’s profile.
2. If it’s a synonym, follow the merger link and fetch the **canonical** page once.
3. Index every synonym listed on the canonical page as one *tree* (shared `root` + `fetched_at`) → later raw forms resolve with **zero** fetches.
4. Persist to `.cache/ao3_tag_cache.sqlite` (gitignored; legacy `.json` is imported once on open).
5. Trees older than `tag_cache_ttl_days` (default **90**; `0` = never) are purged automatically so wrangling stays fresh.
6. **Background warming** (`tags warm start`) detaches a slow daemon that re-scans JSONL / names files for uncached tags and fetches them through `TagResolver` (host-wide limiter plus `tag_warm_interval`, default 10s extra between fetches). It does not raise the shared scrape/download interval. Idle-exits after a few empty polls. Calibre **Warm tag cache…** dumps library tags to a names file and runs the same command.
7. **Tag graph** (`tags graph`) reads the cache (no AO3 fetch) and writes an HTML / JSON / DOT view of works plus synonym and metatag links. `--jsonl` places each work as a work node linked to its tags. Positions are precomputed (fandom clusters, bridge tags in the middle); the viewer can regroup by largest fandom, mixed-franchise crossovers, ships, or one group, and optionally settle the focused neighborhood. Calibre **Tag graph…** dumps JSONL, starts a `tags graph serve` job (id `graph`), and opens the live viewer. **Find similar** on a work or a tag lets you pick fandoms, ships, characters, tags, excludes, extra query, and filters, then queues a scrape/import; the dump updates as Calibre indexes matches. Clicking a tag starts the search with that tag. `python -m ao3kit tags graph serve` is the same server for UI iteration; **Reload data** rebuilds from the JSONL dump.

```bash
python -m ao3kit tags resolve "wolfstar" "Kisses" "Slow Burn"
python -m ao3kit tags resolve --jsonl results.jsonl --work-id 50448730 --verbose
python -m ao3kit tags resolve --jsonl results.jsonl --work-id 50448730 --drop-unmarked --include-fandoms --include-relationships
python -m ao3kit tags resolve "Kisses" --cache .cache/ao3_tag_cache.sqlite --verbose
python -m ao3kit tags resolve "Kisses" --cache-ttl-days 30 --verbose
python -m ao3kit tags resolve "Kisses" --no-cache
python -m ao3kit config set tag_cache_ttl_days 90
python -m ao3kit config set tag_warm_interval 15
python -m ao3kit config set include_metatags true
python -m ao3kit tags apply --rules example_tag_rules.py \
  "Jegulus" "Melody Pond" "River Song - Freeform" "Fluff"
python -m ao3kit tags apply --rules example_tag_rules.py \
  --jsonl results.jsonl --work-id 50448730 --verbose
python -m ao3kit tags warm start --jsonl results.jsonl
python -m ao3kit tags warm start --names-file tags.txt --foreground
python -m ao3kit tags warm status
python -m ao3kit tags warm log --follow
python -m ao3kit tags warm stop
python -m ao3kit tags graph --names-file tags.txt -o tag-graph.html --open
python -m ao3kit tags graph --jsonl results.jsonl -o tag-graph.html
python -m ao3kit tags graph serve
python -m ao3kit tags graph reload
```

### User config & rules storage

User settings and rule modules live in **`.ao3kit/`** (project-local; override with
`AO3KIT_HOME`). Gitignored.

```
.ao3kit/
  config.yaml           # delay, resolve flags, active_rules, …
  mappings.yaml         # extra keep / rename / drop rows
  collections.yaml      # collection membership rules + per-work pins
  ao3_session.json      # cached AO3 cookies (not the password)
  rules/
    default.py          # active code-first rules (editable)
```

```bash
python -m ao3kit config init
python -m ao3kit config show
python -m ao3kit config set request_delay 6
python -m ao3kit config set collections_remember_manual_adds true
python -m ao3kit config collections list
python -m ao3kit config collections add --match mentions --values "River Song" --collection "River Song"
python -m ao3kit config collections add --match fandom_mentions --values "The Pitt" --collection "The Pitt"
python -m ao3kit config collections add --match author_ci --values avocadomoon --collection Avocado
python -m ao3kit config collections pin --work-id 50448730 --collection Jegulus
python -m ao3kit config collections unpin --work-id 50448730 --collection Jegulus
python -m ao3kit config mappings list
python -m ao3kit config mappings add --values Jegulus --action keep_separate --stop
python -m ao3kit config mappings add --match tag_ci --values "Melody Pond" --action map_to --map-to "River Song"
python -m ao3kit config mappings preview "Jegulus"
python -m ao3kit config rules list
python -m ao3kit config rules new river_song
python -m ao3kit config rules use river_song
python -m ao3kit config rules install-example
python -m ao3kit tags apply "Jegulus" "Fluff"   # canonical + mappings.yaml + active Python rules
python -m ao3kit tags apply --collections-only "Melody Pond" "Fluff"
python -m ao3kit tags apply --collections-only --jsonl results.jsonl
python -m ao3kit tags enrich --jsonl results.jsonl -o cleaned.jsonl
python -m ao3kit tags collections --jsonl results.jsonl -o cleaned.jsonl
python -m ao3kit tags collections --jsonl results.jsonl -o explain.json --explain
```

`tags enrich` (and `download`) print a unique remapping summary when finished (tags, fandoms, and relationships). `tags collections` overlays `.ao3kit/collections.yaml` on tags already on the work — it does **not** fetch AO3 or run keep/rename/drop. Hand-added Calibre memberships can be saved as per-work pins. `--explain` writes JSON describing which rules apply to each work (no pin capture, no overlay write).

Edit collection rules in the Calibre plugin (**Edit collections of selected books…** or **Collections & tag rules…**) or with `python -m ao3kit config collections`. Tag keep / rename / drop stays in **Tag rules** / `python -m ao3kit config mappings`. Python rule modules remain for custom logic the table cannot express.

### Collection membership (computed)

Collections on a work are a **view of rules**, not a separate stored truth. Recompute anytime (`tags collections` or the plugin action). That path does not fetch AO3 or simplify tags. Membership is **includes** (YAML rules, plus leftover mapping/Python collections already stored on the work) **minus excludes**. The plugin **Edit collections of selected books** dialog (and `tags collections --explain`) shows which rules produced each membership.

| When | Then |
|---|---|
| tag contains | Any tag/fandom/ship/character name already on the work includes this text |
| tag is exactly | Exact tag (or AO3’s usual name) |
| fandom contains | Fandom name includes this text |
| author is | Exact author name |
| this AO3 work / this book | Per-work pin (`work_id` or Calibre UUID) |

A collection you add by hand on one book becomes an include pin for that work (unless `collections_remember_manual_adds` is false). Manual removals do **not** become exclude pins — add a **Never** rule if you want a book kept out.

### Extra tag mappings (on top of canonical)

AO3 wrangling collapses synonyms onto the canonical tag first. Then `.ao3kit/mappings.yaml` rows run (default priority 1000, list order among themselves), then the active Python rule module.

| When | Then |
|---|---|
| contains | Tag name includes this text, or AO3’s usual name is this |
| is exactly | Exact tag name or exact AO3 usual name |

First mapping that sets keep / rename / drop wins. `stop` skips later mappings and Python rules for that tag. Mapping rows that still list collections are unioned into the computed set.

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

## Deprecated: web UI and REST API

`ao3kit.webapp`, `ao3kit.api`, `templates/`, and `python -m ao3kit serve` still exist but are **frozen**. Do not add features, endpoints, templates, or docs to keep them current. Use the CLI or the Calibre plugin.

`serve` prints a deprecation warning. HTML pages show a banner. API responses send a `Deprecation: true` header. Existing tests under `tests/test_api.py` freeze current behavior; do not add new API/web coverage unless fixing a bug in those frozen surfaces.

## Shared HTTP layer (`ao3kit.http`)

Scrape, tag resolve, and EPUB download share one request path. Rate limiting is
**host-wide** (not just process-wide): CLI, the Calibre plugin’s `ao3kit`
subprocess, and the frozen web UI / REST API all coordinate through ``ao3kit.rate`` /
``ao3kit.rate_store`` (SQLite at ``.cache/ao3_rate.sqlite``, override with
``AO3KIT_RATE_DB``). Concurrent processes on the same host wait their turn.

- **Login** — form POST with authenticity token (same flow as ao3downloader). Scrape and EPUB download log in immediately when credentials are set, then cookies are saved to ``.ao3kit/ao3_session.json`` (password is not stored). Later CLI/plugin/web processes reuse that session and skip the login GET+POST until cookies expire or AO3 returns a logged-out page. ``python -m ao3kit login`` / Test login always hit AO3, then refresh the cache. Disable with ``AO3KIT_SESSION_CACHE=0``. Login uses its own ~1s interval and a 20s request timeout so a hung login page retries instead of sitting on “Logging in to AO3…” for 60s. Tag lookups stay anonymous (and skip the network entirely on cache hits) until AO3 returns a login wall, unless a saved session for the same username is restored.
- **Cloudflare** — detect challenge/block HTML markers; exponential backoff retries; clear error if still blocked
- **Rate limits** — shared slot reservation before each request; honor `429` + `Retry-After` as a host-wide cooldown (every interface waits; cruise intervals stay at ``request_delay`` / the tag lane). Work pages, search listings, and EPUB downloads use config ``request_delay`` (default 1.5s). Tag profiles stay on the adaptive ~0.4s lane.
- **Request log** — each attempt is stored in the same SQLite file (`rate_events`: kind, status, wait, claimed interval, limiter snapshot, Retry-After). Raw events are kept 30 days (cap 50k). Hourly rollups (`rate_hourly`) are kept 180 days for tuning. Inspect with `python -m ao3kit rate` or `python -m ao3kit rate export --hourly`. Query strings are not stored. The report includes 24h/7d 429 rates, interval-vs-429 buckets, and pacing hints (hints do not change intervals automatically).
- **Adult gate** — append `view_adult=true` on work/search fetches so mature/explicit confirmation is skipped; EPUB download still falls back to the Proceed link if needed
- **Status callback** — optional `on_status` for live UI messages during waits/retries

Do not bypass ``ao3kit.http`` / ``wait_for_request`` with raw ``requests`` calls
from a surface — that breaks shared pacing.

