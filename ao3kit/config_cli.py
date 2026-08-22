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
from ao3kit.tags.collections import MATCH_KINDS as COLLECTION_MATCH_KINDS
from ao3kit.tags.mappings import MATCH_KINDS


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

    maps = sub.add_parser(
        "mappings",
        help="Tag keep / rename / drop on top of AO3 cleanup",
    )
    maps_sub = maps.add_subparsers(dest="mappings_command", required=True)
    maps_sub.add_parser("list", help="List collection and tag rules")
    maps_sub.add_parser("path", help="Print mappings.yaml path")

    add_p = maps_sub.add_parser("add", help="Add a collection or tag rule")
    add_p.add_argument(
        "--match",
        default="mentions",
        choices=list(MATCH_KINDS),
        help="contains (mentions) or is exactly (is_ci); default: contains",
    )
    add_p.add_argument(
        "--values",
        required=True,
        help="Comma-separated tag names or fragments",
    )
    add_p.add_argument(
        "--action",
        required=True,
        choices=["keep_separate", "map_to", "drop", "collect"],
    )
    add_p.add_argument("--map-to", default="", help="Target tag for --action map_to")
    add_p.add_argument(
        "--collection",
        action="append",
        default=[],
        help="Collection name (repeatable)",
    )
    add_p.add_argument("--stop", action="store_true", help="Skip later rules")
    add_p.add_argument("--id", default="", help="Optional mapping id")
    add_p.add_argument("--disabled", action="store_true")

    set_p = maps_sub.add_parser("set", help="Update a mapping in place")
    set_p.add_argument("id")
    set_p.add_argument(
        "--match",
        default="tag_ci",
        choices=list(MATCH_KINDS),
    )
    set_p.add_argument("--values", required=True)
    set_p.add_argument(
        "--action",
        required=True,
        choices=["keep_separate", "map_to", "drop", "collect"],
    )
    set_p.add_argument("--map-to", default="")
    set_p.add_argument("--collection", action="append", default=[])
    set_p.add_argument("--stop", action="store_true")
    set_p.add_argument("--disabled", action="store_true")

    rm_p = maps_sub.add_parser("remove", help="Delete a mapping by id")
    rm_p.add_argument("id")

    mv_p = maps_sub.add_parser("move", help="Reorder a mapping")
    mv_p.add_argument("id")
    mv_group = mv_p.add_mutually_exclusive_group(required=True)
    mv_group.add_argument("--up", action="store_true")
    mv_group.add_argument("--down", action="store_true")

    tog_p = maps_sub.add_parser("toggle", help="Enable or disable a mapping")
    tog_p.add_argument("id")
    tog_p.add_argument("--on", action="store_true")
    tog_p.add_argument("--off", action="store_true")

    prev_p = maps_sub.add_parser(
        "preview", help="Show AO3's usual name plus your rules for a tag"
    )
    prev_p.add_argument("tag")
    prev_p.add_argument("--no-cache", action="store_true")

    coll = sub.add_parser(
        "collections",
        help="Collection membership rules (computed on recompute)",
    )
    coll_sub = coll.add_subparsers(dest="collections_command", required=True)
    coll_sub.add_parser("list", help="List collection rules")
    coll_sub.add_parser("path", help="Print collections.yaml path")

    coll_add = coll_sub.add_parser("add", help="Add a collection rule")
    coll_add.add_argument(
        "--match",
        default="mentions",
        choices=list(COLLECTION_MATCH_KINDS),
    )
    coll_add.add_argument("--values", required=True)
    coll_add.add_argument(
        "--collection",
        action="append",
        default=[],
        help="Collection name (repeatable; default: the match text)",
    )
    coll_add.add_argument(
        "--mode",
        default="include",
        choices=["include", "exclude"],
    )
    coll_add.add_argument("--id", default="")
    coll_add.add_argument("--pin", action="store_true")
    coll_add.add_argument("--description", default="")
    coll_add.add_argument("--disabled", action="store_true")

    coll_set = coll_sub.add_parser("set", help="Update a collection rule")
    coll_set.add_argument("id")
    coll_set.add_argument("--match", default="mentions", choices=list(COLLECTION_MATCH_KINDS))
    coll_set.add_argument("--values", required=True)
    coll_set.add_argument("--collection", action="append", default=[])
    coll_set.add_argument("--mode", default="include", choices=["include", "exclude"])
    coll_set.add_argument("--pin", action="store_true")
    coll_set.add_argument("--description", default="")
    coll_set.add_argument("--disabled", action="store_true")

    coll_rm = coll_sub.add_parser("remove", help="Delete a collection rule by id")
    coll_rm.add_argument("id")

    coll_mv = coll_sub.add_parser("move", help="Reorder a collection rule")
    coll_mv.add_argument("id")
    coll_mv_g = coll_mv.add_mutually_exclusive_group(required=True)
    coll_mv_g.add_argument("--up", action="store_true")
    coll_mv_g.add_argument("--down", action="store_true")

    coll_tog = coll_sub.add_parser("toggle", help="Enable or disable a collection rule")
    coll_tog.add_argument("id")
    coll_tog.add_argument("--on", action="store_true")
    coll_tog.add_argument("--off", action="store_true")

    coll_pin = coll_sub.add_parser(
        "pin", help="Always put this AO3 work in a collection"
    )
    coll_pin.add_argument("--work-id", default="")
    coll_pin.add_argument("--uuid", default="", help="Calibre book UUID if there is no AO3 id")
    coll_pin.add_argument("--collection", required=True)
    coll_pin.add_argument("--description", default="")
    coll_pin.add_argument(
        "--exclude",
        action="store_true",
        help="Never put this work in the collection",
    )

    coll_unpin = coll_sub.add_parser(
        "unpin", help="Remove a per-work pin for a collection"
    )
    coll_unpin.add_argument("--work-id", default="")
    coll_unpin.add_argument("--uuid", default="", help="Calibre book UUID if there is no AO3 id")
    coll_unpin.add_argument("--collection", required=True)
    coll_unpin.add_argument(
        "--exclude",
        action="store_true",
        help="Remove a Never pin (default: remove Always pin)",
    )
    coll_unpin.add_argument(
        "--all-modes",
        action="store_true",
        help="Remove both Always and Never pins for this work and collection",
    )

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

    if args.command == "mappings":
        from ao3kit.tags.mappings import (
            mapping_from_form,
            move_mapping,
            preview_tag,
            remove_mapping,
            replace_mapping,
            toggle_mapping,
        )

        cfg = load_user_config(home=home, ensure=True)
        cmd = args.mappings_command
        if cmd == "path":
            _print(str(cfg.mappings_path))
            return 0
        if cmd == "list":
            rows = []
            for mapping in cfg.load_mappings():
                rows.append(mapping.to_dict() | {"id": mapping.id, "enabled": mapping.enabled})
            _print(rows)
            return 0
        if cmd == "add":
            existing = cfg.load_mappings()
            try:
                mapping = mapping_from_form(
                    match=args.match,
                    values=args.values,
                    action=args.action,
                    map_to=args.map_to,
                    collections=args.collection,
                    stop=args.stop,
                    enabled=not args.disabled,
                    mapping_id=args.id,
                    existing_ids=[item.id for item in existing],
                )
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            if any(item.id == mapping.id for item in existing):
                print(f"Mapping already exists: {mapping.id}", file=sys.stderr)
                return 1
            existing.append(mapping)
            cfg.save_mappings(existing)
            _print(mapping.to_dict() | {"id": mapping.id})
            return 0
        if cmd == "set":
            existing = cfg.load_mappings()
            try:
                mapping = mapping_from_form(
                    match=args.match,
                    values=args.values,
                    action=args.action,
                    map_to=args.map_to,
                    collections=args.collection,
                    stop=args.stop,
                    enabled=not args.disabled,
                    mapping_id=args.id,
                    existing_ids=[],
                )
                cfg.save_mappings(replace_mapping(existing, mapping))
            except (ValueError, KeyError) as exc:
                print(str(exc), file=sys.stderr)
                return 1
            _print(mapping.to_api_dict())
            return 0
        try:
            if cmd == "remove":
                cfg.save_mappings(remove_mapping(cfg.load_mappings(), args.id))
                _print({"removed": args.id})
                return 0
            if cmd == "move":
                direction = "up" if args.up else "down"
                cfg.save_mappings(
                    move_mapping(cfg.load_mappings(), args.id, direction=direction)
                )
                _print([item.id for item in cfg.load_mappings()])
                return 0
            if cmd == "toggle":
                enabled = True if args.on else False if args.off else None
                cfg.save_mappings(
                    toggle_mapping(cfg.load_mappings(), args.id, enabled=enabled)
                )
                current = next(
                    item for item in cfg.load_mappings() if item.id == args.id
                )
                _print({"id": current.id, "enabled": current.enabled})
                return 0
        except KeyError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if cmd == "preview":
            from ao3kit.tags.metadata import DEFAULT_TAG_CACHE_PATH, TagResolver
            from ao3kit.tags.rules import TagRulesEngine

            rules = cfg.load_active_rules()
            rules.resolve_canonical = cfg.settings.resolve_canonical
            rules.include_metatags = cfg.settings.include_metatags
            use_cache = (not args.no_cache) and cfg.settings.tag_cache_enabled
            with TagResolver(
                delay=cfg.settings.request_delay,
                cache_path=DEFAULT_TAG_CACHE_PATH if use_cache else None,
                follow_canonical=cfg.settings.follow_canonical,
                persist=use_cache,
                ttl_days=cfg.settings.tag_cache_ttl_days,
            ) as resolver:
                engine = TagRulesEngine(rules, resolver)
                _print(preview_tag(args.tag, engine))
            return 0

    if args.command == "collections":
        from ao3kit.tags.collections import (
            collection_rule_from_form,
            move_collection_rule,
            remove_collection_rule,
            remove_pin,
            replace_collection_rule,
            toggle_collection_rule,
            upsert_pin,
        )

        cfg = load_user_config(home=home, ensure=True)
        cmd = args.collections_command
        if cmd == "path":
            _print(str(cfg.collections_path))
            return 0
        if cmd == "list":
            _print([rule.to_api_dict() for rule in cfg.load_collection_rules()])
            return 0
        if cmd == "add":
            existing = cfg.load_collection_rules()
            try:
                rule = collection_rule_from_form(
                    match=args.match,
                    values=args.values,
                    collections=args.collection,
                    mode=args.mode,
                    enabled=not args.disabled,
                    pin=args.pin,
                    rule_id=args.id,
                    description=args.description,
                    existing_ids=[item.id for item in existing],
                )
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            if any(item.id == rule.id for item in existing):
                print(f"Collection rule already exists: {rule.id}", file=sys.stderr)
                return 1
            existing.append(rule)
            cfg.save_collection_rules(existing)
            _print(rule.to_api_dict())
            return 0
        if cmd == "set":
            existing = cfg.load_collection_rules()
            try:
                rule = collection_rule_from_form(
                    match=args.match,
                    values=args.values,
                    collections=args.collection,
                    mode=args.mode,
                    enabled=not args.disabled,
                    pin=args.pin,
                    rule_id=args.id,
                    description=args.description,
                    existing_ids=[],
                )
                cfg.save_collection_rules(replace_collection_rule(existing, rule))
            except (ValueError, KeyError) as exc:
                print(str(exc), file=sys.stderr)
                return 1
            _print(rule.to_api_dict())
            return 0
        try:
            if cmd == "remove":
                cfg.save_collection_rules(
                    remove_collection_rule(cfg.load_collection_rules(), args.id)
                )
                _print({"removed": args.id})
                return 0
            if cmd == "move":
                direction = "up" if args.up else "down"
                cfg.save_collection_rules(
                    move_collection_rule(
                        cfg.load_collection_rules(), args.id, direction=direction
                    )
                )
                _print([item.id for item in cfg.load_collection_rules()])
                return 0
            if cmd == "toggle":
                enabled = True if args.on else False if args.off else None
                cfg.save_collection_rules(
                    toggle_collection_rule(
                        cfg.load_collection_rules(), args.id, enabled=enabled
                    )
                )
                current = next(
                    item
                    for item in cfg.load_collection_rules()
                    if item.id == args.id
                )
                _print({"id": current.id, "enabled": current.enabled})
                return 0
            if cmd == "pin":
                if not (args.work_id or args.uuid):
                    print("Provide --work-id or --uuid", file=sys.stderr)
                    return 1
                rules, pin = upsert_pin(
                    cfg.load_collection_rules(),
                    collection=args.collection,
                    work_id=args.work_id,
                    calibre_uuid=args.uuid,
                    mode="exclude" if args.exclude else "include",
                    description=args.description,
                )
                cfg.save_collection_rules(rules)
                _print(
                    pin.to_api_dict()
                    if pin is not None
                    else {"ok": True, "already": True}
                )
                return 0
            if cmd == "unpin":
                if not (args.work_id or args.uuid):
                    print("Provide --work-id or --uuid", file=sys.stderr)
                    return 1
                mode = None if args.all_modes else ("exclude" if args.exclude else "include")
                rules, removed = remove_pin(
                    cfg.load_collection_rules(),
                    collection=args.collection,
                    work_id=args.work_id,
                    calibre_uuid=args.uuid,
                    mode=mode,
                )
                cfg.save_collection_rules(rules)
                _print({"removed": [rule.id for rule in removed]})
                return 0
        except KeyError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
