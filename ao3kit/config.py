"""Persistent user configuration and rule files for ao3kit.

Default location: ``<project>/.ao3kit/`` (override with ``AO3KIT_HOME``).

Layout::

    .ao3kit/
      config.yaml          # settings
      rules/
        default.py         # active rules module (code-first)
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

DEFAULT_RULES_FILENAME = "default.py"
CONFIG_FILENAME = "config.yaml"
RULES_DIRNAME = "rules"

_SAFE_RULE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")

DEFAULT_RULES_TEMPLATE = '''\
"""User tag rules for ao3kit.

Edit this file (CLI or Settings UI), then re-run apply / use the web tools.
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


@dataclass
class UserSettings:
    """Serializable user preferences (config.yaml)."""

    version: int = 1
    request_delay: float = 1.5
    resolve_canonical: bool = True
    drop_unmarked: bool = False
    drop_errors: bool = False
    # Relative to the config home, or absolute.
    active_rules: str = f"{RULES_DIRNAME}/{DEFAULT_RULES_FILENAME}"
    tag_cache_enabled: bool = True
    # Days before a synonym/canonical tree is purged (0 = never expire).
    tag_cache_ttl_days: float = 90.0
    follow_canonical: bool = True
    # Optional UI / scrape defaults
    default_language_id: str = "en"
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> UserSettings:
        data = data or {}
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

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
        data.update(changes)
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

    def load_active_rules(self):
        """Load ``TagRulesConfig`` from the active rules module."""
        from ao3kit.tags.rules import load_tag_rules

        path = self.active_rules_path()
        if not path.is_file():
            raise FileNotFoundError(
                f"Active rules file missing: {path}. "
                f"Run: python -m ao3kit config init"
            )
        return load_tag_rules(path)


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
