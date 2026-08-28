---
name: plugin-gui-computer-use
description: Fast Calibre GUI testing of Fanfic Organizer via computerUse. Use when manually testing the plugin, recording a demo, verifying Qt dialogs/menus, or prompting a computerUse subagent. Do not explore the tag browser, column list, or library switcher — this skill already has that map.
---

# Plugin GUI (computer use)

Use this instead of rediscovering Calibre. **Do not spend turns** collapsing the tag browser, listing columns, or hunting how to switch libraries. pytest covers library logic; computerUse is only for Qt (menus, dialogs, job windows, book list).

## Do not explore

| Temptation | Do this instead |
|---|---|
| Can the tag browser collapse? | Ignore it. Leave left/right sidebars as they are unless they cover the control you must click. |
| Which columns exist? | See [Columns](#columns). Show extra columns only if the test must read them. |
| How do I switch libraries? | Start Calibre with `--with-library`. If it is already open on the wrong library, use [Switch library](#switch-library) once — do not browse Preferences. |
| What is on the plugin menu? | See [Plugin menu](#plugin-menu). Click that path. Do not open every submenu to learn it. |
| Extra toolbar icons (`fo_fill_from_ao3…`, truncated names) | Ignore. The product button is **Fanfic Organizer**. |

Dismiss **Update available** / What’s New / crash reporter with Cancel or Esc. Do not update Calibre during a plugin test.

## Setup (parent agent, before computerUse)

1. Throwaway library only. Search / Import / Process library / Fill from AO3 / Complete / Tag purge **write the open library**.
2. Prepare books **before** starting the GUI. `calibredb` refuses the library while Calibre has it open.
3. `python3 makeplugin.py install` (no `--restart` unless this session must reload plugin UI now). Lock-aware restart only; never `killall calibre`.
4. `python3 makeplugin.py status`. If the GUI is up on the wrong library, switch once or start a new GUI with the right path.
5. Cloud VM: `DISPLAY=:1`.

```bash
LIB=/tmp/fo_gui_test
mkdir -p "$LIB"
# optional: calibredb add --with-library "$LIB" some.epub
# optional: calibredb add_custom_column --with-library "$LIB" fandom Fandom text
python3 makeplugin.py status
# if not running:
calibre --with-library "$LIB"
```

Title bar is `calibre — ||<library folder name>||`. If that is not your throwaway lib, switch before testing.

## Layout (fixed)

```
[ tag browser ] [ book list ] [ book details ]
                 toolbar on top
```

- **Tag browser** (left): Authors, Tags, Formats, plus Fandom / Relationships / … if columns exist. Not needed for plugin menus. Do not collapse, resize, or “clean up” the layout for a demo unless the target control is hidden.
- **Book list** (center): select rows here.
- **Book details** (right): metadata for the current row (Ids, Tags, Fandom, word count). Click a row to refresh it.
- **Toolbar**: **Fanfic Organizer** is InstantPopup — **one click** opens the menu. Do not double-click.

Linux selection: click, then **Ctrl+click** (not Cmd) for a second row. Shift+click for a range. **Selected books** is disabled when nothing is selected (label includes the count, e.g. `Selected books (2)`).

## Switch library

Prefer **not** switching: launch with `--with-library`.

If the GUI is already up:

1. **File → Switch/create library…**
2. Choose **Use the previously existing library at this location**.
3. Pick the folder (e.g. `/tmp/fo_gui_test`).
4. Confirm. Wait for the book list to reload.

Do not create a new library inside computerUse unless the test is specifically “empty library / first import”. Do not use the user’s real fanfic library.

## Columns

Always present: **Title**, **Author(s)**. Also common: Date, Size, Rating, Tags, Series, Publisher, Published.

Plugin custom columns (created on first import into an empty library, or Plugin settings → **Create missing fanfic columns**):

| Header | Lookup |
|---|---|
| Fandom | `#fandom` |
| Relationships | `#relationships` |
| Collections | `#collections` |
| Original Tags | `#originaltags` |
| word count | `#wordcount` |

Series is Calibre’s built-in field (AO3 series name + index). Identifiers: `url`, `ao3`, `ao3series`.

**To show a hidden column (once):** right-click any column header → check the name. Drag the header next to Author(s) if the test must read it without horizontal scroll. If Title + Author is enough, skip this.

**Do not** open Preferences → Add your own columns. **Do not** iterate “which columns can I add?”.

## Plugin menu

**Fanfic Organizer** (teal book / F):

- Search AO3 and import...
- Search similar... *(needs a selection)*
- Import JSONL or zip...
- Process library... *(no selection; whole open library)*
- **Selected books (N)** *(disabled if N=0)*
  - Complete selected
  - Fill from AO3
  - Download EPUB
  - Generate covers
  - Import rest of series
  - Fill series
  - Simplify tags, fandoms & relationships
  - Edit collections...
  - Recompute collections
  - Add to a collection...
- Running jobs...
- **Tags and collections**
  - Collections & tag rules...
  - Tag graph
  - Tag purge...
  - Warm tag cache / Tag cache log... / Stop tag cache
- Check for updates...
- Plugin settings...

Fill from AO3 picker title: **Fill from AO3 — pick the matching work**. First radio is pre-checked. **Skip this book** / **OK** / **Cancel**. Cancel skips remaining ambiguous books.

Process library dialog title: **Process library**. **Start job** / **Cancel**. No selection needed. Estimate text updates when you toggle tasks.

## Jobs

Most selected-book and search actions start a background job and a log window.

| Button | Meaning |
|---|---|
| Hide window | Keep running; tuck the log away |
| Cancel | Stop the process |
| Close | After Done |
| Try again | Retry a failed/stopped job from the start |

Window close box = hide, not cancel. If you hid the log, a completion popup may appear (Retry on failure). **Running jobs...** reopens it.

AO3 is rate-limited (~1.5s/request). Wait on the log; do not Cancel because it looks idle. Do not start a second Fill/Search on the same books while the first job is running.

## Prompt the computerUse subagent

Write exact clicks. Setup (library, columns, books) belongs in the **parent** via `calibredb` / `--with-library`, not in the subagent.

```
Calibre is already open on library <name>. Do not switch libraries,
do not change sidebars, do not hunt columns.

1. Select rows <Title A> and <Title B> (Ctrl+click).
2. Click toolbar Fanfic Organizer (not any other plugin icon).
3. <exact menu path>.
4. Expected dialog: <title>. Click <OK/…>.
5. Wait for the job log; do not Cancel. Close when Done.
6. Click <Title A> and confirm <field> in the book list or details pane.
```

Resume the same computerUse agent for follow-up clicks. For a demo video: get the book list ready first, then start recording, then the clicks above.

## If Calibre looks wrong

- No Fanfic Organizer on the toolbar: plugin not installed or GUI needs restart after `makeplugin.py install`. Stop; do not add it via Preferences during the test unless that is the test.
- Wrong library in the title bar: switch once (above) or relaunch with `--with-library`.
- `calibredb` errors about another Calibre process: the GUI has the library; use the GUI or quit first (lock-aware restart), do not fight with `calibredb`.
