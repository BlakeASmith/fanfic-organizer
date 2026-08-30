# Contributing

**Fanfic Organizer** is the Calibre plugin (the product). **`ao3kit`** is the Python library it runs. This git repo is both. The CLI (`python -m ao3kit`) is optional for scripting — keep it in relative parity with the plugin, but do not document it as a second product.

## Day 1

You need Python 3.10+ (CI uses 3.12). Calibre is required only for plugin UI work.

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env        # optional AO3_USERNAME / AO3_PASSWORD
pytest
```

`requirements.txt` is the `ao3kit` library runtime (also used by the optional CLI). `requirements-dev.txt` adds pytest and the Calibre-dev MCP extra. `just build` vendors that list into the zip except native packages Calibre already has (`lxml`, Pillow).

Calibre **source** for reading GUI/library internals lives at `$HOME/src/calibre` (the [BlakeASmith/calibre](https://github.com/BlakeASmith/calibre) fork). Clone it if missing; update with `git -C "$HOME/src/calibre" pull --ff-only origin master`. Agents: follow `.cursor/skills/calibre-source-code-read/SKILL.md`. Do not open PRs against `kovidgoyal/calibre`. The plugin must keep working on stock Calibre and must not depend on fork-only additions.

Open a **throwaway Calibre library**. Search, import, process library, fill from AO3, complete, and tag purge write the library that is open. Config, cache, jobs, and the AO3 session are **not** stored in that library: they follow the [XDG Base Directory](https://specifications.freedesktop.org/basedir/latest/) spec (`~/.config/fanfic-organizer`, `~/.cache/fanfic-organizer`, `~/.local/state/fanfic-organizer`). pytest sets `XDG_*` under a temp dir so it does not write your real home.

GUI testing with computerUse: follow [Plugin GUI (computer use)](.cursor/skills/plugin-gui-computer-use/SKILL.md) (menu map, columns, throwaway library). Do not spend time collapsing the tag browser or discovering Calibre chrome.

```bash
python makeplugin.py install   # or: just load-dev
```

That copies the plugin UI into Calibre **and** writes `calibre-plugin/dev_project.json` (gitignored) with this checkout’s path. After you restart Calibre, jobs use this tree instead of the bundled zip. To override: plugin settings → Project path, or `AO3KIT_PROJECT`.

Optional [just](https://github.com/casey/just) recipes: `just load-dev` (install), `just build` (`fanfic-organizer.zip`), `just release` (next 0.x minor; add `patch` and/or `publish`).

Restart Calibre yourself unless you are iterating on plugin UI and need the GUI reloaded now (`python makeplugin.py install --restart` / MCP `restart=true`). Default is install only.

## Where to change things

| Change | Start here |
|---|---|
| AO3 HTTP, login, 429s | `ao3kit/http.py`, `rate.py`, `rate_store.py` |
| Search URL / scrape filters | `ao3kit/scrape.py` (keep plugin `scrape_run.py` sorts in sync) |
| Tag canonical / cache / autocomplete | `ao3kit/tags/` (`suggest.py` for local name completion) |
| Collections / keep-rename-drop | `ao3kit/tags/collections.py`, `config.py`, plugin `collection_*.py` |
| Jobs / logs / retry | `ao3kit/jobs.py`; plugin `job_*.py` |
| Covers | `ao3kit/covers.py`; plugin `cover_ui.py` |
| Calibre columns / import | `calibre-plugin/importer.py`, `columns.py`, `cleaned.py` |
| Plugin menu / settings | `ao3_plugin.py`, `config.py`, `dialogs.py`, `library_job.py` |

Known copies (do not invent a third): `calibre-plugin/similar.py` mirrors `ao3kit/similar.py`; `SORT_OPTIONS` lives in `ao3kit/scrape.py` and `calibre-plugin/scrape_run.py` (tests check they match).

## Version, changelog, and releases

One version string: `ao3kit.__version__`. The plugin tuple in `calibre-plugin/__init__.py` must match (`__version_display__` is the same `X.Y.Z` on the source tree). `pyproject.toml` reads the package attribute.

**Standard releases** (what most users should install) are cut from GitHub Actions → **Release plugin** (`workflow_dispatch`, bump `patch` / `minor` / `major`). That workflow rolls `[Unreleased]` into `## [X.Y.Z]`, bumps versions, tags `vX.Y.Z`, attaches `FanFicOrganizer-X.Y.Z.zip`, and prepends a notice on preview releases whose commits are included. Locally, `just release` / `python makeplugin.py release` still does the same cut; add `publish` to push and create the GitHub release. This tool does not cut 1.0+.

