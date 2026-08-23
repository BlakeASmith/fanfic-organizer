# Changelog

User-facing changes to wranglekit (Calibre plugin) and `ao3kit` (library).

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Headings follow [Conventional Commits](https://www.conventionalcommits.org/):
`feat` → Features, `fix` → Bug Fixes, `perf` → Performance, `docs` → Documentation,
`refactor` → Refactoring, `build`/`ci` → Build. Breaking changes (`feat!` / `BREAKING CHANGE:`)
go under Breaking Changes.

Put new bullets under **[Unreleased]** in the same change that ships them.
`chore` / `test` / `style` stay out unless a user would notice.
Release command: `just release` (next 0.x minor; add `patch` and/or `publish`).

## [Unreleased]

### Documentation

- Add a features list and attribution section to the README.

## [0.26.1] - 2026-08-23

### Breaking Changes

- Store settings, cache, jobs, and the AO3 session outside the Calibre library, following the [XDG Base Directory](https://specifications.freedesktop.org/basedir/latest/) spec (`~/.config/wranglekit`, `~/.cache/wranglekit`, `~/.local/state/wranglekit`). Checkout `.ao3kit` / `.cache` trees are no longer read. Override with `AO3KIT_HOME` / `AO3KIT_CONFIG_DIR` / `AO3KIT_CACHE_DIR` / `AO3KIT_STATE_DIR`.
- Remove the deprecated web UI, REST API, templates, and `python -m ao3kit serve`. Use the Calibre plugin.

### Bug Fixes

- Fix checkout installs not finding ao3kit after Calibre loads the plugin from its zip (`dev_project.json` is read from inside that zip).
- Dev install registers the plugin as **Wranglekit** and removes a leftover **AO3 Scraper** plugin or toolbar button.

### Build

- Vendor the plugin zip from `requirements.txt`, skipping native packages Calibre already provides (`lxml`, Pillow).
- `just release` / `python makeplugin.py release` bump the next 0.x minor (or `--patch`); 1.0+ is not supported.

### Documentation

- Treat the Calibre plugin as the primary interface; keep CLI docs to a short optional note.
- Add `just load-dev`, `just build`, and `just release` wrappers around plugin install, zip, and GitHub release.
- Document checkout `install` vs the fat `wranglekit.zip` GitHub artifact, and how CI attaches that zip to a tag.
- Adopt Keep a Changelog + Conventional Commits, with `python makeplugin.py release` to cut [Unreleased] into the GitHub release body.
- State that 0.x releases are not rigorously tested (part-time open source); prefer **Stable** marks added after the fact, or accept bugs and try another release if one will not run.

## [0.26.0] - 2026-08-23

First GitHub Release of **Wranglekit**. Install `wranglekit.zip` from the release page
(Preferences → Plugins → Load plugin from file). The zip is self-contained: plugin UI,
ao3kit, and vendored Python libraries. No git checkout or `pip install` required.

If you loaded an older **AO3 Scraper** zip, remove that plugin first.

### Features

- Self-contained Calibre plugin zip (`wranglekit.zip`) bundling the Qt UI, ao3kit, and pure-Python deps.
- Search AO3, search similar, and import JSONL or zip into a Calibre library.
- Selected books: complete selected, download native EPUBs, generate covers, import the rest of a series, fill Series, simplify tags, edit collections.
- Detachable background jobs (attach logs, stop, retry).
- Tags and collections: rules, tag graph, tag purge, background tag cache.
- Generated EPUB covers (title, author, word count, fandom colour).
- CLI remains `python -m ao3kit scrape|tags|download|cover|job|config|login|rate …`.
