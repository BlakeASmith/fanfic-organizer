"""Code-first tag rules: arbitrary Python with optional YAML wiring.

Preferred authoring is a Python module that exports ``RULES`` (a
``TagRulesConfig`` or list of ``TagRule`` instances). YAML can still
instantiate registered / importable rule classes for simple cases.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import re
import sys
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Sequence

from ao3kit.tags.metadata import ResolvedTag, TagResolver

MappingAction = Literal["default", "keep_separate", "map_to", "drop"]

# Short names for YAML ``use:`` entries.
_RULE_REGISTRY: dict[str, type[TagRule]] = {}


def register_rule(name: str) -> Callable[[type[TagRule]], type[TagRule]]:
    """Decorator to register a rule class under a short YAML alias."""

    def decorator(cls: type[TagRule]) -> type[TagRule]:
        _RULE_REGISTRY[name] = cls
        return cls

    return decorator


@dataclass
class RuleContext:
    """Inputs available to a rule for one tag."""

    resolved: ResolvedTag

    @property
    def original(self) -> str:
        return self.resolved.original

    @property
    def canonical(self) -> str:
        return self.resolved.resolved

    @property
    def status(self) -> str:
        return self.resolved.status

    @property
    def category(self) -> str | None:
        return self.resolved.category


@dataclass
class RuleEffect:
    """What a rule wants to do when it applies.

    Mapping fields compete by rule priority (first wins). Collections from
    every applying rule are unioned. ``stop`` ends evaluation for the tag.
    """

    keep_separate: bool = False
    map_to: str | None = None
    drop: bool = False
    collections: list[str] = field(default_factory=list)
    stop: bool = False

    @property
    def has_mapping(self) -> bool:
        return bool(self.keep_separate or self.map_to or self.drop)


class TagRule(ABC):
    """Base class for tag rules — implement ``apply`` with arbitrary logic."""

    id: str = "rule"
    priority: int = 0
    enabled: bool = True
    description: str = ""

    def __init__(
        self,
        *,
        id: str | None = None,
        priority: int | None = None,
        enabled: bool = True,
        description: str = "",
        **_ignored: Any,
    ) -> None:
        if id is not None:
            self.id = id
        if priority is not None:
            self.priority = priority
        self.enabled = enabled
        if description:
            self.description = description

    @abstractmethod
    def apply(self, ctx: RuleContext) -> RuleEffect | None:
        """Return an effect if this rule applies to ``ctx``, else ``None``."""


def rule(
    *,
    id: str,
    priority: int = 0,
    enabled: bool = True,
    description: str = "",
) -> Callable[[Callable[[RuleContext], RuleEffect | None]], TagRule]:
    """Decorator to turn a function into a ``TagRule``."""

    def decorator(fn: Callable[[RuleContext], RuleEffect | None]) -> TagRule:
        class FnRule(TagRule):
            def apply(self, ctx: RuleContext) -> RuleEffect | None:
                return fn(ctx)

        FnRule.__name__ = fn.__name__
        FnRule.__qualname__ = fn.__qualname__
        return FnRule(
            id=id,
            priority=priority,
            enabled=enabled,
            description=description or (fn.__doc__ or "").strip(),
        )

    return decorator


# ---------------------------------------------------------------------------
# Built-in configurable rules (also usable from YAML)
# ---------------------------------------------------------------------------


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


@dataclass
class MatchSpec:
    """Optional declarative matcher for built-ins (AND across fields)."""

    always: bool = False
    tags: list[str] = field(default_factory=list)
    tags_ci: list[str] = field(default_factory=list)
    prefixes: list[str] = field(default_factory=list)
    suffixes: list[str] = field(default_factory=list)
    contains: list[str] = field(default_factory=list)
    contains_ci: list[str] = field(default_factory=list)
    regex: list[str] = field(default_factory=list)
    canonical: list[str] = field(default_factory=list)
    canonical_ci: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    statuses: list[str] = field(default_factory=list)
    # If True, any single field matching is enough (OR). Default is AND.
    match_any: bool = False

    @classmethod
    def from_kwargs(cls, data: dict[str, Any]) -> MatchSpec:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        payload = {k: v for k, v in data.items() if k in known}
        for key in (
            "tags",
            "tags_ci",
            "prefixes",
            "suffixes",
            "contains",
            "contains_ci",
            "regex",
            "canonical",
            "canonical_ci",
            "categories",
            "statuses",
        ):
            if key in payload:
                payload[key] = _as_list(payload[key])
        return cls(**payload)

    def matches(self, ctx: RuleContext) -> bool:
        if self.always:
            return True

        original = ctx.original
        canonical = ctx.canonical
        checks: list[bool] = []

        if self.tags:
            checks.append(original in self.tags)
        if self.tags_ci:
            checks.append(original.lower() in {t.lower() for t in self.tags_ci})
        if self.prefixes:
            checks.append(any(original.startswith(p) for p in self.prefixes))
        if self.suffixes:
            checks.append(any(original.endswith(s) for s in self.suffixes))
        if self.contains:
            checks.append(any(part in original for part in self.contains))
        if self.contains_ci:
            lowered = original.lower()
            checks.append(any(part.lower() in lowered for part in self.contains_ci))
        if self.regex:
            checks.append(
                any(re.compile(pattern).search(original) for pattern in self.regex)
            )
        if self.canonical:
            checks.append(canonical in self.canonical)
        if self.canonical_ci:
            checks.append(canonical.lower() in {t.lower() for t in self.canonical_ci})
        if self.categories:
            checks.append((ctx.category or "") in self.categories)
        if self.statuses:
            checks.append(ctx.status in self.statuses)

        if not checks:
            return False
        return any(checks) if self.match_any else all(checks)


class MatchRule(TagRule):
    """Rule with a declarative matcher + fixed effect (YAML-friendly)."""

    def __init__(
        self,
        *,
        match: MatchSpec | dict[str, Any] | None = None,
        effect: RuleEffect | None = None,
        keep_separate: bool = False,
        map_to: str | None = None,
        drop: bool = False,
        collections: Sequence[str] | str | None = None,
        stop: bool = False,
        **kwargs: Any,
    ) -> None:
        # Pull matcher keys out of kwargs so YAML can be flat.
        match_keys = {f.name for f in MatchSpec.__dataclass_fields__.values()}
        match_data = dict(match) if isinstance(match, dict) else {}
        if match is None or isinstance(match, dict):
            for key in list(kwargs.keys()):
                if key in match_keys:
                    match_data[key] = kwargs.pop(key)
        super().__init__(**kwargs)
        self.match = (
            match
            if isinstance(match, MatchSpec)
            else MatchSpec.from_kwargs(match_data)
        )
        self.effect = effect or RuleEffect(
            keep_separate=keep_separate,
            map_to=map_to,
            drop=drop,
            collections=_as_list(collections),
            stop=stop,
        )

    def apply(self, ctx: RuleContext) -> RuleEffect | None:
        if not self.match.matches(ctx):
            return None
        return self.effect


@register_rule("match")
@register_rule("declarative")
class DeclarativeRule(MatchRule):
    """Alias for ``MatchRule`` registered for YAML ``use: match``."""


@register_rule("keep_separate")
class KeepSeparateRule(MatchRule):
    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("keep_separate", True)
        super().__init__(**kwargs)


@register_rule("map_to")
class MapToRule(MatchRule):
    def __init__(self, map_to: str, **kwargs: Any) -> None:
        kwargs["map_to"] = map_to
        super().__init__(**kwargs)


@register_rule("collect")
class CollectRule(MatchRule):
    def __init__(self, collections: Sequence[str] | str, **kwargs: Any) -> None:
        kwargs["collections"] = collections
        # Collection rules often want OR across name/canonical hints.
        kwargs.setdefault("match_any", True)
        super().__init__(**kwargs)


@register_rule("drop")
class DropRule(MatchRule):
    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("drop", True)
        super().__init__(**kwargs)


# ---------------------------------------------------------------------------
# Config + engine
# ---------------------------------------------------------------------------


@dataclass
class TagRulesConfig:
    """Pipeline defaults + precedence-ordered rule instances."""

    version: int = 1
    resolve_canonical: bool = True
    drop_unmarked: bool = False
    drop_errors: bool = False
    rules: list[TagRule] = field(default_factory=list)

    def sorted_rules(self) -> list[TagRule]:
        indexed = [
            (rule.priority, index, rule) for index, rule in enumerate(self.rules)
        ]
        indexed.sort(key=lambda item: (-item[0], item[1]))
        return [rule for _priority, _index, rule in indexed if rule.enabled]


@dataclass
class RuledTag:
    original: str
    mapped: str | None
    status: str
    category: str | None = None
    collections: list[str] = field(default_factory=list)
    applied_rules: list[str] = field(default_factory=list)
    mapping_action: MappingAction = "default"
    mapping_rule: str | None = None
    dropped: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RuledTagsResult:
    original: list[str]
    tags: list[RuledTag]
    simplified: list[str]
    dropped: list[str] = field(default_factory=list)
    collections: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "original": self.original,
            "tags": [t.to_dict() for t in self.tags],
            "simplified": self.simplified,
            "dropped": self.dropped,
            "collections": self.collections,
        }


class TagRulesEngine:
    """Run code rules (priority order) on top of ``TagResolver``."""

    def __init__(self, config: TagRulesConfig, resolver: TagResolver) -> None:
        self.config = config
        self.resolver = resolver
        self._rules = config.sorted_rules()

    def apply_one(self, name: str) -> RuledTag:
        resolved = self.resolver.resolve_one(name)
        ctx = RuleContext(resolved=resolved)
        collections: list[str] = []
        applied: list[str] = []
        mapping_action: MappingAction = "default"
        mapping_rule: str | None = None
        mapped = (
            resolved.resolved if self.config.resolve_canonical else resolved.original
        )
        dropped = False

        for rule in self._rules:
            effect = rule.apply(ctx)
            if effect is None:
                continue
            applied.append(rule.id)
            for collection in effect.collections:
                if collection not in collections:
                    collections.append(collection)

            if mapping_action == "default" and effect.has_mapping:
                mapping_rule = rule.id
                if effect.drop:
                    mapping_action = "drop"
                    mapped = None
                    dropped = True
                elif effect.keep_separate:
                    mapping_action = "keep_separate"
                    mapped = resolved.original
                    dropped = False
                elif effect.map_to:
                    mapping_action = "map_to"
                    mapped = effect.map_to
                    dropped = False

            if effect.stop:
                break

        if mapping_action == "default":
            if resolved.status == "unmarked" and self.config.drop_unmarked:
                dropped = True
                mapped = None
            elif resolved.status == "error" and self.config.drop_errors:
                dropped = True
                mapped = None
            elif resolved.status == "missing":
                dropped = True
                mapped = None

        return RuledTag(
            original=resolved.original,
            mapped=mapped,
            status=resolved.status,
            category=resolved.category,
            collections=collections,
            applied_rules=applied,
            mapping_action=mapping_action,
            mapping_rule=mapping_rule,
            dropped=dropped,
        )

    def apply(self, names: list[str]) -> RuledTagsResult:
        original = list(names)
        tags = [self.apply_one(name) for name in original]
        simplified: list[str] = []
        dropped: list[str] = []
        seen: set[str] = set()
        collections: dict[str, list[str]] = {}

        for item in tags:
            for collection in item.collections:
                bucket = collections.setdefault(collection, [])
                if item.original not in bucket:
                    bucket.append(item.original)
            if item.dropped or item.mapped is None:
                dropped.append(item.original)
                continue
            if item.mapped in seen:
                dropped.append(item.original)
                continue
            seen.add(item.mapped)
            simplified.append(item.mapped)

        return RuledTagsResult(
            original=original,
            tags=tags,
            simplified=simplified,
            dropped=dropped,
            collections=collections,
        )


# ---------------------------------------------------------------------------
# Loading: Python modules (preferred) or YAML/JSON wiring
# ---------------------------------------------------------------------------


def _resolve_use(use: str) -> type[TagRule]:
    if use in _RULE_REGISTRY:
        return _RULE_REGISTRY[use]
    if ":" in use:
        module_name, _, attr = use.partition(":")
    elif "." in use:
        module_name, _, attr = use.rpartition(".")
    else:
        raise ValueError(
            f"Unknown rule {use!r}. Use a registry alias or module:Class path."
        )
    module = importlib.import_module(module_name)
    cls = getattr(module, attr)
    if not isinstance(cls, type) or not issubclass(cls, TagRule):
        raise TypeError(f"{use!r} is not a TagRule subclass")
    return cls


def _rule_from_mapping(data: dict[str, Any]) -> TagRule:
    use = data.get("use") or data.get("class") or "match"
    cls = _resolve_use(str(use))
    kwargs = {k: v for k, v in data.items() if k not in {"use", "class"}}
    return cls(**kwargs)


def _config_from_mapping(data: dict[str, Any]) -> TagRulesConfig:
    rules = [_rule_from_mapping(item) for item in (data.get("rules") or [])]
    return TagRulesConfig(
        version=int(data.get("version", 1)),
        resolve_canonical=bool(data.get("resolve_canonical", True)),
        drop_unmarked=bool(data.get("drop_unmarked", False)),
        drop_errors=bool(data.get("drop_errors", False)),
        rules=rules,
    )


def _load_python_module(path: Path) -> Any:
    module_name = f"ao3kit_user_rules_{path.stem}_{abs(hash(path.resolve()))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot import rules module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _config_from_python(module: Any) -> TagRulesConfig:
    if hasattr(module, "build_rules") and callable(module.build_rules):
        built = module.build_rules()
        if isinstance(built, TagRulesConfig):
            return built
        if isinstance(built, list):
            return TagRulesConfig(rules=list(built))
        raise TypeError("build_rules() must return TagRulesConfig or list[TagRule]")

    for attr in ("RULES", "rules", "CONFIG", "config"):
        if not hasattr(module, attr):
            continue
        value = getattr(module, attr)
        if isinstance(value, TagRulesConfig):
            return value
        if isinstance(value, list):
            return TagRulesConfig(rules=list(value))
        if callable(value):
            return _config_from_python(type("M", (), {"build_rules": staticmethod(value)}))
    raise AttributeError(
        "Rules module must export RULES / CONFIG (TagRulesConfig or list) "
        "or build_rules()"
    )


def load_tag_rules(path: Path | str) -> TagRulesConfig:
    """Load rules from a Python module (preferred) or YAML/JSON."""
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix == ".py":
        return _config_from_python(_load_python_module(path))

    text = path.read_text(encoding="utf-8")
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "PyYAML is required for .yaml rules files. pip install pyyaml"
            ) from exc
        data = yaml.safe_load(text) or {}
    elif suffix == ".json":
        data = json.loads(text)
    else:
        try:
            import yaml

            data = yaml.safe_load(text) or {}
        except Exception:
            data = json.loads(text)

    if not isinstance(data, dict):
        raise ValueError(f"Rules file must be a mapping: {path}")
    return _config_from_mapping(data)


# Back-compat aliases used by older tests / docs.
TagMatch = MatchSpec
TagRuleActions = RuleEffect


def rule_matches(rule: TagRule, resolved: ResolvedTag) -> bool:
    """Whether a rule returns an effect for ``resolved`` (test helper)."""
    return rule.apply(RuleContext(resolved=resolved)) is not None
