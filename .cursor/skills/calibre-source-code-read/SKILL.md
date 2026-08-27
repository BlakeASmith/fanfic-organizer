---
name: calibre-source-code-read
description: Read the local Calibre source checkout to understand GUI, library, and tag-browser internals. Use when investigating Calibre UI stalls, library writes, tag browser recounts, incremental ingest, plugin compatibility with stock Calibre, or when you need to map a plugin call onto Calibre's implementation. Never open pull requests against kovidgoyal/calibre. The Fanfic Organizer plugin must stay compatible with stock Calibre and must not depend on fork-only APIs.
---

# Calibre source code read

This skill is **read-only Calibre awareness**. The product is still the Fanfic Organizer plugin targeting **stock Calibre**. The checkout exists so agents can explain GUI/library behavior, not so we can patch or ship against a private fork.

## Where it lives

| What | Path |
|---|---|
| Calibre **source checkout** (BlakeASmith fork) | `$HOME/src/calibre` (`/home/ubuntu/src/calibre` on this machine) |
| Installed Calibre **binary** (stock, what the plugin must run against) | `/usr/bin/calibre` / `/opt/calibre` |
| This plugin repo | `/workspace` (or the git checkout of `BlakeASmith/fanfic-organizer`) |

The source tree is **outside** the plugin repo. Do not copy it into `fanfic-organizer`, do not add it as a submodule, and do not vendor Calibre.

If the checkout is missing (new VM / fresh agent):

```bash
mkdir -p "$HOME/src"
git clone --filter=blob:none https://github.com/BlakeASmith/calibre.git "$HOME/src/calibre"
git -C "$HOME/src/calibre" remote add upstream https://github.com/kovidgoyal/calibre.git
git -C "$HOME/src/calibre" remote set-url --push upstream DISABLE
```

`--filter=blob:none` is enough for reading source. Deepen history only if you need `git blame` across old commits.

## How to update

```bash
git -C "$HOME/src/calibre" fetch origin
git -C "$HOME/src/calibre" pull --ff-only origin master
```

Optional, to compare with upstream Kovid (fetch only):

```bash
git -C "$HOME/src/calibre" fetch upstream
git -C "$HOME/src/calibre" log --oneline HEAD..upstream/master | head
```

Do not rebase, merge upstream into the fork, or push, unless the user explicitly asks to maintain the fork itself.

Match the **installed** Calibre the plugin actually runs:

```bash
calibre --version
# then read src/calibre/constants.py numeric_version in the checkout
```

On this machine that is Calibre **9.13**. Prefer the checkout when it matches that version. If the fork has moved ahead, still reason about APIs that exist in stock 9.13.

## Hard rules

1. **Never open a pull request against `kovidgoyal/calibre`.** No `gh pr create` with that repo, no patches mailed upstream, no `git push upstream`. The primary Calibre project is not ours to change from this plugin work.
2. **Never push to `BlakeASmith/calibre` from a fanfic-organizer task** unless the user explicitly asked to change the fork.
3. **The plugin must stay compatible with stock Calibre.** Users install official Calibre. Do not call APIs, Qt widgets, or settings that exist only on the BlakeASmith fork (for example the Grok/LLM extras on `master`). If a fork-only helper would make a problem easier, reimplement the behavior in the plugin using public Calibre APIs that 9.13 already has (`LibraryDatabase`, `db.new_api`, `gui.library_view.model()`, `gui.tags_view`).
4. Read Calibre to **understand** stalls and to **call existing hooks correctly**. Do not “fix Calibre” from this repo.

## Layout (what to open first)

Root: `$HOME/src/calibre/src/calibre/`

