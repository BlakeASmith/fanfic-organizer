# Calibre “Get plugins” (the in-app catalog)

Calibre has **no plugin marketplace of its own** and **no GitHub/PyPI ingest**. Preferences → **Get plugins to enhance calibre** (also Preferences → Plugins → **Get new plugins**) is the old **Plugin Updater**, now built in.

It installs from a **mirror of MobileRead**, not from the zip you attach to a GitHub Release.

## End-to-end

```
You                    MobileRead                         Kovid’s mirror              Calibre
──                     ──────────                         ──────────────              ───────
post [GUI Plugin]      Plugins forum (f=237)              cron: plugins_mirror.py
  thread + 1 zip   →   first post has attachment.php      scrape Index of plugins
                       PM a calibre mod                   fetch first .zip on thread
                       mod edits Index of plugins     →   parse __init__.py (AST)
                                                          write plugins.json.bz2
                                                          host {thread_id}.zip    →  GET plugins.json.bz2
                                                                                     download {thread_id}.zip
                                                                                     add_plugin()
```

Users never hit GitHub. They hit `https://code.calibre-ebook.com/plugins/` (Calibre pins its own CA). A public copy of the catalog is `https://plugins.calibre-ebook.com/` (HTML + `plugins.json.bz2`).

## Sources of truth

