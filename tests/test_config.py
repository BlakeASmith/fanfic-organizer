"""Tests for user config storage."""

from __future__ import annotations

from pathlib import Path

import pytest

from ao3kit.config import (
    UserSettings,
    init_user_config,
    load_user_config,
)
from ao3kit.tags.rules import load_tag_rules


def test_init_creates_config_and_default_rules(tmp_path: Path):
    home = tmp_path / "home"
    cfg = init_user_config(home=home)
    assert cfg.config_path.is_file()
    assert (cfg.rules_dir / "default.py").is_file()
    assert cfg.settings.request_delay == 1.5
    rules = load_tag_rules(cfg.active_rules_path())
    assert rules.resolve_canonical is True


def test_update_settings_persists(tmp_path: Path):
    home = tmp_path / "home"
    cfg = init_user_config(home=home)
    cfg.update_settings(request_delay=7.5, drop_unmarked=True, notes="hi")
    reloaded = load_user_config(home=home)
    assert reloaded.settings.request_delay == 7.5
    assert reloaded.settings.drop_unmarked is True
    assert reloaded.settings.notes == "hi"


def test_create_and_activate_rule(tmp_path: Path):
    cfg = init_user_config(home=tmp_path / "home")
    path = cfg.create_rule(
        "custom",
        source=(
            "from ao3kit.tags.rules import TagRulesConfig\n"
            "RULES = TagRulesConfig(resolve_canonical=False, rules=[])\n"
        ),
    )
    assert path.name == "custom.py"
    cfg.set_active_rules("custom")
    assert cfg.active_rules_path().name == "custom.py"
    loaded = cfg.load_active_rules()
    assert loaded.resolve_canonical is False


def test_reject_unsafe_rule_name(tmp_path: Path):
    cfg = init_user_config(home=tmp_path / "home")
    with pytest.raises(ValueError):
        cfg.create_rule("../evil")


def test_settings_from_dict_ignores_unknown():
    settings = UserSettings.from_dict({"request_delay": 3, "nope": 1})
    assert settings.request_delay == 3.0
