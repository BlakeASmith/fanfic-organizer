"""CLI for user configuration and rule files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ao3kit.config import (
    copy_example_rules,
    init_user_config,
    load_user_config,
)


def _print(data: object) -> None:
    if isinstance(data, (dict, list)):
        json.dump(data, sys.stdout, indent=2, ensure_ascii=False, default=str)
        sys.stdout.write("\n")
    else:
        print(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Manage ao3kit user config and rule files (.ao3kit/)."
    )
    parser.add_argument(
        "--home",
        type=Path,
        help="Config directory (default: AO3KIT_HOME or ./.ao3kit)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("path", help="Print config home path")
    init_p = sub.add_parser("init", help="Create config home + default rules")
    init_p.add_argument(
        "--force-rules",
        action="store_true",
        help="Overwrite rules/default.py with the starter template",
    )
    sub.add_parser("show", help="Show settings (JSON)")

    set_p = sub.add_parser("set", help="Set a settings key")
    set_p.add_argument("key")
    set_p.add_argument("value")

    rules = sub.add_parser("rules", help="Manage rule modules")
    rules_sub = rules.add_subparsers(dest="rules_command", required=True)
    rules_sub.add_parser("list", help="List rule files")
    new_p = rules_sub.add_parser("new", help="Create a new rule module")
    new_p.add_argument("name", help="Module stem, e.g. river_song")
    new_p.add_argument("--overwrite", action="store_true")
    show_p = rules_sub.add_parser("show", help="Print a rule module")
    show_p.add_argument("name")
    edit_p = rules_sub.add_parser(
        "edit",
        help="Write rule module from a file (or stdin with -)",
    )
    edit_p.add_argument("name")
    edit_p.add_argument(
        "source",
        help="Path to .py file, or '-' to read stdin",
    )
    active_p = rules_sub.add_parser("use", help="Set the active rules module")
    active_p.add_argument("name", help="Rule stem or path")
    rules_sub.add_parser(
        "path", help="Print path to the active rules module"
    )
    ex_p = rules_sub.add_parser(
        "install-example",
        help="Copy bundled example_tag_rules.py into rules/",
    )
    ex_p.add_argument("--name", default="example")

    args = parser.parse_args(argv)
    home = args.home

    if args.command == "path":
        cfg = load_user_config(home=home, ensure=False)
        _print(str(cfg.home))
        return 0

    if args.command == "init":
        cfg = init_user_config(home=home, force_rules=args.force_rules)
        _print(
            {
                "home": str(cfg.home),
                "config": str(cfg.config_path),
                "active_rules": str(cfg.active_rules_path()),
            }
        )
        return 0

    if args.command == "show":
        cfg = load_user_config(home=home, ensure=True)
        payload = cfg.settings.to_dict()
        payload["_home"] = str(cfg.home)
        payload["_active_rules_path"] = str(cfg.active_rules_path())
        _print(payload)
        return 0

    if args.command == "set":
        cfg = load_user_config(home=home, ensure=True)
        key = args.key
        if not hasattr(cfg.settings, key) or key == "version":
            print(f"Unknown settings key: {key}", file=sys.stderr)
            return 1
        raw = args.value
        current = getattr(cfg.settings, key)
        if isinstance(current, bool):
            value: object = raw.lower() in {"1", "true", "yes", "on"}
        elif isinstance(current, int) and not isinstance(current, bool):
            value = int(raw)
        elif isinstance(current, float):
            value = float(raw)
        else:
            value = raw
        cfg.update_settings(**{key: value})
        _print(cfg.settings.to_dict())
        return 0

    if args.command == "rules":
        cfg = load_user_config(home=home, ensure=True)
        cmd = args.rules_command
        if cmd == "list":
            active = cfg.active_rules_path()
            rows = []
            for path in cfg.list_rule_files():
                rows.append(
                    {
                        "name": path.stem,
                        "path": str(path),
                        "active": path.resolve() == active.resolve(),
                    }
                )
            _print(rows)
            return 0
        if cmd == "new":
            path = cfg.create_rule(args.name, overwrite=args.overwrite)
            _print(str(path))
            return 0
        if cmd == "show":
            _print(cfg.read_rule(args.name))
            return 0
        if cmd == "edit":
            if args.source == "-":
                source = sys.stdin.read()
            else:
                source = Path(args.source).read_text(encoding="utf-8")
            path = cfg.write_rule(args.name, source)
            _print(str(path))
            return 0
        if cmd == "use":
            path = cfg.set_active_rules(args.name)
            _print(str(path))
            return 0
        if cmd == "path":
            _print(str(cfg.active_rules_path()))
            return 0
        if cmd == "install-example":
            path = copy_example_rules(cfg, name=args.name)
            _print(str(path))
            return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