| What | Where |
|---|---|
| Human catalog + support | MobileRead **Plugins** forum, [f=237](https://www.mobileread.com/forums/forumdisplay.php?f=237) |
| How to use Get plugins | Sticky [Introduction to plugins](https://www.mobileread.com/forums/showthread.php?t=118680) |
| Machine index (scraped) | Sticky [Index of plugins](https://www.mobileread.com/forums/showthread.php?t=118764), first post `p=1362767` |
| Zip Calibre actually installs | First `.zip` **forum attachment** on the plugin **thread** (usually post 1) |
| Version / author / min Calibre / platforms / description | AST parse of `__init__.py` inside that zip — **not** the index blurb |
| In-app list | `https://code.calibre-ebook.com/plugins/plugins.json.bz2` |

GitHub Releases, `just release publish`, and `wranglekit.zip` on the repo **do not** update Get plugins.

## Index entry (what mods paste)

The index is HTML `<li>` items under headings such as **GUI Plugins:**. The scraper (`parse_index` in `setup/plugins_mirror.py`) is strict.

From each `<li>` it takes:

| Field | How |
|---|---|
| `index_name` | **Visible text of the thread link** — must equal `Plugin.name` in `__init__.py` (`Wranglekit`) |
| `thread_url` | `https://www.mobileread.com/forums/showthread.php?t=…` (or `?p=`) |
| `category` | Nearest heading (`GUI`, `Metadata Source`, `Editor`, …) |
| `donate` | Optional `Donate: <a href="…">` |
| `history` | Optional `History: Yes` / `True` (bool in JSON; Calibre mostly just links the thread now) |
| `uninstall` | Optional `Uninstall: Old Name, Other Name` — removed before install |
| `deprecated` | Item under **Deprecated/Renamed/Retired** (plus historical `Deprecated: Yes`) |

**Not** in the index anymore (Nov 2020): Version, Released, Author, Calibre, Platforms. Putting them in the `<li>` is ignored (or noise). Those come from the zip.

Mods: copy an existing `[*]` block; link text = internal plugin name; **PM an active calibre moderator** with a filled [Index Entry Sample](https://www.mobileread.com/forums/showthread.php?t=118764). Authors cannot edit the sticky.

Optional: PM **Kovid Goyal** to mention the plugin in the next Calibre changelog (“What’s new”).

## Zip rules (mirror will skip you otherwise)

`parse_plugin_zip_url` takes the **first** `attachment.php?…>something.zip` on the fetched thread HTML.

1. **Exactly one `.zip` on post 1.** Extra zips → wrong file or failed install.
2. **Test builds** go on a **reserved post 2**, not post 1.
3. Filename must end in `.zip` (not `.ZIP.PHP`, not a GitHub URL).
4. Replacing the attachment updates `Last-Modified`; the mirror HEADs and re-downloads if newer.
5. MobileRead zip attachments are on the order of **20 MB**. FanFicFare’s catalog zip is ~1.8 MB; our fat `wranglekit.zip` should be fine if we keep skipping `lxml` / Pillow.

Calibre then installs `https://code.calibre-ebook.com/plugins/{thread_id}.zip` (mirror rename). Example: FanFicFare thread `t=259221` → `file: "259221.zip"`.

## What `__init__.py` must look like

The mirror **does not import** the plugin. It AST-walks `__init__.py` (and simple `from calibre_plugins… import CONST` in the zip).

Required on the `Plugin` / `InterfaceActionBase` subclass:

- `name`, `description`, `author`, `version` (tuple)
- `supported_platforms` (use `osx` not `macos`)
- `minimum_calibre_version`

Allowed imports of the base class: `calibre.customize`, `calibre.customize.builtins`, conversion/metadata/device modules.

**Keep this file boring.** June 2026: a metadata plugin was in the forum index for a week but **absent from Get plugins** because type annotations / split imports broke the parser. Kovid extended the parser; still avoid:

- Annotated class attributes that confuse older parser builds
- `version` imported from a second file unless it is a trivial `NAME = (1,2,3)` module
- `description = 'foo' + 'bar'` (`BinOp` keeps only the right-hand string)
- Implicit adjacent-string concat is OK (one `Constant`)

Local check (after `python makeplugin.py zip`):

```bash
git clone --depth 1 https://github.com/kovidgoyal/calibre.git /tmp/calibre
python /tmp/calibre/setup/plugins_mirror.py wranglekit.zip
```

Must print `name`, `author`, `version`, `description`, `supported_platforms`, `minimum_calibre_version`. If it throws, Get plugins will never list the zip.

Wranglekit’s `calibre-plugin/__init__.py` already matches this shape (`version = __version__`, adjacent-string `description`, `InterfaceActionBase` from `calibre.customize`).

## Live `plugins.json.bz2` record (2026-08-23)

261 plugins. Example **FanFicFare** (closest analogue: fanfic download GUI):

```json
{
  "name": "FanFicFare",
  "index_name": "FanFicFare",
  "author": "Jim Miller",
  "category": "GUI",
  "description": "UI plugin to download FanFiction stories from various sites.",
  "version": [4, 60, 0],
  "minimum_calibre_version": [5, 0, 0],
  "supported_platforms": ["windows", "osx", "linux"],
  "thread_id": "259221",
  "thread_url": "https://www.mobileread.com/forums/showthread.php?t=259221",
  "file": "259221.zip",
  "original_url": "https://www.mobileread.com/forums/attachment.php?attachmentid=…",
  "size": 1755761,
  "last_modified": "2026-08-01T14:00:42",
  "donate": null,
  "history": false,
  "deprecated": false,
  "uninstall": ["FanFictionDownLoader"]
}
```

`description` in Calibre is the **zip** string, not the index sentence. `index_name` (link text) should equal `name` (zip) or the list label and the installed plugin disagree.

Categories in that snapshot: GUI 149, Metadata Source 53, Deprecated 19, Editor 11, plus File Type / Store / Device / Conversion / Library Closed. **Wranglekit → GUI.**

No catalog entry contains `Wranglekit` or `AO3` in the name.

## Updates (after you are listed)

1. Bump `version` in `__init__.py` (Get plugins compares tuples; same version ⇒ no update).
2. Edit **post 1**: remove the old zip, attach the new one (still a single zip).
3. Update the human **Version History** spoiler on post 1 (for the thread, not the JSON).
4. Wait for the mirror. `Last-Modified` on `https://plugins.calibre-ebook.com/plugins.json.bz2` is a useful check (this research: updated ~hourly on 2026-08-23). Community reports **hours to about a day**. If the index lists you and you still miss Get plugins after a week, the zip parse is the usual cause ([example](https://www.mobileread.com/forums/showthread.php?t=374096)).
5. Users who already sideloaded a zip whose `name` is `Wranglekit` will see **Update available**. Other names will not, unless the index has `Uninstall: That Name`.

Calibre’s dialog installs from the mirror URL, shows the forum thread / donate links, and may uninstall `uninstall` names first. Changelog is **not** scraped into a pane anymore; `history` is a leftover flag. Users open **Plugin forum thread**.

## Policy / culture

- **DRM / DeDRM** is banned on MobileRead. Do not mention it.
- Fanfic + AO3 is already accepted (**FanFicFare** is GUI, indexed, ~1.8 MB zip).
- Adult/mature AO3: FanFicFare does this; no extra catalog gate. Be clear in the thread.
- No formal code review. Gate = “looks like a real plugin, zip on post 1, a mod adds the `<li>`.”
- Forum registration has had outages; attachments in Plugins are normally allowed for registered users. Zip cap ~20 MB.
- Calibre is GPLv3. Plugins that `import calibre.*` are often GPL; MobileRead does **not** check. Wranglekit is MIT today — listing still happens; relicensing the plugin layer is a separate legal call, not a catalog requirement.

## Keep GitHub anyway

Sideload remains valid (and required for betas on post 2). FanFicFare ships GitHub + post 1 zip + Get plugins. Do the same: GitHub = automation and source; MobileRead post 1 = what the updater installs.
