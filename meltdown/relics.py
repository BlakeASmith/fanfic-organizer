"""Relic catalog for Colander Kid."""

from __future__ import annotations

from meltdown.models import RelicDef

RELIC_MESH = "relic_mesh_helmet_mk1"
RELIC_ELMO = "relic_elmo_onesie"
RELIC_BINKY = "relic_golden_binky"
RELIC_SIPPY = "relic_sippy_holy_grail"

CATALOG: dict[str, RelicDef] = {
    RELIC_MESH: RelicDef(
        id=RELIC_MESH,
        name="Plastic Strainer of Destiny",
        rarity="Starter",
        text="At the start of combat, choose Sieve or Dome. Switching stances grants 4 Block.",
    ),
    RELIC_ELMO: RelicDef(
        id=RELIC_ELMO,
        name="Elmo Sleepsuit of Resilience",
        rarity="Common",
        text="When you take 8+ unblocked damage in one hit, gain 2 Pout and a free Crawl Away.",
    ),
    RELIC_BINKY: RelicDef(
        id=RELIC_BINKY,
        name="The Golden Pacifier",
        rarity="Rare",
        text="At the start of your turn, exhaust a random Clutter or Curse in hand and gain 1 AP.",
    ),
    RELIC_SIPPY: RelicDef(
        id=RELIC_SIPPY,
        name="Bottomless Sippy Cup",
        rarity="Boss",
        text="Gain +1 Max AP every turn. Meltdown no longer applies Drowsy.",
    ),
}

REWARD_RELICS = (RELIC_ELMO, RELIC_BINKY, RELIC_SIPPY)


def get_relic(relic_id: str) -> RelicDef:
    return CATALOG[relic_id]
