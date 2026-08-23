# Contributing

**Wranglekit** is the Calibre plugin (the product). **`ao3kit`** is the Python library it runs. This git repo is both. The CLI (`python -m ao3kit`) is optional for scripting — keep it in relative parity with the plugin, but do not document it as a second product.

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

Open a **throwaway Calibre library**. Search, import, complete, and tag purge write the library that is open. Config, cache, jobs, and the AO3 session are **not** stored in that library: they follow the [XDG Base Directory](https://specifications.freedesktop.org/basedir/latest/) spec (`~/.config/wranglekit`, `~/.cache/wranglekit`, `~/.local/state/wranglekit`). pytest sets `XDG_*` under a temp dir so it does not write your real home.

```bash
python makeplugin.py install   # or: just load-dev
```

That copies the plugin UI into Calibre **and** writes `calibre-plugin/dev_project.json` (gitignored) with this checkout’s path. After you restart Calibre, jobs use this tree instead of the bundled zip. To override: plugin settings → Project path, or `AO3KIT_PROJECT`.

Optional [just](https://github.com/casey/just) recipes: `just load-dev` (install), `just build` (`wranglekit.zip`), `just release` (next 0.x minor; add `patch` and/or `publish`).

Restart Calibre yourself unless you are iterating on plugin UI and need the GUI reloaded now (`python makeplugin.py install --restart` / MCP `restart=true`). Default is install only.

## Where to change things

| Change | Start here |
|---|---|
| AO3 HTTP, login, 429s | `ao3kit/http.py`, `rate.py`, `rate_store.py` |
| Search URL / scrape filters | `ao3kit/scrape.py` (keep plugin `scrape_run.py` sorts in sync) |
| Tag canonical / cache | `ao3kit/tags/` |
| Collections / keep-rename-drop | `ao3kit/tags/collections.py`, `config.py`, plugin `collection_*.py` |
| Jobs / logs / retry | `ao3kit/jobs.py`; plugin `job_*.py` |
| Covers | `ao3kit/covers.py`; plugin `cover_ui.py` |
| Calibre columns / import | `calibre-plugin/importer.py`, `columns.py`, `cleaned.py` |
| Plugin menu / settings | `ao3_plugin.py`, `config.py`, `dialogs.py` |

Root `scrape_ao3.py`, `ao3_http.py`, and similar files are compatibility shims. Import from `ao3kit`.

Known copies (do not invent a third): `calibre-plugin/similar.py` mirrors `ao3kit/similar.py`; `SORT_OPTIONS` lives in `ao3kit/scrape.py` and `calibre-plugin/scrape_run.py` (tests check they match).

## Version, changelog, and releases

One version string: `ao3kit.__version__`. The plugin tuple in `calibre-plugin/__init__.py` must match. `pyproject.toml` reads the package attribute. `just release` (or `python makeplugin.py release`) bumps the next **0.x minor** (0.26.0 → 0.27.0). Pass `--patch` / `just release patch` for a patch bump. This tool does not cut 1.0+.

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

Unreleased notes **are** the GitHub release body (plus the pre-1.0 disclaimer `makeplugin.py` appends until 1.0). Empty Unreleased cannot be released.

```bash
python makeplugin.py changelog              # preview [Unreleased]
pytest
just release                                # next 0.x minor (working tree)
just release publish                        # cut + commit + push + zip + gh release
just release patch                          # next 0.x patch instead
```

Same as `python makeplugin.py release` / `--patch` / `--publish`.

`--publish` commits the changelog/version files (`chore(release): X.Y.Z`), pushes, builds `wranglekit.zip`, and runs `gh release create` with those notes. Pushing the `vX.Y.Z` tag also runs CI, which rebuilds the zip and sets the release body from the versioned CHANGELOG section.

## Tests and plugin install

```bash
pytest
just load-dev   # or: python makeplugin.py install
```

Tell whoever is using Calibre to restart it after install. Do not `killall calibre`.

Full product notes: [AGENTS.md](AGENTS.md). User-facing plugin install: [README.md](README.md).
