# fanfic-organizer

A **Calibre plugin** for scraping AO3 search results, wrangling tags, downloading native EPUBs, and cleaning metadata. **`ao3kit`** is the Python library the plugin runs.

Unofficial; not affiliated with the Organization for Transformative Works, Archive of Our Own, FanFicFare, or Calibre.

## Pre-1.0

This project will stay below 1.0 for a long time. **0.x releases are not rigorously tested.** It is open source and maintained part-time, so there is no capacity to fully test each release. Bugs are expected.

Prefer a release marked **Stable** — that label is added after the fact when no problems have been reported. If you use any other 0.x build, you should be OK with some bugs. If a specific version will not run, try an older or newer release.

## Features

**Search and import**

- Search AO3 from Calibre: paste a works-search URL or an AO3 series URL, or fill the form
- **Search similar…** from selected library books (pick fandoms, ships, characters, extra tags)
- Import `results.jsonl` or `ao3-import.zip` produced outside Calibre
- Works appear in the library as they scrape; native EPUBs attach as each file finishes
- Optional: download native AO3 EPUBs, simplify tags on import, update existing books, always import the rest of each series

**Selected books**

- **Complete selected** — fill Series, import missing series parts, download missing EPUBs, simplify tags, recompute collections
- Download missing native EPUBs (existing files are never replaced)
- Generate covers (title, author, word count, quality score on a fandom-coloured field)
- Import the rest of an AO3 series, or fill Calibre Series on books already in the library
- Simplify tags, fandoms, and relationships (AO3 synonym collapse + keep/rename/drop rules; fandom metatags)
- Edit, recompute, or add collections (shared rules plus per-work always/never pins)

**Tags and collections**

- Collection rules (tag contains / is exactly, fandom, author, this work) and tag keep/rename/drop
- **Tag graph** of works and tags in the open library (group by fandom, ships, or crossovers); find similar from a node
- **Tag purge** of Tags-column values that appear on at most *N* works (library-wide)
- Background **tag cache** warmer so synonym and metatag lookups do not block search

**Jobs and pacing**

- Long work runs as detachable background jobs: attach the log, hide the window without stopping, stop, retry, delete
- **Running jobs…** lists search, download, simplify, series, collections, tag cache, and the graph viewer
- Optional AO3 login (session cookie saved; password is not stored)
- Host-wide request pacing shared by every fanfic-organizer process on the machine (honours 429 / Retry-After)

**Library layout**

On first import into an empty library (or via plugin settings), creates and fills fanfic columns: **Fandom**, **Relationships**, **Collections**, **Original Tags**, **word count**, plus Calibre’s built-in **Series**. Identifiers store the work URL, AO3 work id, and first series id.

## Install

**One-line install** (downloads Calibre when it is not already installed, then grabs the latest GitHub release, installs the plugin, and starts Calibre if it is not already running):

```bash
curl -fsSL https://raw.githubusercontent.com/BlakeASmith/fanfic-organizer/main/scripts/install.sh | bash
```

Linux installs Calibre without `sudo` into `~/.local/opt/calibre`. macOS needs [Homebrew](https://brew.sh/); Windows needs [winget](https://learn.microsoft.com/en-us/windows/package-manager/winget/). Pass `--no-install-calibre` to skip that step if you manage Calibre yourself.

If Calibre is already open, quit it completely and reopen so the plugin loads.

**Manual install:** download **fanfic-organizer.zip** from [Releases](https://github.com/BlakeASmith/fanfic-organizer/releases). That zip is the whole plugin: UI, ao3kit, and Python libraries. You do not need a git checkout or `pip install`.

1. In Calibre: **Preferences → Plugins → Load plugin from file** → choose `fanfic-organizer.zip`.
2. Restart Calibre.
3. Open a **new** fanfic library (or one you are happy to write). Plugin settings → optional AO3 login.

Search, import, complete, and tag purge write whichever library is currently open. Config, cache, jobs, and the AO3 session live under XDG (`~/.config/fanfic-organizer`, `~/.cache/fanfic-organizer`, `~/.local/state/fanfic-organizer`), not in that library.

Checkout development (install from this repo, cut a release) is in **[CONTRIBUTING.md](CONTRIBUTING.md)**.

## CLI (optional)

There is a scripting CLI over the same library: `python -m ao3kit --help`. The plugin is the product; the CLI is for enthusiasts who want to script.

## Attribution

Fanfic Organizer is original code. These projects were **reference** for behaviour, layout, and ideas — not forks, and none of them endorse this plugin.

- [Calibre](https://calibre-ebook.com/) — the ebook library this plugin runs in ([Kovid Goyal](https://github.com/kovidgoyal/calibre) and contributors).
- [FanFicFare](https://github.com/jimmxinu/fanficfare) — Calibre fanfic columns (`#fandom`, `#relationships`, `#wordcount`, …) and the library layout fanfic-organizer aims to match, so a new library can sit beside an existing FanFicFare one.
- [ao3downloader](https://github.com/nianeyna/ao3downloader) — AO3 login (authenticity-token form POST), Cloudflare challenge HTML markers, and bulk download / session patterns.
- [add-cover-to-ao3-files](https://github.com/alexwlchan/add-cover-to-ao3-files) — generated EPUB covers: title and author on a dark, fandom-seeded gradient so same-fandom works group in grid view.
- [Archive of Our Own](https://archiveofourown.org/) — public search, work, series, and tag-wrangling pages. Not affiliated with the [Organization for Transformative Works](https://www.transformativeworks.org/).

Runtime libraries (Beautiful Soup, lxml, requests, PyYAML, Pillow, and others) are listed in [`requirements.txt`](requirements.txt) and vendored into `fanfic-organizer.zip`.

## Docs

- **[CHANGELOG.md](CHANGELOG.md)** — user-facing history; [Unreleased] plus the pre-1.0 disclaimer become the next GitHub release notes
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — clone, pytest, plugin install, changelog/release rules
- **[AGENTS.md](AGENTS.md)** — layout and plugin behavior
