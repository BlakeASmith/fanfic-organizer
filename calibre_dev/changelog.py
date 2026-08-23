"""Keep a Changelog + Conventional Commits helpers for wranglekit releases."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG_PATH = ROOT / "CHANGELOG.md"
PLUGIN_INIT = ROOT / "calibre-plugin" / "__init__.py"
PACKAGE_INIT = ROOT / "ao3kit" / "__init__.py"

HEADING_RE = re.compile(
    r"^## \[([^\]]+)\](?:\s+-\s+(\d{4}-\d{2}-\d{2}))?\s*$",
    re.MULTILINE,
)
VERSION_RE = re.compile(r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$")
PLUGIN_VERSION_RE = re.compile(r"^(__version__\s*=\s*)\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*\)\s*$", re.M)
PACKAGE_VERSION_RE = re.compile(r'^(__version__\s*=\s*)"[^"]+"\s*$', re.M)
BULLET_RE = re.compile(r"^\s*[-*+] ")

CONVENTIONAL_SECTIONS = (
    "Breaking Changes",
    "Features",
    "Bug Fixes",
    "Performance",
    "Documentation",
    "Refactoring",
    "Build",
)

# Appended to every 0.x GitHub release body. Keep README / CONTRIBUTING / AGENTS in sync.
PRE_1_0_DISCLAIMER = """\
### Pre-1.0

This project will stay below 1.0 for a long time. **0.x releases are not rigorously tested.** It is open source and maintained part-time, so there is no capacity to fully test each release. Bugs are expected.

Prefer a release marked **Stable** — that label is added after the fact when no problems have been reported. If you use any other 0.x build, you should be OK with some bugs. If a specific version will not run, try an older or newer release.
"""


class ChangelogError(ValueError):
    """Changelog or version file is not in a releasable state."""


@dataclass(frozen=True)
class ChangelogSection:
    title: str
    date: str | None
    body: str


def parse_version(value: str) -> tuple[int, int, int]:
    match = VERSION_RE.fullmatch(value.strip())
    if not match:
        raise ChangelogError(f"version must be X.Y.Z, got {value!r}")
    return int(match["major"]), int(match["minor"]), int(match["patch"])


def format_version(parts: tuple[int, int, int]) -> str:
    return f"{parts[0]}.{parts[1]}.{parts[2]}"


def require_0x(parts: tuple[int, int, int]) -> tuple[int, int, int]:
    """This tool only cuts 0.x. 1.0+ is a deliberate, manual decision."""
    if parts[0] >= 1:
        raise ChangelogError(
            f"{format_version(parts)} is 1.0+; this tool only cuts 0.x releases"
        )
    return parts


def next_0x_version(
    current: tuple[int, int, int],
    *,
    patch: bool = False,
) -> tuple[int, int, int]:
    """Next 0.x: minor+1 (default) or patch+1. Never increments major."""
    require_0x(current)
    major, minor, patch_n = current
    if patch:
        return (major, minor, patch_n + 1)
    return (major, minor + 1, 0)


def parse_changelog(text: str) -> tuple[str, list[ChangelogSection]]:
    matches = list(HEADING_RE.finditer(text))
    if not matches:
        raise ChangelogError("CHANGELOG.md has no ## [version] headings")
    preamble = text[: matches[0].start()].rstrip() + "\n"
    sections: list[ChangelogSection] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append(
            ChangelogSection(
                title=match.group(1),
                date=match.group(2),
                body=text[match.end() : end].strip("\n"),
            )
        )
    return preamble, sections


def render_changelog(preamble: str, sections: list[ChangelogSection]) -> str:
    parts = [preamble.rstrip(), ""]
    for section in sections:
        heading = f"## [{section.title}]"
        if section.date:
            heading = f"{heading} - {section.date}"
        parts.append(heading)
        body = section.body.strip("\n")
        if body:
            parts.append("")
            parts.append(body)
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def has_changelog_items(body: str) -> bool:
    return any(BULLET_RE.match(line) for line in body.splitlines())


def compact_section_body(body: str) -> str:
    """Drop empty ``###`` groups so the GitHub release body stays tight."""
    chunks = re.split(r"(?=^### )", body.strip("\n"), flags=re.M)
    kept: list[str] = []
    for chunk in chunks:
        text = chunk.strip("\n")
        if not text:
            continue
        if text.startswith("### ") and not has_changelog_items(text):
            continue
        kept.append(text)
    return "\n\n".join(kept)