| Area | Files |
|---|---|
| Book list model | `gui2/library/models.py` (`BooksModel.refresh_ids`, `books_added`, `refresh`, `count_changed`) |
| Book list view | `gui2/library/views.py` |
| Tag browser | `gui2/tag_browser/view.py` (`recount`), `gui2/tag_browser/model.py` (`rebuild_node_tree`) |
| Tag browser ↔ library | `gui2/tag_browser/ui.py` (`count_changed_signal` → `recount`) |
| DB writes | `db/cache.py` (`create_book_entry`, `set_field`), `db/legacy.py` (`create_book_entry` also calls `data.books_added`) |
| Filtered id map | `db/view.py` (`books_added`, `refresh_ids`) |
| Tag-browser data | `db/categories.py` (`get_categories`), `db/fields.py` (`Field.get_categories`) |
| Metadata OPF backup | `db/backup.py` (`MetadataBackup`, dirtied after every `set_field`) |

Plugin counterparts: `calibre-plugin/importer.py` (`import_record`, `refresh_library_ui`), `calibre-plugin/job_supervise.py` (`_poll_import`, 1s `QTimer`).

## Incremental ingest vs the GUI (known stall)

Background ao3kit jobs are **not** on Calibre’s GUI thread. The plugin **is**: `JobSupervisor.tick` runs every 1s on the GUI, reads JSONL, calls `import_record` per new work, then `refresh_library_ui`. Concurrent jobs each get a poll on that same timer.

`refresh_library_ui` does:

1. `library_view.model().refresh_ids(book_ids)` — `dataChanged` for existing rows. It does **not** call `BooksModel.books_added()`, which is Calibre’s own `beginInsertRows` path when adding books.
2. `tags_view.recount()` — full tag-browser rebuild: `get_categories()` over **every** many-many field (Tags, Fandom, Relationships, Original Tags, …) then `rebuild_node_tree()` (`beginResetModel` on the tag tree).

`import_record` → `find_existing_book` runs up to four `search_getting_ids('identifiers:…')` queries. Identifier search (`db/search.py` keypair over `field_iter`) walks **every book**. A *miss* (the new-work case) pays for all three URL/ao3 queries.

`db.create_book_entry` updates `db.data` so `BooksModel.rowCount()` grows without `beginInsertRows`. Calibre’s add-books UI calls `model.books_added(n)` after inserts; the plugin currently does not.

Measured with **stock Calibre 9.13** (`calibre-debug`) on AO3-like unique freeforms (not a small shared tag pool):

| Library | Unique Tags + Original Tags | `find_existing` miss / new work | `create_book_entry` + `set_field` | `tags_view` rebuild (`recount`) | Tag-tree nodes |
|---|---|---|---|---|---|
| 300 | 6.6k + 7.2k | 16ms (hit) | — | 98ms | 15k |
| 1000 | 22k + 24k | 6ms (hit) | — | 296ms | 49k |
| 1800 | 40k + 43k | **554ms miss** / 11ms hit | 89ms | **623ms** | 88k |
| 20 new works, recount after each | | 11.1s total lookup | 1.8s writes | 12.2s recounts | **25s GUI** |
| Same 20, writes then one recount | | (lookups omitted in that run) | 1.7s | 0.63s | **2.3s** |

Visible-row tag painting stayed a few milliseconds (Qt only paints on-screen cells). A naïve “measure every row” proxy grew with row count (~171ms at 2000 shared-tag books) but that is **not** what the book list does on a normal refresh. Long tag columns matter because they inflate the **tag browser**, not because the table paints every character of every row.

So the freeze is not “background processes fighting Calibre.” It is the plugin importing on the GUI thread and rebuilding the tag browser (and doing O(library) identifier searches) as often as JSONL grows. Multiple jobs multiply those 1s ticks.

**Preferred plugin-side mitigations** (stock APIs only):

- Look up existing AO3 ids without `search_getting_ids` (in-memory identifier map, or one indexed query).
- Batch library writes, then refresh **once**.
- Call `model.books_added(n)` for new rows instead of only `refresh_ids`.
- Throttle `tags_view.recount()` (every few seconds, or after the job), not after every work.
- Keep `_poll_import` cheap: do not re-parse the whole JSONL on the GUI thread.

Do not add a Calibre-fork API to “fix” this.

## How to search

```bash
rg -n "def recount|def refresh_ids|def books_added|def create_book_entry" "$HOME/src/calibre/src/calibre"
rg -n "tags_view.recount|count_changed_signal" "$HOME/src/calibre/src/calibre/gui2"
```
