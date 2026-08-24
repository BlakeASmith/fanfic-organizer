#!/usr/bin/env python3
"""Build, install, or release the Calibre plugin.

``zip`` writes a self-contained ``fanfic-organizer.zip`` for GitHub Releases
(plugin UI + ao3kit + vendored pure-Python deps). ``install`` still uses
``calibre-customize -b`` from ``calibre-plugin/`` for fast UI iteration.
``release`` cuts ``CHANGELOG.md`` [Unreleased] into a versioned section,
bumps ``ao3kit`` + plugin versions, and optionally publishes the GitHub release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLUGIN_DIR = ROOT / "calibre-plugin"
AO3KIT_DIR = ROOT / "ao3kit"
OUTPUT = ROOT / "fanfic-organizer.zip"
RUNTIME_REQUIREMENTS = ROOT / "requirements.txt"
# Native wheels (or already in Calibre). Do not pip-install these into vendor/.
SKIP_VENDOR_PACKAGES = frozenset({"lxml", "pillow", "pil"})
NATIVE_SUFFIXES = {".so", ".pyd", ".dylib", ".dll"}
SKIP_DIR_NAMES = {
    "__pycache__",
    ".git",
    "bin",
    "tests",
}
SKIP_FILE_NAMES = {
    "dev_project.json",
}
RELEASE_PATHS = (
    "CHANGELOG.md",
    "ao3kit/__init__.py",
    "calibre-plugin/__init__.py",
)
# Fixed entry timestamp so the zip is reproducible and never trips the ZIP
# format's 1980 floor (fresh CI/snapshot checkouts can leave epoch mtimes).
ZIP_ENTRY_DATE_TIME = (1980, 1, 1, 0, 0, 0)


def _purge_native_extensions(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in NATIVE_SUFFIXES:
            path.unlink()


def _requirement_name(line: str) -> str:
    spec = line.split("#", 1)[0].strip()
    if not spec or spec.startswith("-"):
        return ""
    for sep in ("===", "==", "<=", ">=", "~=", "!=", "<", ">"):
        if sep in spec:
            spec = spec.split(sep, 1)[0]
            break
    return spec.split("[", 1)[0].strip().lower().replace("_", "-")


def vendor_requirement_lines(text: str | None = None) -> list[str]:
    """Runtime requirements minus native/Calibre-provided packages."""
    raw = RUNTIME_REQUIREMENTS.read_text(encoding="utf-8") if text is None else text
    lines: list[str] = []
    for line in raw.splitlines():
        spec = line.split("#", 1)[0].strip()
        if not spec or spec.startswith("-"):
            continue
        name = _requirement_name(spec)
        if not name or name in SKIP_VENDOR_PACKAGES:
            continue
        lines.append(spec)
    return lines


def vendor_requirements_hash() -> str:
    payload = "\n".join(vendor_requirement_lines()).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def vendor_cache_dir() -> Path:
    from ao3kit.paths import plugin_vendor_dir

    return plugin_vendor_dir()


def ensure_vendor(*, force: bool = False) -> Path:
    """pip-install plugin runtime deps into the XDG plugin-vendor cache."""
    cache = vendor_cache_dir()
    stamp = cache / ".requirements.sha256"
    req_hash = vendor_requirements_hash()
    if (
        not force
        and (cache / "requests" / "__init__.py").is_file()
        and (cache / "yaml" / "__init__.py").is_file()
        and stamp.is_file()
        and stamp.read_text(encoding="utf-8").strip() == req_hash
    ):
        return cache
    if cache.exists():
        shutil.rmtree(cache)
    cache.mkdir(parents=True, exist_ok=True)
    reqs = vendor_requirement_lines()
    if not reqs:
        raise SystemExit("no pure-Python packages left to vendor from requirements.txt")
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-compile",
            "-q",
            *reqs,
            "--target",
            str(cache),
        ]
    )
    _purge_native_extensions(cache)
    stamp.write_text(req_hash + "\n", encoding="utf-8")
    return cache


def _should_skip(path: Path, root: Path) -> bool:
    rel_parts = path.relative_to(root).parts
    if any(part in SKIP_DIR_NAMES for part in rel_parts):
        return True
    if path.name in SKIP_FILE_NAMES:
        return True
    if path.suffix in {".pyc", ".pyo"}:
        return True
    if path.suffix.lower() in NATIVE_SUFFIXES:
        return True
    return False


def iter_zip_entries(
    *,
    plugin_dir: Path = PLUGIN_DIR,
    ao3kit_dir: Path = AO3KIT_DIR,
    vendor_dir: Path | None = None,
) -> list[tuple[Path, str]]:
    """``(filesystem path, zip arcname)`` for a release plugin zip."""
    entries: list[tuple[Path, str]] = []
    import_name = plugin_dir / "plugin-import-name-fanfic_organizer.txt"
    if not import_name.exists():
        raise SystemExit(f"Missing required file: {import_name}")
    for path in sorted(plugin_dir.glob("*.py")):
        entries.append((path, path.name))
    entries.append((import_name, import_name.name))
    images_dir = plugin_dir / "images"
    if images_dir.is_dir():
        for path in sorted(images_dir.rglob("*")):
            if not path.is_file() or path.suffix.lower() != ".png":
                continue
            entries.append((path, path.relative_to(plugin_dir).as_posix()))
    if not (ao3kit_dir / "__init__.py").is_file():
        raise SystemExit(f"Missing ao3kit package: {ao3kit_dir}")
    for path in sorted(ao3kit_dir.rglob("*")):
        if not path.is_file() or _should_skip(path, ao3kit_dir):
            continue
        rel = path.relative_to(ao3kit_dir).as_posix()
        entries.append((path, f"ao3kit/{rel}"))
    if vendor_dir is not None:
        for path in sorted(vendor_dir.rglob("*")):
            if not path.is_file() or _should_skip(path, vendor_dir):
                continue
            if path.name.startswith("."):
                continue
            rel = path.relative_to(vendor_dir).as_posix()
            entries.append((path, f"vendor/{rel}"))
    return entries


def build_zip(
    output: Path | None = None,
    *,
    vendor: bool = True,
    force_vendor: bool = False,
) -> Path:
    dest = output or OUTPUT
    vendor_dir = ensure_vendor(force=force_vendor) if vendor else None
    entries = iter_zip_entries(vendor_dir=vendor_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        written: set[str] = set()
        for path, arcname in entries:
            if arcname in written:
                continue
            info = zipfile.ZipInfo(arcname, date_time=ZIP_ENTRY_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, path.read_bytes())
            written.add(arcname)
    print(f"Wrote {dest} ({dest.stat().st_size} bytes, {len(written)} files)")
    return dest


def _print_result(result: dict) -> int:
    message = str(result.get("message") or "")
    if message:
        print(message)
    if not result.get("ok"):
        extra = {
            key: result[key]
            for key in ("error", "holder", "agent_id", "pid", "started_at")
            if result.get(key) not in (None, "")
        }
        if extra:
            print(json.dumps(extra, indent=2))
        return 1
    return 0


def _ctl():
    from calibre_dev.calibre import CalibreCtl

    return CalibreCtl(plugin_dir=PLUGIN_DIR)


def _git(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        text=True,
        **kwargs,
    )


def _dirty_paths_outside_release() -> list[str]:
    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    blocked: list[str] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        path = line[3:].split(" -> ", 1)[-1].strip().strip('"')
        if path not in RELEASE_PATHS:
            blocked.append(path)
    return blocked


def _cmd_changelog(version: str | None) -> int:
    from calibre_dev.changelog import CHANGELOG_PATH, ChangelogError, notes_for_version

    try:
        notes = notes_for_version(
            CHANGELOG_PATH.read_text(encoding="utf-8"),
            version,
        )
    except ChangelogError as exc:
        print(exc, file=sys.stderr)
        return 1
    sys.stdout.write(notes)
    return 0


def _publish_release(version: str, notes: str) -> None:
    tag = f"v{version}"
    _git(["add", *RELEASE_PATHS])
    _git(["commit", "-m", f"chore(release): {version}"])
    _git(["push", "origin", "HEAD"])
    dest = build_zip()
    from ao3kit.paths import cache_dir

    notes_path = cache_dir() / "release-notes.md"
    notes_path.parent.mkdir(parents=True, exist_ok=True)
    notes_path.write_text(notes, encoding="utf-8")
    subprocess.run(
        [
            "gh",
            "release",
            "create",
            tag,
            str(dest),
            "--title",
            tag,
            "--notes-file",
            str(notes_path),
        ],
        cwd=ROOT,
        check=True,
    )
    _git(["fetch", "origin", f"refs/tags/{tag}:refs/tags/{tag}"])


def _cmd_release(
    version: str | None,
    *,
    publish: bool,
    dry_run: bool,
    patch: bool,
    release_date: str | None,
) -> int:
    from calibre_dev.changelog import (
        ChangelogError,
        format_version,
        next_0x_version,
        parse_version,
        prepare_release,
        read_plugin_version,
        require_0x,
    )

    if version and patch:
        print("use either a version or --patch, not both", file=sys.stderr)
        return 2
    try:
        if version:
            version = format_version(require_0x(parse_version(version)))
        else:
            version = format_version(next_0x_version(read_plugin_version(), patch=patch))
    except ChangelogError as exc:
        print(exc, file=sys.stderr)
        return 1
    if publish and dry_run:
        print("use either --publish or --dry-run, not both", file=sys.stderr)
        return 2
    if publish:
        blocked = _dirty_paths_outside_release()
        if blocked:
            print(
                "--publish needs a clean tree except changelog/version files; "
                f"blocked by: {', '.join(blocked)}",
                file=sys.stderr,
            )
            return 1
    try:
        notes = prepare_release(
            version,
            release_date=release_date or None,
            write=not dry_run,
        )
    except ChangelogError as exc:
        print(exc, file=sys.stderr)
        return 1
    sys.stdout.write(notes)
    if dry_run:
        print(f"(dry-run) would cut [Unreleased] into {version}", file=sys.stderr)
        return 0
    print(
        f"Cut [Unreleased] into {version}; bumped ao3kit and plugin versions.",
        file=sys.stderr,
    )
    if publish:
        _publish_release(version, notes)
        print(f"Published GitHub release v{version} with fanfic-organizer.zip.", file=sys.stderr)
    else:
        print(
            f"Commit those files and tag v{version} "
            "(push the tag; CI attaches fanfic-organizer.zip). "
            "To cut and publish in one step: python makeplugin.py release --publish",
            file=sys.stderr,
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        prog="makeplugin.py",
        description=(
            "Build a self-contained fanfic-organizer.zip for GitHub Releases, "
            "install the Calibre plugin from calibre-plugin/, or cut a "
            "changelog release. Restart is opt-in and lock-aware."
        ),
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("zip", "install", "restart", "status", "changelog", "release"),
        help="zip (default), install, restart, status, changelog, or release.",
    )
    parser.add_argument(
        "version",
        nargs="?",
        help=(
            "Optional 0.x X.Y.Z for release (default: next minor). "
            "For changelog: section to print (default Unreleased)."
        ),
    )
    parser.add_argument(
        "-i",
        "--install",
        action="store_true",
        help="Install the plugin (legacy; same as the install command).",
    )
    parser.add_argument(
        "-r",
        "--restart",
        action="store_true",
        help="With install: also quit and start Calibre (host-wide restart lock).",
    )
    parser.add_argument(
        "--lock-timeout",
        type=float,
        default=15.0,
        help="Seconds to wait for the restart lock (default 15). MCP default is 0.",
    )
    parser.add_argument(
        "--agent-id",
        default="",
        help="Label written to the restart lock (who is restarting).",
    )
    parser.add_argument(
        "--no-vendor",
        action="store_true",
        help="Zip plugin + ao3kit only (no pip vendor). For tests.",
    )
    parser.add_argument(
        "--force-vendor",
        action="store_true",
        help="Re-run pip install into the XDG plugin-vendor cache before zipping.",
    )
    parser.add_argument(
        "--patch",
        action="store_true",
        help="With release: bump 0.x patch instead of minor. Do not pass a version.",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="With release: commit, push, zip, and create the GitHub release from [Unreleased].",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With release: print notes without writing files.",
    )
    parser.add_argument(
        "--date",
        default="",
        help="With release: YYYY-MM-DD (default: today).",
    )
    args = parser.parse_args(argv)
    command = args.command
    if args.install:
        command = "install"
    restart = bool(args.restart)
    if args.publish and command != "release":
        parser.error("--publish requires release")
    if args.dry_run and command != "release":
        parser.error("--dry-run requires release")
    if args.patch and command != "release":
        parser.error("--patch requires release")
    if command in {"zip", "install", "restart", "status"} and args.version:
        parser.error(f"{command} does not take a version")
    if command == "changelog":
        return _cmd_changelog(args.version)
    if command == "release":
        return _cmd_release(
            args.version,
            publish=args.publish,
            dry_run=args.dry_run,
            patch=args.patch,
            release_date=args.date or None,
        )
    if command is None:
        if restart:
            parser.error("--restart requires install (or use: makeplugin.py restart)")
        build_zip(vendor=not args.no_vendor, force_vendor=args.force_vendor)
        return 0
    if command == "zip":
        build_zip(vendor=not args.no_vendor, force_vendor=args.force_vendor)
        return 0
    if command == "status":
        result = _ctl().status()
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1
    if command == "install":
        result = _ctl().install(
            restart=restart,
            agent_id=args.agent_id,
            lock_timeout=args.lock_timeout,
            holder="makeplugin:install",
        )
        return _print_result(result)
    if command == "restart":
        result = _ctl().restart(
            agent_id=args.agent_id,
            lock_timeout=args.lock_timeout,
            holder="makeplugin:restart",
        )
        return _print_result(result)
    parser.error(f"Unknown command: {command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
