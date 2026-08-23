# Plan: list Wranglekit in Calibre Get plugins

Goal: **Preferences → Get plugins to enhance calibre** shows **Wranglekit**, Install works, later GitHub tags also show as in-app updates.

GitHub Releases stay. They are not the in-app source (see [calibre-get-plugins.md](calibre-get-plugins.md)).

## Already true

- Fat zip: `python makeplugin.py zip` / CI on `v*` tags → `wranglekit.zip`.
- `calibre-plugin/__init__.py`: `name = 'Wranglekit'`, `author = 'Emily'`, `version = __version__`, platforms `windows`/`osx`/`linux`, `minimum_calibre_version = (5, 0, 0)`, `InterfaceActionBase` from `calibre.customize`.
- Empty `plugin-import-name-wranglekit.txt` (multi-file import).
- Unique catalog name (261 plugins, 2026-08-23: no Wranglekit / AO3-named GUI plugin).
- Legacy **AO3 Scraper** already stripped on checkout install — use `Uninstall: AO3 Scraper` on the index so Get plugins does the same.

## Gaps (do these)

| # | Gap | Why |
|---|---|---|
| 1 | MobileRead account that can post + attach in [Plugins](https://www.mobileread.com/forums/forumdisplay.php?f=237) | Only distribution the mirror reads |
| 2 | Support thread `[GUI Plugin] Wranglekit` with **one** zip on **post 1** | Scraper takes first `attachment.php` `*.zip` |
| 3 | Empty **post 2** for test zips | Community convention; keeps post 1 clean |
| 4 | PM a **calibre moderator** with index BBCode | Authors cannot edit [Index of plugins](https://www.mobileread.com/forums/showthread.php?t=118764) |
| 5 | Prove `plugins_mirror.py wranglekit.zip` parses | Else you sit in the sticky and never appear in Calibre ([2026 case](https://www.mobileread.com/forums/showthread.php?t=374096)) |
| 6 | Dual-publish on every tag | Forgetting post 1 means Get plugins stays old while GitHub is new |
| 7 | README / CONTRIBUTING Get plugins + “replace post-1 zip” | After listing; not required to *start* the thread |

Not required: Kovid changelog PM, Donate URL, `History: Yes`, GPL relicense, raising min Calibre above 5.0.

## Phase 0 — parse gate (before any PM)

```bash
pytest
python makeplugin.py zip
git clone --depth 1 https://github.com/kovidgoyal/calibre.git /tmp/calibre
python /tmp/calibre/setup/plugins_mirror.py wranglekit.zip
```

Expect JSON with `name: Wranglekit`, `author: Emily`, `version` matching the tag, `minimum_calibre_version: [5,0,0]`. Check zip size ≪ 20 MB.

Do **not** add type annotations on the Plugin class fields in `__init__.py`. Keep `description` as adjacent string literals, not `+`.

Confirm public author string **Emily** (that is what Get plugins shows).

## Phase 1 — MobileRead thread (human; ~1–2 hours)

1. Register / log in. If registration is broken, use MobileRead contact, not Calibre GitHub.
2. New thread in **E-Book Software / Calibre / Plugins**.
3. Title: **`[GUI Plugin] Wranglekit`**
4. Body: draft below. Attach **`wranglekit.zip` only** (same bytes as the GitHub release you want as “current”).
5. Immediately post **#2**: “Test builds go here. If there is no zip, there is no test build.”
6. Do not attach a second zip to post 1. Do not link GitHub as the only install path once you want Get plugins (sideload can stay as a fallback).

### First-post draft

```text
[GUI Plugin] Wranglekit

Search Archive of Our Own (AO3), import works into Calibre, download native EPUBs,
wrangle tags / fandoms / relationships, generate covers, and manage collections.

This is AO3-focused library tooling, not a fork of FanFicFare. FanFicFare remains
the multi-site story downloader; Wranglekit is search → Calibre columns → tag
rules → jobs. You can use both.

Main features
- Search AO3 (and similar-to-selected), import JSONL / ao3-import.zip
- Selected books: complete (series + missing EPUBs + simplify tags), series fill,
  covers, collections
- Tag cache, tag graph, tag purge, collection/tag rules
- Background jobs with attachable logs

Requirements
- Calibre 5 or newer (Windows, macOS, Linux)
- Prefer a dedicated fanfic library. Search / import / complete / purge write
  the library that is open.
- Optional AO3 login in plugin settings (anonymous works; some works need login)

Pre-1.0: 0.x builds are not rigorously tested (part-time OSS). Prefer a release
marked Stable after soak. If a version will not run, try another.

Install
1. Preferences → Get plugins to enhance calibre → Wranglekit → Install
   (after this thread is on the Index of plugins; can take hours)
2. Or: download the zip attached to this first post → Preferences → Plugins →
   Load plugin from file → restart Calibre

Source, issues, GitHub zip: https://github.com/BlakeASmith/wranglekit
Please report bugs on GitHub or in this thread.

AO3: the plugin rate-limits requests (host-wide). It downloads AO3’s own EPUB
when you ask it to. Mature works follow AO3’s adult gate the same way a browser
would.

If you previously installed a zip named "AO3 Scraper", remove it or install
from Get plugins once Wranglekit is listed (index Uninstall takes care of it).

Version History
[SPOILER]
0.26.1 - … paste CHANGELOG section …
[/SPOILER]
```

(Use the forum spoiler button if BBCode tags differ.)

### Index BBCode to PM a moderator

After the thread exists, substitute `THREAD_ID`:

```text
Please add Wranglekit to the Index of plugins (GUI Plugins), alphabetically.

[*][URL="https://www.mobileread.com/forums/showthread.php?t=THREAD_ID"]Wranglekit[/URL] Search AO3, download native EPUBs, wrangle tags, generate covers, and import into a Calibre library. History: Yes; Uninstall: AO3 Scraper

- Link text is the internal plugin name (Wranglekit).
- Single zip attached to post 1 only.
- Category: GUI Plugins
```

Omit `Donate:` unless there is a real URL. `History: Yes` is optional (FanFicFare is `history: false` in JSON); keep the spoiler for humans anyway.

**Who to PM:** an active Plugins moderator (thread stickies say this; names rotate — theducks / BetterRed / Comfy.n / JimmXinu are routinely in that forum). Do not edit the index yourself.

Optional: PM Kovid to mention Wranglekit in Calibre’s next What’s New.

## Phase 2 — wait and verify

1. Confirm the `<li>` exists on the [Index of plugins](https://www.mobileread.com/forums/showthread.php?t=118764).
2. Wait hours (up to ~1 day). Check `Last-Modified` on [plugins.json.bz2](https://plugins.calibre-ebook.com/plugins.json.bz2) and search [plugins.calibre-ebook.com](https://plugins.calibre-ebook.com/) for Wranglekit.
3. In Calibre: Get plugins → filter **Not installed** → Wranglekit → Install → restart.
4. If the sticky has you but Calibre does not: re-run `plugins_mirror.py wranglekit.zip`. Ask in the thread; do not add a second zip to post 1.

## Phase 3 — every GitHub release after listing

Add a **human** step after `just release publish` / tag CI. MobileRead has no supported API; FanFicFare updates post 1 by hand.

1. Cut the GitHub release as today (`wranglekit.zip`, version bump, CHANGELOG).
2. Download that zip (or use the local `python makeplugin.py zip` output — same contents).
3. Edit **post 1**: delete the old attachment, attach the new zip **only**.
4. Refresh the Version History spoiler (and optionally a new reply with the changelog).
5. Do not bump forum zip without bumping `version` (no update chip).
6. Betas → **post 2** only.

Later (optional engineering): CONTRIBUTING checklist; a `makeplugin.py` reminder printed on `--publish`; **not** cookie automation against MobileRead.

## Phase 4 — docs once live

- README Install: Get plugins first, GitHub zip second.
- CONTRIBUTING: post-1 zip replace.
- Plugin settings / error strings that only mention GitHub: mention Get plugins.

## Decision notes

**FanFicFare overlap.** Different product (AO3 search + Calibre wrangling vs multi-site EPUB). Say so on post 1 so mods/users do not treat it as a duplicate.

**0.x quality.** The catalog is not a review board. Honesty in the thread matches our existing pre-1.0 README. Waiting for “Stable” is optional; listing 0.26.x is consistent with other GUI plugins.

**License.** Stay MIT for listing. Revisit GPLv3 for `calibre-plugin/` only if we want Calibre-derivative clarity — not a Get plugins blocker.

**Author field.** Catalog shows `Emily`. Change `author` in `__init__.py` before the first indexed zip if the public name should differ; renaming later is cosmetic.

**Do not rename `name = 'Wranglekit'`** after listing. A rename needs a new index row + `Uninstall: Wranglekit` (see DeACSM → ACSM Input). The AO3 Scraper → Wranglekit move is the one Uninstall we need.

## Checklist (copy)

- [ ] `plugins_mirror.py wranglekit.zip` prints metadata
- [ ] MobileRead account
- [ ] Thread `[GUI Plugin] Wranglekit` + post-1 zip + reserved post 2
- [ ] PM mod with `[*][URL=…]Wranglekit[/URL] … Uninstall: AO3 Scraper`
- [ ] Appears on Index of plugins sticky
- [ ] Appears on plugins.calibre-ebook.com and in Get plugins
- [ ] Clean install from Get plugins on a throwaway library
- [ ] CONTRIBUTING/README dual-publish
- [ ] Each later tag: replace post-1 zip + spoiler
