"""Shared types for the Colander Clash deckbuilder."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal


TEAM_PLAYER = "PlayerTeam"
TEAM_ENEMY = "EnemyTeam"

STANCE_SIEVE = "Sieve"
STANCE_DOME = "Dome"

STATUS_POUT = "status_pout"
STATUS_FUSSINESS = "status_fussiness"
STATUS_MELTDOWN = "status_meltdown"
STATUS_DROWSY = "status_drowsy"
STATUS_STUN = "status_stun"
STATUS_NO_ATTACKS = "status_no_attacks"
STATUS_RETAIN_BLOCK = "status_retain_block"
STATUS_RETAIN_AP = "status_retain_ap"
POWER_BOILING_POINT = "power_boiling_point"
POWER_BLANKET_BUNKER = "power_blanket_bunker"
POWER_TOY_FREE = "power_toy_free"

DamageType = Literal["Physical", "Sonic", "Recoil"]
Phase = Literal[
    "stance_select",
    "map",
    "combat",
    "reward",
    "upgrade",
    "relic",
    "victory",
    "defeat",
]
IntentKind = Literal["attack", "debuff", "block", "buff", "unknown"]


@dataclass
class CardDef:
    id: str
    name: str
    type: str
    tags: tuple[str, ...]
    cost: int
    target: str
    speed: str
    color: str
    text: str
    upgrade_text: str
    art_prompt: str
    resolve: Callable[..., None]
    upgrade_cost: int | None = None
    exhaust: bool = False
    ephemeral: bool = False
    unplayable: bool = False
    curse: bool = False
    status: bool = False
    token: bool = False
    rarity: str = "starter"


@dataclass
class CardInstance:
    uid: str
    def_id: str
    upgraded: bool = False
    ephemeral: bool = False
    exhaust: bool = False
    unplayable: bool = False
    cost_override: int | None = None

    def copy(self) -> CardInstance:
        return CardInstance(
            uid=self.uid,
            def_id=self.def_id,
            upgraded=self.upgraded,
            ephemeral=self.ephemeral,
            exhaust=self.exhaust,
            unplayable=self.unplayable,
            cost_override=self.cost_override,
        )


@dataclass
class RelicDef:
    id: str
    name: str
    rarity: str
    text: str


@dataclass
class Intent:
    kind: IntentKind
    value: int = 0
    times: int = 1
    status: str | None = None
    status_stacks: int = 0
    label: str = ""


@dataclass
class EnemyDef:
    id: str
    name: str
    hp: int
    intents: tuple[Intent, ...]
    flavor: str = ""


@dataclass
class Actor:
    id: str
    name: str
    team: str
    hp: int
    max_hp: int
    ap: int = 3
    max_ap: int = 3
    shield: int = 0
    statuses: dict[str, int] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    relics: list[str] = field(default_factory=list)
    draw: list[CardInstance] = field(default_factory=list)
    hand: list[CardInstance] = field(default_factory=list)
    discard: list[CardInstance] = field(default_factory=list)
    exile: list[CardInstance] = field(default_factory=list)
    intent: Intent | None = None
    intent_index: int = 0
    alive: bool = True

    def get_status(self, status_id: str) -> int:
        return int(self.statuses.get(status_id, 0))

    def set_status(self, status_id: str, stacks: int) -> None:
        if stacks <= 0:
            self.statuses.pop(status_id, None)
        else:
            self.statuses[status_id] = stacks

    def add_status(self, status_id: str, stacks: int, cap: int | None = None) -> int:
        nxt = self.get_status(status_id) + stacks
        if cap is not None:
            nxt = min(cap, nxt)
        self.set_status(status_id, nxt)
        return self.get_status(status_id)

    def remove_status(self, status_id: str) -> int:
        old = self.get_status(status_id)
        self.statuses.pop(status_id, None)
        return old


@dataclass
class DamageContext:
    source: Actor
    target: Actor
    damage: int
    damage_type: DamageType
    card: CardInstance | None
    is_preview: bool = False
    is_attack: bool = True
    unblocked: int = 0
    blocked: int = 0
    tags: tuple[str, ...] = ()
