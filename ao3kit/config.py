"""Persistent user configuration and rule files for ao3kit.

Default location: ``<project>/.ao3kit/`` (override with ``AO3KIT_HOME``).

Layout::

    .ao3kit/
      config.yaml          # settings
      mappings.yaml        # extra keep/map/drop rows
      collections.yaml     # collection membership rules + per-work pins
      ao3_session.json     # cached AO3 cookies (not the password)
      rules/
        default.py         # active rules module (code-first)
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

DEFAULT_RULES_FILENAME = "default.py"
CONFIG_FILENAME = "config.yaml"
MAPPINGS_FILENAME = "mappings.yaml"
COLLECTIONS_FILENAME = "collections.yaml"
RULES_DIRNAME = "rules"

_SAFE_RULE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")

DEFAULT_RULES_TEMPLATE = '''\
"""User tag rules for ao3kit.

Simple keep / rename / drop rows live in ``.ao3kit/mappings.yaml``
(Calibre plugin: Collections & tag rules → Tag rules, or ``python -m ao3kit config mappings``).
Collection membership lives in ``.ao3kit/collections.yaml``
(``python -m ao3kit config collections``).
Use this Python module for custom logic that YAML cannot express.
Docs: TagRule, RuleContext, RuleEffect, KeepSeparateRule, CollectRule, …
"""

from __future__ import annotations

from ao3kit.tags.rules import (
    CollectRule,
    KeepSeparateRule,
    RuleContext,
    RuleEffect,
    TagRule,
    TagRulesConfig,
)


class ExampleCollection(TagRule):
    """Replace with your own logic — return RuleEffect or None."""

    id = "example-collection"
    priority = 50

    def apply(self, ctx: RuleContext) -> RuleEffect | None:
        # if "river song" in ctx.original.lower():
        #     return RuleEffect(collections=["River Song"])
        return None


RULES = TagRulesConfig(
    resolve_canonical=True,
    drop_unmarked=False,
    rules=[
        # KeepSeparateRule(
        #     id="keep-jegulus",
        #     priority=100,
        #     tags_ci=["Jegulus"],
        #     collections=["Jegulus"],
        #     stop=True,
        # ),
        # CollectRule(
        #     id="river-song",
        #     priority=50,
        #     collections=["River Song"],
        #     canonical_ci=["River Song"],
        #     contains_ci=["River Song"],
        # ),
        ExampleCollection(),
    ],
)
'''


DEFAULT_REQUEST_DELAY = 1.5

COVER_COLOR_SEEDS = ("fandom", "relationship", "author", "title", "work_id")
COVER_COLOR_MODES = ("hash", "palette", "solid")
COVER_IMAGE_FORMATS = ("png", "jpeg")
COVER_FIELDS = (
    "title",
    "author",
    "fandom",
    "relationship",
    "series",
    "rating",
    "wordcount",
    "score",
    "complete",
)
DEFAULT_COVER_FIELDS = ("title", "author", "wordcount", "score")


def _as_str_list(value: Any) -> list[str]:
    if value is None or value is False:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def parse_color_map(value: Any) -> dict[str, str]:
    """Parse ``fandom_colors`` from a dict, JSON object, or ``Name=#hex`` lines."""
    if not value:
        return {}
    if isinstance(value, dict):
        return {
            str(key).strip(): str(color).strip()
            for key, color in value.items()
            if str(key).strip() and str(color).strip()
        }
    text = str(value).strip()
    if not text:
        return {}
    if text.startswith("{"):
        try:
            import json

            parsed = json.loads(text)
        except (ImportError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            return parse_color_map(parsed)
    mapping: dict[str, str] = {}
    for raw in text.replace(";", "\n").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            name, color = line.split("=", 1)
        elif ":" in line:
            name, color = line.split(":", 1)
        else:
            continue
        name = name.strip().strip('"').strip("'")
        color = color.strip().strip(",").strip('"').strip("'")
        if name and color:
            mapping[name] = color
    return mapping


def format_color_map(mapping: dict[str, str] | None) -> str:
    if not mapping:
        return ""
    return "\n".join(f"{name} = {color}" for name, color in mapping.items())


@dataclass
class CoverSettings:
    """How generated EPUB / Calibre covers look and when they are applied.

    Style defaults follow alexwlchan's AO3 cover tool: 600×900, Georgia,
    title + author, fandom-seeded gradient. Word count and quality score
    are on by default; other fields and palettes are opt-in. Nested under
    ``cover:`` in ``config.yaml``.
    """

    enabled: bool = True
    replace_existing: bool = True
    set_calibre_cover: bool = True
    width: int = 600
    height: int = 900
    font: str = "Georgia"
    font_path: str = ""
    title_size: int = 88
    author_size: int = 62
    header_size: int = 28
    footer_size: int = 24
    title_max_lines: int = 5
    author_max_lines: int = 2
    header_max_lines: int = 2
    title_color: str = "#ffffff"
    author_color: str = "#ffffffcc"
    header_color: str = "#ffffffcc"
    footer_color: str = "#ffffffcc"
    fields: list[str] = field(default_factory=lambda: list(DEFAULT_COVER_FIELDS))
    color_seed: str = "fandom"
    color_mode: str = "hash"
    gradient: bool = True
    solid_color: str = "#2c3e6b"
    palette: list[str] = field(default_factory=list)
    fandom_colors: dict[str, str] = field(default_factory=dict)
    saturation_min: float = 0.7
    saturation_max: float = 1.0
    lightness_top: float = 0.35
    lightness_bottom: float = 0.2
    seed_words: int = 2
    image_format: str = "png"
    jpeg_quality: int = 90
    padding: float = 0.125
    title_y: float = 0.18
    author_y: float = 0.82
    header_y: float = 0.07
    footer_y: float = 0.93
    uppercase_title: bool = False
    text_shadow: bool = False
    border_px: int = 0
    border_color: str = "#ffffff40"
    cover_href: str = "media/cover.png"
    cover_page: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> CoverSettings:
        data = data or {}
        known = {item.name for item in fields(cls)}
        kwargs: dict[str, Any] = {}
        for key, value in data.items():
            if key not in known:
                continue
            if key in {"fields", "palette"}:
                value = _as_str_list(value)
            elif key == "fandom_colors":
                value = parse_color_map(value)
            kwargs[key] = value
        settings = cls(**kwargs)
        settings.fields = [
            name.lower()
            for name in settings.fields
            if name.lower() in COVER_FIELDS
        ] or list(DEFAULT_COVER_FIELDS)
        seed = str(settings.color_seed or "fandom").strip().lower()
        settings.color_seed = seed if seed in COVER_COLOR_SEEDS else "fandom"
        mode = str(settings.color_mode or "hash").strip().lower()
        settings.color_mode = mode if mode in COVER_COLOR_MODES else "hash"
        fmt = str(settings.image_format or "png").strip().lower()
        if fmt in {"jpg", "jpeg"}:
            settings.image_format = "jpeg"
        elif fmt != "png":
            settings.image_format = "png"
        return settings

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def shows(self, name: str) -> bool:
        return name.lower() in {item.lower() for item in self.fields}


@dataclass
class UserSettings:
    """Serializable user preferences (config.yaml)."""

    version: int = 1
    request_delay: float = DEFAULT_REQUEST_DELAY
    resolve_canonical: bool = True
    drop_unmarked: bool = False
    drop_errors: bool = False
    # Relative to the config home, or absolute.
    active_rules: str = f"{RULES_DIRNAME}/{DEFAULT_RULES_FILENAME}"
    tag_cache_enabled: bool = True
    # Days before a synonym/canonical tree is purged (0 = never expire).
    tag_cache_ttl_days: float = 90.0
    follow_canonical: bool = True
    include_metatags: bool = True
    # Extra seconds the background tag warmer waits after each fetch.
    tag_warm_interval: float = 10.0
    # On recompute, collections added by hand on a book become a pin rule.
    collections_remember_manual_adds: bool = True
    # Optional UI / scrape defaults
    default_language_id: str = "en"
    notes: str = ""
    cover: CoverSettings = field(default_factory=CoverSettings)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> UserSettings:
        data = data or {}
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in known}
        cover = kwargs.get("cover")
        if isinstance(cover, CoverSettings):
            kwargs["cover"] = cover
        else:
            kwargs["cover"] = CoverSettings.from_dict(
                cover if isinstance(cover, dict) else None
            )
        return cls(**kwargs)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class UserConfig:
    """On-disk user home: settings + rule modules."""

    home: Path
    settings: UserSettings = field(default_factory=UserSettings)

    @property
    def config_path(self) -> Path:
        return self.home / CONFIG_FILENAME

    @property
    def rules_dir(self) -> Path:
        return self.home / RULES_DIRNAME

    @property
    def mappings_path(self) -> Path:
        return self.home / MAPPINGS_FILENAME

    @property
    def collections_path(self) -> Path:
        return self.home / COLLECTIONS_FILENAME

    def active_rules_path(self) -> Path:
        raw = Path(self.settings.active_rules)
        if raw.is_absolute():
            return raw
        return (self.home / raw).resolve()

    def ensure_layout(self, *, write_default_rules: bool = True) -> None:
        """Create home, config.yaml, and rules/default.py if missing."""
        self.home.mkdir(parents=True, exist_ok=True)
        self.rules_dir.mkdir(parents=True, exist_ok=True)
        if not self.config_path.is_file():
            self.save()
        default_rules = self.rules_dir / DEFAULT_RULES_FILENAME
        if write_default_rules and not default_rules.is_file():
            default_rules.write_text(DEFAULT_RULES_TEMPLATE, encoding="utf-8")

    def save(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("PyYAML required: pip install pyyaml") from exc
        text = yaml.safe_dump(
            self.settings.to_dict(),
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )
        tmp = self.config_path.with_suffix(".yaml.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self.config_path)

    def reload(self) -> UserSettings:
        self.settings = _read_settings(self.config_path)
        return self.settings

    def update_settings(self, **changes: Any) -> UserSettings:
        data = self.settings.to_dict()
        for key, value in changes.items():
            if is_dataclass(value) and not isinstance(value, type):
                value = asdict(value)
            data[key] = value
        self.settings = UserSettings.from_dict(data)
        self.save()
        return self.settings

    def list_rule_files(self) -> list[Path]:
        if not self.rules_dir.is_dir():
            return []
        files = sorted(self.rules_dir.glob("*.py"))
        return [p for p in files if p.name != "__init__.py"]

    def rule_path(self, name: str) -> Path:
        stem = name.removesuffix(".py")
        if not _SAFE_RULE_NAME.match(stem):
            raise ValueError(
                f"Invalid rule name {name!r}: use letters, numbers, _ or -"
            )
        return self.rules_dir / f"{stem}.py"

    def read_rule(self, name: str) -> str:
        path = self.rule_path(name)
        if not path.is_file():
            raise FileNotFoundError(f"No rule file: {path}")
        return path.read_text(encoding="utf-8")

    def write_rule(self, name: str, source: str) -> Path:
        path = self.rule_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".py.tmp")
        tmp.write_text(source, encoding="utf-8")
        tmp.replace(path)
        return path

    def create_rule(
        self,
        name: str,
        *,
        source: str | None = None,
        overwrite: bool = False,
    ) -> Path:
        path = self.rule_path(name)
        if path.exists() and not overwrite:
            raise FileExistsError(f"Rule already exists: {path}")
        return self.write_rule(name, source or DEFAULT_RULES_TEMPLATE)

    def set_active_rules(self, name_or_path: str) -> Path:
        """Point active_rules at a rules/*.py file (by stem or relative path)."""
        candidate = Path(name_or_path)
        if candidate.suffix != ".py":
            path = self.rule_path(name_or_path)
        elif candidate.is_absolute():
            path = candidate
        else:
            path = (self.home / candidate).resolve()
            if not path.is_file():
                path = self.rule_path(candidate.stem)
        if not path.is_file():
            raise FileNotFoundError(f"Rules file not found: {path}")
        try:
            rel = path.resolve().relative_to(self.home.resolve())
            self.settings.active_rules = str(rel)
        except ValueError:
            self.settings.active_rules = str(path.resolve())
        self.save()
        return path

    def load_mappings(self):
        """Load extra tag mapping rows from ``mappings.yaml``."""
        from ao3kit.tags.mappings import load_mappings

        return load_mappings(self.mappings_path)

    def save_mappings(self, mappings) -> Path:
        from ao3kit.tags.mappings import save_mappings

        return save_mappings(self.mappings_path, mappings)

    def load_collection_rules(self):
        """Load collection membership rules (``.ao3kit/collections.yaml``)."""
        from ao3kit.tags.collections import load_collection_rules

        return load_collection_rules(self.collections_path)

    def save_collection_rules(self, rules) -> Path:
        from ao3kit.tags.collections import save_collection_rules

        return save_collection_rules(self.collections_path, rules)

    def with_mappings(self, config):
        """Layer extra tag mappings on top of a Python/YAML ``TagRulesConfig``."""
        from ao3kit.tags.mappings import merge_mapping_rules

        return merge_mapping_rules(config, self.load_mappings())

    def load_active_rules(self):
        """Load active Python/YAML rules plus extra tag mappings."""
        from ao3kit.tags.rules import TagRulesConfig, load_tag_rules

        path = self.active_rules_path()
        if path.is_file():
            config = load_tag_rules(path)
        else:
            config = TagRulesConfig()
        return self.with_mappings(config)


def default_home(project_root: Path | None = None) -> Path:
    """Resolve config home: ``AO3KIT_HOME`` or ``<project>/.ao3kit``."""
    env = os.environ.get("AO3KIT_HOME", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    root = project_root or Path(__file__).resolve().parents[1]
    return (root / ".ao3kit").resolve()


def _read_settings(path: Path) -> UserSettings:
    if not path.is_file():
        return UserSettings()
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PyYAML required: pip install pyyaml") from exc
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Invalid config file (expected mapping): {path}")
    return UserSettings.from_dict(data)


def default_request_delay() -> float:
    """Work-page / search / download interval from ``config.yaml``."""
    return float(load_user_config().settings.request_delay)


def resolve_request_delay(requested: float | None) -> float:
    """``None`` means use :func:`default_request_delay`; ``0`` is kept as-is."""
    if requested is not None:
        return float(requested)
    return default_request_delay()


def load_cover_settings(home: Path | None = None) -> CoverSettings:
    """Cover style from ``config.yaml`` (defaults if unset)."""
    return load_user_config(home=home).settings.cover


def merge_cover_settings(
    base: CoverSettings | None = None,
    *,
    home: Path | None = None,
    **changes: Any,
) -> CoverSettings:
    """Return ``base`` (or disk settings) with non-``None`` overrides applied."""
    current = base or load_cover_settings(home=home)
    data = current.to_dict()
    for key, value in changes.items():
        if value is None:
            continue
        data[key] = value
    return CoverSettings.from_dict(data)


def load_user_config(
    home: Path | None = None,
    *,
    ensure: bool = False,
) -> UserConfig:
    """Load user config from disk (optionally create defaults)."""
    cfg = UserConfig(home=home or default_home(), settings=UserSettings())
    if ensure:
        cfg.ensure_layout()
    if cfg.config_path.is_file():
        cfg.reload()
    return cfg


def init_user_config(
    home: Path | None = None,
    *,
    force_rules: bool = False,
) -> UserConfig:
    """Initialize config home; optionally reset default rules from template."""
    cfg = load_user_config(home=home, ensure=True)
    if force_rules:
        target = cfg.rules_dir / DEFAULT_RULES_FILENAME
        target.write_text(DEFAULT_RULES_TEMPLATE, encoding="utf-8")
    return cfg


def copy_example_rules(cfg: UserConfig, *, name: str = "example") -> Path:
    """Copy repo ``example_tag_rules.py`` into the user's rules dir."""
    project = Path(__file__).resolve().parents[1]
    src = project / "example_tag_rules.py"
    if not src.is_file():
        raise FileNotFoundError(f"Bundled example missing: {src}")
    dest = cfg.rule_path(name)
    if dest.exists():
        raise FileExistsError(f"Already exists: {dest}")
    shutil.copy2(src, dest)
    return dest
