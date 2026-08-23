# Release platforms (research)

How users can install **Fanfic Organizer** without sideloading a zip.

| Path | In Calibre? | Status |
|---|---|---|
| [GitHub Releases](https://github.com/BlakeASmith/fanfic-organizer/releases) zip + **Load plugin from file** | No | **Current** (keep) |
| Calibre **Get plugins** (Preferences → Plugins → Get new plugins) | Yes | **Not listed** — see plan |
| PyPI / Homebrew / Flathub plugin store | — | None exists for Calibre plugins |

There is no GitHub-backed Calibre store. The in-app catalog is a **MobileRead forum index** mirrored by Kovid Goyal’s server.

- **[calibre-get-plugins.md](calibre-get-plugins.md)** — how Get plugins works (sources, zip rules, JSON schema, delays).
- **[fanfic-organizer-plan.md](fanfic-organizer-plan.md)** — steps, drafts, and release-process changes to get listed and stay updated.

Researched 2026-08-23 against Calibre `setup/plugins_mirror.py`, `src/calibre/gui2/dialogs/plugin_updater.py`, live `https://plugins.calibre-ebook.com/plugins.json.bz2` (261 plugins; Fanfic Organizer absent), and the MobileRead [Index of plugins](https://www.mobileread.com/forums/showthread.php?t=118764).
