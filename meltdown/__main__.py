"""CLI: python -m meltdown serve | demo."""

from __future__ import annotations

import argparse
import sys

from meltdown.enemies import ENCOUNTERS
from meltdown.engine import new_combat
from meltdown.serve import serve


def _autoplay(seed: int, encounter: str) -> int:
    combat = new_combat(enemy_defs=list(ENCOUNTERS[encounter]), seed=seed)
    combat.start_combat("Sieve")
    safety = 80
    while not combat.over and safety > 0:
        safety -= 1
        played = False
        for card in list(combat.hero.hand):
            if combat.over:
                break
            if combat.can_play(card, combat.frontmost_enemy()) is None:
                target = combat.frontmost_enemy()
                try:
                    combat.play(card.uid, target.id if target else None)
                    played = True
                except combat.IllegalPlay:
                    continue
        if not combat.over:
            combat.end_hero_turn()
        if not played and combat.turn > 20:
            break
    for line in combat.log_lines:
        print(line)
    print(f"winner={combat.winner} turn={combat.turn} hp={combat.hero.hp}")
    return 0 if combat.winner == "PlayerTeam" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="meltdown", description="Meltdown: The Colander Clash")
    sub = parser.add_subparsers(dest="cmd")
    serve_p = sub.add_parser("serve", help="Open the comic-book web UI")
    serve_p.add_argument("--host", default="127.0.0.1")
    serve_p.add_argument("--port", type=int, default=8766)
    demo = sub.add_parser("demo", help="Seeded autoplay combat (no UI)")
    demo.add_argument("--seed", type=int, default=7)
    demo.add_argument("--encounter", default="goon", choices=sorted(ENCOUNTERS))
    args = parser.parse_args(argv)
    if args.cmd == "demo":
        return _autoplay(args.seed, args.encounter)
    if args.cmd == "serve" or args.cmd is None:
        host = getattr(args, "host", "127.0.0.1")
        port = getattr(args, "port", 8766)
        print(f"Colander Kid at http://{host}:{port}/", file=sys.stderr)
        serve(host, port)
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
