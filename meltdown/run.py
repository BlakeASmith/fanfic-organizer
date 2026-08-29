"""A short Colander Clash run: map, rewards, relics."""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from meltdown.cards import REWARD_POOL_IDS, STARTER_DECK_IDS, get_card
from meltdown.enemies import ENCOUNTERS, RUN_PATH
from meltdown.engine import Combat, new_combat
from meltdown.models import Phase
from meltdown.relics import RELIC_MESH, REWARD_RELICS, get_relic


@dataclass
class RunState:
    seed: int
    rng: random.Random
    phase: Phase = "stance_select"
    node: int = 0
    deck: list[str] = field(default_factory=lambda: list(STARTER_DECK_IDS))
    upgraded: set[str] = field(default_factory=set)
    relics: list[str] = field(default_factory=lambda: [RELIC_MESH])
    hp: int = 35
    max_hp: int = 35
    combat: Combat | None = None
    rewards: list[str] = field(default_factory=list)
    relic_choices: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    stance: str | None = None

    def start_combat(self, encounter_id: str) -> Combat:
        defs = list(ENCOUNTERS[encounter_id])
        combat = new_combat(
            enemy_defs=defs,
            relics=list(self.relics),
            seed=self.rng.randint(1, 1_000_000),
            deck_ids=list(self.deck),
            upgraded=set(self.upgraded),
        )
        combat.hero.hp = self.hp
        combat.hero.max_hp = self.max_hp
        if self.stance:
            combat.start_combat(self.stance)
        else:
            combat.start_combat("Sieve")
        self.combat = combat
        self.phase = "combat"
        return combat

    def choose_stance(self, stance: str) -> None:
        if stance not in {"Sieve", "Dome"}:
            raise ValueError("Stance must be Sieve or Dome")
        self.stance = stance
        self.log.append(f"Plastic Strainer locked to {stance} Mode.")
        self.begin_node()

    def begin_node(self) -> None:
        if self.node >= len(RUN_PATH):
            self.phase = "victory"
            return
        encounter = RUN_PATH[self.node]
        self.log.append(f"Encounter: {encounter}")
        self.start_combat(encounter)

    def after_combat(self) -> None:
        assert self.combat is not None
        if self.combat.winner != "PlayerTeam":
            self.hp = 0
            self.phase = "defeat"
            return
        self.hp = self.combat.hero.hp
        encounter = RUN_PATH[self.node]
        self.node += 1
        if encounter == "boss":
            self.phase = "victory"
            self.log.append("The Bedtime Committee is adjourned.")
            return
        if encounter == "elite":
            owned = set(self.relics)
            choices = [r for r in REWARD_RELICS if r not in owned]
            self.rng.shuffle(choices)
            self.relic_choices = choices[:2] or list(choices)
            self.phase = "relic" if self.relic_choices else "reward"
        else:
            self.phase = "reward"
        pool = [c for c in REWARD_POOL_IDS if c not in self.deck]
        if len(pool) < 3:
            pool = list(REWARD_POOL_IDS)
        self.rng.shuffle(pool)
        self.rewards = pool[:3]
        if encounter in {"goon_pair", "streets"}:
            # Offer an upgrade instead of (or after) a pick on longer fights.
            pass

    def take_reward(self, card_id: str | None) -> None:
        if card_id:
            if card_id not in self.rewards:
                raise ValueError("Not a offered reward")
            self.deck.append(card_id)
            self.log.append(f"Added {get_card(card_id).name} to the deck.")
        self.rewards = []
        if self.node >= len(RUN_PATH):
            self.phase = "victory"
        elif RUN_PATH[self.node - 1] in {"goon_pair", "streets"} and any(
            card_id not in self.upgraded for card_id in self.deck
        ):
            self.phase = "upgrade"
        else:
            self.begin_node()

    def take_relic(self, relic_id: str | None) -> None:
        if relic_id:
            if relic_id not in self.relic_choices:
                raise ValueError("Not an offered relic")
            self.relics.append(relic_id)
            self.log.append(f"Relic: {get_relic(relic_id).name}")
        self.relic_choices = []
        self.phase = "reward"

    def upgrade_card(self, card_id: str | None) -> None:
        if card_id:
            if card_id not in self.deck:
                raise ValueError("Card is not in the deck")
            self.upgraded.add(card_id)
            self.log.append(f"Upgraded {get_card(card_id).name}.")
        self.phase = "map"
        self.begin_node()

    def public_state(self) -> dict:
        combat = self.combat.public_state() if self.combat and self.phase == "combat" else None
        return {
            "phase": self.phase,
            "node": self.node,
            "path": list(RUN_PATH),
            "hp": self.hp,
            "max_hp": self.max_hp,
            "stance": self.stance,
            "deck": [
                {
                    "id": card_id,
                    "name": get_card(card_id).name,
                    "upgraded": card_id in self.upgraded,
                    "text": get_card(card_id).upgrade_text
                    if card_id in self.upgraded
                    else get_card(card_id).text,
                    "color": get_card(card_id).color,
                    "type": get_card(card_id).type,
                    "cost": get_card(card_id).cost,
                }
                for card_id in self.deck
            ],
            "relics": [
                {"id": rid, "name": get_relic(rid).name, "text": get_relic(rid).text, "rarity": get_relic(rid).rarity}
                for rid in self.relics
            ],
            "rewards": [
                {
                    "id": card_id,
                    "name": get_card(card_id).name,
                    "text": get_card(card_id).text,
                    "color": get_card(card_id).color,
                    "type": get_card(card_id).type,
                    "cost": get_card(card_id).cost,
                    "upgrade": get_card(card_id).upgrade_text,
                }
                for card_id in self.rewards
            ],
            "relic_choices": [
                {"id": rid, "name": get_relic(rid).name, "text": get_relic(rid).text, "rarity": get_relic(rid).rarity}
                for rid in self.relic_choices
            ],
            "upgrades": [
                {
                    "id": card_id,
                    "name": get_card(card_id).name,
                    "text": get_card(card_id).text,
                    "upgrade": get_card(card_id).upgrade_text,
                    "already": card_id in self.upgraded,
                }
                for card_id in self.deck
                if card_id not in self.upgraded and get_card(card_id).upgrade_text
            ],
            "run_log": self.log[-20:],
            "combat": combat,
        }


def new_run(seed: int = 1) -> RunState:
    return RunState(seed=seed, rng=random.Random(seed))
