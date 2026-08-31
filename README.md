# Fanfic Organizer

A **Calibre plugin** for people who live on [Archive of Our Own](https://archiveofourown.org/).

Search AO3 from your library, pull in works and whole series, download native EPUBs, collapse wrangled tags onto their canonicals, and keep fandoms, ships, and collections tidy — without bouncing between a browser, a downloader, and a spreadsheet.

Unofficial; not affiliated with the Organization for Transformative Works, Archive of Our Own, FanFicFare, or Calibre.

## Why this exists

If you already keep fanfic in Calibre (or want to), AO3 is only half the work. The rest is metadata: synonym soup on Tags, series with missing parts, works with no EPUB, and “I swear I already downloaded that.” Fanfic Organizer is built for that second half — so your library looks like a fanfic library, not a pile of anonymous ebooks.

It aims to sit comfortably next to a [FanFicFare](https://github.com/jimmxinu/fanficfare)-style setup: same kinds of columns (Fandom, Relationships, word count, …), AO3 work and series identifiers, and summaries where you expect them.

## What you can do

**Find and bring works in**

- Paste an AO3 works-search URL or a **series** URL, or fill a search form inside Calibre
- **Search similar…** from books you already have — pick a fandom plus a ship or two (AO3 ANDs every tag you select)
- Watch matches land in the library as they scrape; native EPUBs attach as each download finishes
- Optional on each run: download EPUBs, simplify tags, update existing books, always pull the rest of each series

**Tend the books you already own**

- **Open in AO3** from the library right-click menu
- **Complete selected** — fill Series, import missing parts, grab missing EPUBs, simplify tags, recompute collections
- **Fill from AO3** — match a library row from a works URL, the EPUB, or title + author (picker when several hits)
- Download missing native EPUBs (never overwrites ones you already have)
- Generate covers (title, author, word count, quality score on a fandom-coloured field)
- Import the rest of a series, or only fill Calibre’s Series field
- Simplify tags, fandoms, and relationships (AO3 synonym collapse + your keep/rename/drop rules; fandom metatags like *Marvel* on Spider-Man)

**Collections and tag sense**

- Rules that put works into collections (fandom, ship, word count, complete, …) plus always/never pins per work
- **Tag graph** of your library — group by fandom, ships, or crossovers; find similar from a node
- **Tag purge** for Tags that only appear on a handful of works
- Background **tag cache** so wrangling lookups do not stall search

**Stay in the flow**

- Long jobs run in the background: hide the log without stopping, reopen it later, stop, retry, or clear finished runs
- Optional AO3 login (session cookie saved; password is not stored)
- Polite, shared request pacing across every Fanfic Organizer process on your machine (backs off on 429s)

**Library layout**

On first import into an empty library (or from plugin settings), it creates fanfic columns: **Fandom**, **Relationships**, **Collections**, **Original Tags**, **Summary**, **word count**, plus Calibre’s **Series**, **Publisher** (`Archive of Our Own`), and **Published**. Identifiers store the work URL, AO3 work id, and first series id.

**Also:** **Process library…** for the whole open library without Select All · **Import → Wikipedia…** / **URL or HTML…** for non-AO3 sources · optional **Deploy to KOReader…** for collections on a Kobo/Android with KOReader.

## Install

**One-line install** (installs Calibre if needed, then the latest release, and starts Calibre when it is not already running):

```bash
curl -fsSL https://raw.githubusercontent.com/BlakeASmith/fanfic-organizer/main/scripts/install.sh | bash
```

Linux puts Calibre in `~/.local/opt/calibre` (no `sudo`). macOS needs [Homebrew](https://brew.sh/); Windows needs [winget](https://learn.microsoft.com/en-us/windows/package-manager/winget/). Use `--no-install-calibre` if you already manage Calibre yourself.

If Calibre was already open, quit it completely and reopen so the plugin loads.

**Manual install:** grab the latest **standard** release from [Releases](https://github.com/BlakeASmith/fanfic-organizer/releases) (`FanFicOrganizer-X.Y.Z.zip`). That zip is the whole plugin — no git checkout, no `pip install`. Skip GitHub **pre-releases** unless you want an unreleased snapshot.

1. Calibre → **Preferences → Plugins → Load plugin from file** → choose the zip.
2. Restart Calibre.
3. Open a **new** fanfic library (or one you are happy to write into). Plugin settings → optional AO3 login.

Search, import, process library, and similar actions write whichever library is open. Config, cache, jobs, and the AO3 session live under XDG (`~/.config/fanfic-organizer`, `~/.cache/fanfic-organizer`, `~/.local/state/fanfic-organizer`), not inside that library.

## Pre-1.0

This project will stay below 1.0 for a long time. **0.x releases are not rigorously tested.** It is open source and maintained part-time — bugs are expected.

Prefer a release marked **Stable** (added after the fact when nothing bad has been reported). If another 0.x build will not run, try an older or newer one.

## Scripting (optional)

The plugin is the product. Enthusiasts who want to script the same library can use `python -m ao3kit --help`.

Checkout development (install from this repo, cut a release) is in **[CONTRIBUTING.md](CONTRIBUTING.md)**.

## Attribution

Fanfic Organizer is original code. These projects were **reference** for behaviour and ideas — not forks, and none of them endorse this plugin.

- [Calibre](https://calibre-ebook.com/) — the ebook library this plugin runs in ([Kovid Goyal](https://github.com/kovidgoyal/calibre) and contributors).
- [FanFicFare](https://github.com/jimmxinu/fanficfare) — fanfic column layout this plugin aims to match.
- [ao3downloader](https://github.com/nianeyna/ao3downloader) — AO3 login and bulk-download patterns.
- [add-cover-to-ao3-files](https://github.com/alexwlchan/add-cover-to-ao3-files) — fandom-coloured generated covers.
- [Archive of Our Own](https://archiveofourown.org/) — public search, work, series, and tag-wrangling pages. Not affiliated with the [Organization for Transformative Works](https://www.transformativeworks.org/).

Runtime libraries are listed in [`requirements.txt`](requirements.txt) and vendored into the release zip.

## Docs

- **[CHANGELOG.md](CHANGELOG.md)** — what changed; [Unreleased] feeds the next GitHub release notes
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — clone, pytest, plugin install, release rules
- **[AGENTS.md](AGENTS.md)** — project layout and plugin behaviour (for contributors and agents)
- **[demos/](demos/README.md)** — screen recordings of the real UI

## Support

Fanfic Organizer is maintained part-time. If it saves you time wrangling tags and series, tips help fund more of that work.

[![Buy Me A Coffee](https://img.buymeacoffee.com/button-api/?text=I%20can%20do%20more%20of%20this%20with%20%24&emoji=&slug=BlakeSmith&button_colour=BD5FFF&font_colour=ffffff&font_family=Cookie&outline_colour=000000&coffee_colour=FFDD00)](https://www.buymeacoffee.com/BlakeSmith)
