# wranglekit

A **Calibre plugin** for scraping AO3 search results, wrangling tags, downloading native EPUBs, and cleaning metadata. **`ao3kit`** is the Python library the plugin runs.

## Pre-1.0

This project will stay below 1.0 for a long time. **0.x releases are not rigorously tested.** It is open source and maintained part-time, so there is no capacity to fully test each release. Bugs are expected.

Prefer a release marked **Stable** — that label is added after the fact when no problems have been reported. If you use any other 0.x build, you should be OK with some bugs. If a specific version will not run, try an older or newer release.

## Install

Download **wranglekit.zip** from [Releases](https://github.com/BlakeASmith/wranglekit/releases). That zip is the whole plugin: UI, ao3kit, and Python libraries. You do not need a git checkout or `pip install`.

1. In Calibre: **Preferences → Plugins → Load plugin from file** → choose `wranglekit.zip`.
2. Restart Calibre.
3. Open a **new** fanfic library (or one you are happy to write). Plugin settings → optional AO3 login.

### What you can do

- Search AO3, search similar, import JSONL / `ao3-import.zip`
- **Selected books:** complete selected (series + missing EPUBs + simplify tags), series, missing EPUBs, generate covers, simplify tags, collections
- **Tags and collections:** rules, graph, purge, tag cache
- **Running jobs…** to attach logs or stop work
- Plugin settings: AO3 login, cover style, import defaults

Checkout development (install from this repo, cut a release) is in **[CONTRIBUTING.md](CONTRIBUTING.md)**.

## CLI (optional)

There is a scripting CLI over the same library: `python -m ao3kit --help`. The plugin is the product; the CLI is for enthusiasts who want to script.

## Docs

- **[CHANGELOG.md](CHANGELOG.md)** — user-facing history; [Unreleased] plus the pre-1.0 disclaimer become the next GitHub release notes
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — clone, pytest, plugin install, changelog/release rules
- **[AGENTS.md](AGENTS.md)** — layout and plugin behavior

Related tools: [ao3downloader](https://github.com/nianeyna/ao3downloader), [FanFicFare](https://github.com/jimmxinu/fanficfare).