def section_by_title(sections: list[ChangelogSection], title: str) -> ChangelogSection:
    for section in sections:
        if section.title.lower() == title.lower():
            return section
    raise ChangelogError(f"CHANGELOG.md has no [{title}] section")


def is_pre_1_0(version: str | None) -> bool:
    """Unreleased and every 0.x tag carry the testing disclaimer."""
    if version is None:
        return True
    return parse_version(version)[0] < 1


def notes_for_version(text: str, version: str | None = None) -> str:
    """Markdown body for GitHub Releases (no ``##`` heading)."""
    _preamble, sections = parse_changelog(text)
    title = "Unreleased" if version is None else format_version(parse_version(version))
    section = section_by_title(sections, title)
    body = compact_section_body(section.body)
    if not has_changelog_items(body):
        label = "Unreleased" if version is None else title
        raise ChangelogError(f"CHANGELOG.md [{label}] has no entries")
    notes = body.strip() + "\n"
    if is_pre_1_0(version):
        notes = notes.rstrip() + "\n\n" + PRE_1_0_DISCLAIMER.strip() + "\n"
    return notes


def cut_unreleased(text: str, version: str, release_date: str) -> str:
    parts = parse_version(version)
    version_s = format_version(parts)
    preamble, sections = parse_changelog(text)
    if any(section.title == version_s for section in sections):
        raise ChangelogError(f"CHANGELOG.md already has [{version_s}]")
    unreleased = section_by_title(sections, "Unreleased")
    body = compact_section_body(unreleased.body)
    if not has_changelog_items(body):
        raise ChangelogError("CHANGELOG.md [Unreleased] has no entries")
    rest = [section for section in sections if section.title.lower() != "unreleased"]
    sections = [
        ChangelogSection("Unreleased", None, ""),
        ChangelogSection(version_s, release_date, body),
        *rest,
    ]
    return render_changelog(preamble, sections)


def read_plugin_version(path: Path = PLUGIN_INIT) -> tuple[int, int, int]:
    match = PLUGIN_VERSION_RE.search(path.read_text(encoding="utf-8"))
    if not match:
        raise ChangelogError(f"no __version__ tuple in {path}")
    raw = match.group(0).split("=", 1)[1]
    nums = tuple(int(part.strip()) for part in raw.strip()[1:-1].split(","))
    if len(nums) != 3:
        raise ChangelogError(f"plugin version must be 3 ints in {path}")
    return nums[0], nums[1], nums[2]


def set_plugin_version(path: Path, version: tuple[int, int, int]) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = PLUGIN_VERSION_RE.subn(
        rf"\g<1>({version[0]}, {version[1]}, {version[2]})",
        text,
        count=1,
    )
    if count != 1:
        raise ChangelogError(f"could not write plugin version in {path}")
    path.write_text(updated, encoding="utf-8")


def set_package_version(path: Path, version: tuple[int, int, int]) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = PACKAGE_VERSION_RE.subn(
        rf'\g<1>"{format_version(version)}"',
        text,
        count=1,
    )
    if count != 1:
        raise ChangelogError(f"could not write package version in {path}")
    path.write_text(updated, encoding="utf-8")


def prepare_release(
    version: str,
    *,
    release_date: str | None = None,
    changelog_path: Path = CHANGELOG_PATH,
    plugin_init: Path = PLUGIN_INIT,
    package_init: Path = PACKAGE_INIT,
    write: bool = True,
) -> str:
    """Move [Unreleased] to ``version``, bump plugin + package, return notes."""
    parts = require_0x(parse_version(version))
    current = read_plugin_version(plugin_init)
    if parts <= current:
        raise ChangelogError(
            f"{format_version(parts)} must be greater than plugin "
            f"{format_version(current)}"
        )
    day = release_date or date.today().isoformat()
    text = changelog_path.read_text(encoding="utf-8")
    updated = cut_unreleased(text, format_version(parts), day)
    notes = notes_for_version(updated, format_version(parts))
    if write:
        changelog_path.write_text(updated, encoding="utf-8")
        set_plugin_version(plugin_init, parts)
        set_package_version(package_init, parts)
    return notes
