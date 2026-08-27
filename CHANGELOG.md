# Changelog

User-facing changes to fanfic-organizer (Calibre plugin) and `ao3kit` (library).

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Headings follow [Conventional Commits](https://www.conventionalcommits.org/):
`feat` → Features, `fix` → Bug Fixes, `perf` → Performance, `docs` → Documentation,
`refactor` → Refactoring, `build`/`ci` → Build. Breaking changes (`feat!` / `BREAKING CHANGE:`)
go under Breaking Changes.

Put new bullets under **[Unreleased]** in the same change that ships them.
`chore` / `test` / `style` stay out unless a user would notice.
Release command: `just release` (next 0.x minor; add `patch` and/or `publish`).

## [Unreleased]

### Features

- Add AO3 request pacing controls to Plugin settings (min request interval and tag-cache warm interval). Defaults are slightly slower (1.5s between requests) to reduce throttling.
- Expose advanced rate-limit settings in Plugin settings: max cruise interval, tag backoff multipliers, pressure scaling, jitter, and success-streak speed-up (`rate:` in config.yaml; CLI `config set rate.<key>`).

## [0.28.0] - 2026-08-27

### Breaking Changes

- Remove ``request_delay`` from ``config.yaml`` and drop CLI ``--delay`` on scrape, download, and tag commands. AO3 pacing is handled only by the host-wide adaptive rate limiter.

### Features

- Ship a bundled AO3 tag-cache seed (`ao3kit/data/tag_cache_seed.json`) with popular canonical fandom, relationship, character, and freeform synonym trees; merge into the XDG cache on first open (and add missing names later) so initial library simplify runs skip thousands of tag-profile fetches.
- Add `python -m ao3kit tags seed` (`build`, `import`, `stats`) for maintainers to refresh the bundled seed from AO3 tag search (sorted by uses).
- Add a curl-based installer that downloads the latest `fanfic-organizer.zip` release, installs it with `calibre-customize`, starts Calibre when it is not running, and prompts for a restart otherwise.
- Install Calibre automatically when it is missing (Linux: official isolated installer to `~/.local/opt/calibre`; macOS: Homebrew cask; Windows: winget).

### Bug Fixes

- Fix Relationships column pollution: only AO3 Relationship-category tags belong there; slash freeforms such as Hurt/Comfort and Angst stay in Tags, including when re-simplifying books that already had bad data in Relationships.
- Fix the curl installer download bundle so it does not import `ao3kit` (stdlib-only `plugin_install` helpers).
- Raise the host-wide AO3 tag-lane floor from ~0.4s to 1.0s and keep tag fetches from outrunning scrape/search pacing, reducing 429 throttling during Search and tag simplify runs.

## [0.27.0] - 2026-08-24

### Breaking Changes

- Rename the Calibre plugin and GitHub project to **Fanfic Organizer**. The release zip is `fanfic-organizer.zip`. Remove a leftover **Wranglekit** (or **AO3 Scraper**) plugin before loading the new zip.

### Bug Fixes

- Show and filter AO3 quality scores on a normalized 0–100 scale (matching the Reading Time & Quality Score userscript) instead of the raw word-adjusted ratio.
- Stop showing a spurious "Could not start background job" error when a job finishes almost instantly (for example a small offline JSONL/zip import). A clean, fast exit is now treated as success instead of a startup failure.

### Documentation

- Add a features list and attribution section to the README.
- Drop leftover root shims (`scrape_ao3.py`, …) and example files that are not part of the plugin.

## [0.26.1] - 2026-08-23

### Breaking Changes

- Store settings, cache, jobs, and the AO3 session outside the Calibre library, following the [XDG Base Directory](https://specifications.freedesktop.org/basedir/latest/) spec (`~/.config/fanfic-organizer`, `~/.cache/fanfic-organizer`, `~/.local/state/fanfic-organizer`). Checkout `.ao3kit` / `.cache` trees are no longer read. Override with `AO3KIT_HOME` / `AO3KIT_CONFIG_DIR` / `AO3KIT_CACHE_DIR` / `AO3KIT_STATE_DIR`.
- Remove the deprecated web UI, REST API, templates, and `python -m ao3kit serve`. Use the Calibre plugin.

### Bug Fixes

- Fix checkout installs not finding ao3kit after Calibre loads the plugin from its zip (`dev_project.json` is read from inside that zip).
- Dev install registers the plugin as **Fanfic Organizer** and removes a leftover **AO3 Scraper** plugin or toolbar button.

### Build

- Vendor the plugin zip from `requirements.txt`, skipping native packages Calibre already provides (`lxml`, Pillow).
- `just release` / `python makeplugin.py release` bump the next 0.x minor (or `--patch`); 1.0+ is not supported.

### Documentation

- Treat the Calibre plugin as the primary interface; keep CLI docs to a short optional note.
- Add `just load-dev`, `just build`, and `just release` wrappers around plugin install, zip, and GitHub release.
- Document checkout `install` vs the fat `fanfic-organizer.zip` GitHub artifact, and how CI attaches that zip to a tag.
- Adopt Keep a Changelog + Conventional Commits, with `python makeplugin.py release` to cut [Unreleased] into the GitHub release body.
- State that 0.x releases are not rigorously tested (part-time open source); prefer **Stable** marks added after the fact, or accept bugs and try another release if one will not run.

## [0.26.0] - 2026-08-23

First GitHub Release of **Fanfic Organizer**. Install `fanfic-organizer.zip` from the release page
(Preferences → Plugins → Load plugin from file). The zip is self-contained: plugin UI,
ao3kit, and vendored Python libraries. No git checkout or `pip install` required.

If you loaded an older **AO3 Scraper** zip, remove that plugin first.

### Features

- Self-contained Calibre plugin zip (`fanfic-organizer.zip`) bundling the Qt UI, ao3kit, and pure-Python deps.
- Search AO3, search similar, and import JSONL or zip into a Calibre library.
- Selected books: complete selected, download native EPUBs, generate covers, import the rest of a series, fill Series, simplify tags, edit collections.
- Detachable background jobs (attach logs, stop, retry).
- Tags and collections: rules, tag graph, tag purge, background tag cache.
- Generated EPUB covers (title, author, word count, fandom colour).
- CLI remains `python -m ao3kit scrape|tags|download|cover|job|config|login|rate …`.