**Preview pre-releases** are created automatically on every push to `main`. They use `X.Y.Z-preview.<GitHub run number>+<short SHA>` (next 0.x minor as `X.Y.Z`), tag `v` plus that string, and are marked GitHub pre-releases. The pipeline only *reads* `[Unreleased]` for notes; it never writes `CHANGELOG.md`. Prefer a standard release for daily use.

**PR builds** publish a public GitHub **pre-release** (`X.Y.Z-pr.<n>+<sha>`) with the plugin zip and comment a download link. Install from Calibre (**Check for updates…** → **Include PR builds**) or `scripts/install.sh --url '…/releases/download/…'`. No GitHub login required for those assets.

### Pre-1.0

0.x will last a long time. **Do not treat a new tag as production-hardened.** There is no capacity to fully test each release (open source, part-time). `makeplugin.py changelog` / GitHub release notes always append that disclaimer until 1.0.

Users should prefer a release marked **Stable**, or accept bugs. If one build will not run, they can try an older or newer tag.

**Stable** is after-the-fact: soak a 0.x with no reported problems, then mark it — GitHub release title `v0.26.0 (Stable)` and/or `## [0.26.0] - 2026-08-23 — Stable` in CHANGELOG.md. Do not mark Stable at publish time.

### Conventional Commits

Commit subjects use [Conventional Commits](https://www.conventionalcommits.org/): `type: summary` or `type(scope): summary`. Breaking changes use `type!: summary` and a `BREAKING CHANGE:` footer.

| Type | Changelog heading | Goes in CHANGELOG? |
|---|---|---|
| `feat` | Features | yes |
| `fix` | Bug Fixes | yes |
| `perf` | Performance | yes |
| `docs` | Documentation | yes, if users or contributors would notice |
| `refactor` | Refactoring | only if behavior or UX changes |
| `build` / `ci` | Build | only if the zip/install/release path changes |
| `feat!` / `BREAKING CHANGE:` | Breaking Changes | yes |
| `chore` / `test` / `style` | — | no |

Example: `feat(plugin): generate covers for selected books`.

### CHANGELOG.md

[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). New user-facing work gets a bullet under **[Unreleased]** in the **same change** (same PR/commit), grouped by the headings above. Imperative, user-facing: `- Add cover generation for selected books.` not `- Updated cover_ui.py`.

Do not dump test-only or internal chore work. Do not edit old version sections except to fix a factual error.

### Cutting a GitHub release

Unreleased notes **are** the GitHub standard-release body (plus the pre-1.0 disclaimer `makeplugin.py` appends until 1.0). Empty Unreleased cannot be released. Preview tags do not consume Unreleased.

```bash
python makeplugin.py changelog              # preview [Unreleased]
pytest
# Preferred: Actions → Release plugin → bump_type minor|patch
just release                                # next 0.x minor (working tree)
just release publish                        # cut + commit + push + zip + gh release
just release patch                          # next 0.x patch instead
```

Same as `python makeplugin.py release` / `--patch` / `--bump` / `--publish`.

`--publish` commits the changelog/version files (`chore(release): X.Y.Z`), pushes, builds `FanFicOrganizer-X.Y.Z.zip`, and runs `gh release create` with those notes. Pushing the `vX.Y.Z` tag also runs CI, which rebuilds the zip and sets the release body from the versioned CHANGELOG section. Preview tags (`v*-preview*`) are not treated as standard releases.

## Tests and plugin install

```bash
pytest
just load-dev   # or: python makeplugin.py install
```

Tell whoever is using Calibre to restart it after install. Do not `killall calibre`.

Full product notes: [AGENTS.md](AGENTS.md). User-facing plugin install: [README.md](README.md).
