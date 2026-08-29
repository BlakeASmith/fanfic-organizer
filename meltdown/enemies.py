"""Enemy and encounter catalog."""

from __future__ import annotations

from meltdown.models import EnemyDef, Intent

SUIT_GOON = EnemyDef(
    id="enemy_suit_goon",
    name="Suit Goon",
    hp=22,
    flavor="A briefcase, a scowl, and no naptime policy.",
    intents=(
        Intent(kind="attack", value=7, label="Briefcase Swing"),
        Intent(kind="debuff", value=0, status="status_fussiness", status_stacks=2, label="Office Policy"),
        Intent(kind="attack", value=9, label="Overtime Slam"),
    ),
)

BANK_ROBBER = EnemyDef(
    id="enemy_bank_robber",
    name="Bank Robber",
    hp=30,
    flavor="He picked the wrong toddler to rob.",
    intents=(
        Intent(kind="block", value=8, label="Duffel Guard"),
        Intent(kind="attack", value=12, label="Getaway Shove"),
        Intent(kind="attack", value=6, times=2, label="Double Cross"),
    ),
)

FLOOR_MANAGER = EnemyDef(
    id="enemy_floor_manager",
    name="Floor Manager",
    hp=42,
    flavor="Wants the aisle cleared of superheroes.",
    intents=(
        Intent(kind="debuff", value=0, status="status_clutter_drop", status_stacks=1, label="Stock the Aisle"),
        Intent(kind="attack", value=14, label="Clipboard Chop"),
        Intent(kind="block", value=10, label="Closed for Inventory"),
        Intent(kind="attack", value=8, times=2, label="Two-for-One"),
    ),
)

BEDTIME = EnemyDef(
    id="enemy_bedtime_committee",
    name="The Bedtime Committee",
    hp=64,
    flavor="Lights out. No WAAAH-Blasts after 7.",
    intents=(
        Intent(kind="buff", value=6, label="Dim the Lights"),
        Intent(kind="attack", value=16, label="Lights Out"),
        Intent(kind="debuff", value=0, status="status_drowsy", status_stacks=1, label="Storytime Mandate"),
        Intent(kind="attack", value=10, times=2, label="Blanket Barrage"),
    ),
)

ENCOUNTERS: dict[str, tuple[EnemyDef, ...]] = {
    "goon": (SUIT_GOON,),
    "goon_pair": (SUIT_GOON, SUIT_GOON),
    "robber": (BANK_ROBBER,),
    "elite": (FLOOR_MANAGER,),
    "streets": (SUIT_GOON, BANK_ROBBER),
    "boss": (BEDTIME,),
}

RUN_PATH = ("goon", "goon_pair", "elite", "streets", "boss")
