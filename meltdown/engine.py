"""Modular deckbuilder combat engine for Colander Kid."""

from __future__ import annotations

import itertools
import random
from collections.abc import Callable
from dataclasses import dataclass, field

from meltdown.cards import (
    STARTER_DECK_IDS,
    card_cost,
    get_card,
)
from meltdown.models import (
    POWER_BLANKET_BUNKER,
    POWER_BOILING_POINT,
    STATUS_DROWSY,
    STATUS_FUSSINESS,
    STATUS_MELTDOWN,
    STATUS_NO_ATTACKS,
    STATUS_POUT,
    STATUS_RETAIN_AP,
    STATUS_RETAIN_BLOCK,
    STATUS_STUN,
    STANCE_DOME,
    STANCE_SIEVE,
    TEAM_ENEMY,
    TEAM_PLAYER,
    Actor,
    CardInstance,
    DamageContext,
    DamageType,
    EnemyDef,
    Intent,
)
from meltdown.relics import RELIC_BINKY, RELIC_ELMO, RELIC_MESH, RELIC_SIPPY


class IllegalPlay(Exception):
    """Card cannot be played in the current state."""


PipelineFn = Callable[..., None]


@dataclass
class Combat:
    hero: Actor
    enemies: list[Actor]
    rng: random.Random
    log_lines: list[str] = field(default_factory=list)
    turn: int = 0
    over: bool = False
    winner: str | None = None
    _uids: itertools.count = field(default_factory=lambda: itertools.count(1))
    _pipelines: dict[str, list[PipelineFn]] = field(default_factory=dict)
    _drew_this_resolve: int = 0

    IllegalPlay = IllegalPlay

    def register(self, hook: str, fn: PipelineFn) -> None:
        self._pipelines.setdefault(hook, []).append(fn)

    def emit(self, hook: str, *args, **kwargs) -> None:
        for fn in self._pipelines.get(hook, ()):
            fn(*args, **kwargs)

    def log(self, message: str) -> None:
        self.log_lines.append(message)

    def tantrum(self, actor: Actor) -> int:
        return int(actor.state.get("tantrum_meter", 0))

    def living_enemies(self) -> list[Actor]:
        return [e for e in self.enemies if e.alive]

    def frontmost_enemy(self) -> Actor | None:
        living = self.living_enemies()
        return living[0] if living else None

    def random_living_enemy(self) -> Actor | None:
        living = self.living_enemies()
        if not living:
            return None
        return self.rng.choice(living)

    def actor_by_id(self, actor_id: str) -> Actor:
        if self.hero.id == actor_id:
            return self.hero
        for enemy in self.enemies:
            if enemy.id == actor_id:
                return enemy
        raise KeyError(actor_id)

    def make_card(self, def_id: str, *, upgraded: bool = False) -> CardInstance:
        definition = get_card(def_id)
        return CardInstance(
            uid=f"c{next(self._uids)}",
            def_id=def_id,
            upgraded=upgraded,
            ephemeral=definition.ephemeral,
            exhaust=definition.exhaust or definition.ephemeral,
            unplayable=definition.unplayable,
        )

    def add_status(self, actor: Actor, status_id: str, stacks: int, cap: int | None = None) -> int:
        if status_id == STATUS_POUT:
            cap = 20 if cap is None else cap
        if status_id == "status_clutter_drop":
            self.push_clutter(actor, "token_sharp_toy")
            return actor.get_status("status_clutter")
        nxt = actor.add_status(status_id, stacks, cap=cap)
        pretty = status_id.removeprefix("status_").removeprefix("power_").replace("_", " ")
        self.log(f"{actor.name} gains {stacks} {pretty} ({nxt}).")
        return nxt

    def gain_block(self, actor: Actor, amount: int) -> None:
        actor.shield += max(0, amount)
        self.log(f"{actor.name} gains {amount} Block ({actor.shield}).")

    def gain_ap(self, actor: Actor, amount: int) -> None:
        actor.ap += amount
        self.log(f"{actor.name} gains {amount} AP ({actor.ap}).")

    def set_stance(self, actor: Actor, stance: str, *, grant_swap_block: bool = True) -> None:
        prev = actor.state.get("stance")
        actor.state["stance"] = stance
        self.log(f"{actor.name} enters {stance} Mode.")
        if grant_swap_block and prev and prev != stance and RELIC_MESH in actor.relics:
            self.gain_block(actor, 4)

    def swap_stance(self, actor: Actor) -> None:
        current = actor.state.get("stance") or STANCE_SIEVE
        self.set_stance(actor, STANCE_DOME if current == STANCE_SIEVE else STANCE_SIEVE)

    def add_tantrum(self, actor: Actor, amount: int = 1) -> int:
        current = self.tantrum(actor)
        nxt = min(5, current + amount)
        actor.state["tantrum_meter"] = nxt
        self.log(f"Tantrum {current} → {nxt}.")
        if nxt >= 5 and actor.get_status(STATUS_MELTDOWN) == 0:
            self.enter_meltdown(actor, forced_blast=True)
        return nxt

    def enter_meltdown(self, actor: Actor, *, forced_blast: bool) -> None:
        if actor.get_status(STATUS_MELTDOWN):
            actor.state["tantrum_meter"] = 5
            return
        actor.state["tantrum_meter"] = 5
        actor.add_status(STATUS_MELTDOWN, 1)
        actor.state["meltdown_this_turn"] = True
        self.log("MELTDOWN! Outbursts are free and hit twice as hard!")
        self.emit("OnHeroMeltdownTriggered", actor)
        if forced_blast:
            self.log("Forced WAAAH-Blast!")
            for enemy in self.living_enemies():
                combat_card = self.make_card("ck_waaah_blast")
                self.deal_damage(actor, enemy, 10, "Sonic", combat_card, extra_tags=("Outburst",))

    def push_clutter(self, actor: Actor, def_id: str) -> CardInstance | None:
        if len(actor.hand) >= 10:
            card = self.make_card(def_id)
            actor.discard.append(card)
            self.log(f"{actor.name}'s hand is full. {get_card(def_id).name} discarded.")
            return card
        card = self.make_card(def_id)
        actor.hand.append(card)
        actor.add_status("status_clutter", 1)
        self.log(f"{get_card(def_id).name} clutters {actor.name}'s hand.")
        return card

    def shuffle_into_draw(self, actor: Actor, card: CardInstance) -> None:
        actor.draw.append(card)
        self.rng.shuffle(actor.draw)
        self.log(f"{get_card(card.def_id).name} shuffled into {actor.name}'s draw pile.")

    def _recycle_draw(self, actor: Actor) -> bool:
        if actor.draw:
            return True
        if not actor.discard:
            return False
        actor.draw = list(actor.discard)
        actor.discard.clear()
        self.rng.shuffle(actor.draw)
        self.log(f"{actor.name} shuffles discard into draw.")
        return bool(actor.draw)

    def draw_cards(self, actor: Actor, n: int) -> list[CardInstance]:
        drawn: list[CardInstance] = []
        for _ in range(n):
            card = self._draw_one(actor)
            if card is None:
                break
            drawn.append(card)
        return drawn

    def _draw_one(self, actor: Actor) -> CardInstance | None:
        if len(actor.hand) >= 10:
            self.log(f"{actor.name}'s hand is full.")
            return None
        if not self._recycle_draw(actor):
            return None
        card = actor.draw.pop()
        definition = get_card(card.def_id)
        if (
            actor.id == self.hero.id
            and actor.state.get("stance") == STANCE_SIEVE
            and not actor.state.get("sieve_used")
            and (definition.status or definition.curse)
        ):
            actor.exile.append(card)
            actor.state["sieve_used"] = True
            self.log(f"Sieve Mode exiles {definition.name} and redraws.")
            return self._draw_one(actor)
        actor.hand.append(card)
        self.log(f"{actor.name} draws {definition.name}.")
        self.emit("OnCardDrawn", actor, card)
        if actor.get_status(POWER_BLANKET_BUNKER) and actor.state.get("stance") == STANCE_DOME:
            self.gain_block(actor, actor.get_status(POWER_BLANKET_BUNKER))
        return card

    def deal_damage_aoe(
        self,
        source: Actor,
        amount: int,
        damage_type: DamageType,
        card: CardInstance | None,
        extra_tags: tuple[str, ...] = (),
    ) -> None:
        for enemy in list(self.living_enemies()):
            self.deal_damage(source, enemy, amount, damage_type, card, extra_tags=extra_tags)

    def deal_damage(
        self,
        source: Actor,
        target: Actor,
        amount: int,
        damage_type: DamageType,
        card: CardInstance | None = None,
        *,
        is_attack: bool | None = None,
        consume_pout: bool = True,
        extra_tags: tuple[str, ...] = (),
        is_preview: bool = False,
    ) -> DamageContext:
        tags = extra_tags
        if card is not None:
            tags = tuple(dict.fromkeys((*get_card(card.def_id).tags, *extra_tags)))
        if is_attack is None:
            is_attack = damage_type != "Recoil" and "Skill" not in tags
        ctx = DamageContext(
            source=source,
            target=target,
            damage=max(0, amount),
            damage_type=damage_type,
            card=card,
            is_preview=is_preview,
            is_attack=bool(is_attack),
            tags=tags,
        )
        if consume_pout:
            self._apply_pout(ctx)
        if source.get_status(STATUS_MELTDOWN) and "Outburst" in ctx.tags:
            ctx.damage *= 2
        fuss = source.get_status(STATUS_FUSSINESS)
        if fuss and ctx.is_attack:
            ctx.damage = max(0, ctx.damage - fuss)
        self.emit("OnEvaluateDamage", ctx)
        incoming = ctx.damage
        blocked = min(target.shield, incoming)
        unblocked = incoming - blocked
        if not is_preview:
            target.shield -= blocked
            if unblocked:
                target.hp = max(0, target.hp - unblocked)
            ctx.blocked = blocked
            ctx.unblocked = unblocked
            verb = "bonks" if damage_type == "Physical" else "blasts"
            self.log(
                f"{source.name} {verb} {target.name} for {incoming} {damage_type} "
                f"({blocked} blocked, {unblocked} through)."
            )
            if target.hp <= 0:
                target.alive = False
                target.hp = 0
                self.log(f"{target.name} is down!")
            self.emit("OnDamageApplied", ctx)
            if unblocked > 0 and target.id == self.hero.id:
                self._hero_took_hit(ctx)
            if ctx.is_attack and unblocked > 0:
                self._explode_fussiness(target)
            self._check_end()
        return ctx

    def _apply_pout(self, ctx: DamageContext) -> None:
        if ctx.source.id != self.hero.id or ctx.card is None:
            return
        if "Outburst" not in ctx.tags:
            return
        # Tantrum Throw consumes Pout itself.
        if ctx.card.def_id == "ck_tantrum_throw":
            return
        stacks = ctx.source.get_status(STATUS_POUT)
        if stacks <= 0:
            return
        ctx.damage += stacks * 2
        if not ctx.is_preview:
            ctx.source.remove_status(STATUS_POUT)
            self.log(f"{ctx.source.name} spends {stacks} Pout (+{stacks * 2} damage).")

    def _hero_took_hit(self, ctx: DamageContext) -> None:
        hero = self.hero
        if ctx.unblocked <= 0:
            return
        self.add_tantrum(hero, 1)
        if hero.get_status(POWER_BOILING_POINT):
            self.add_status(hero, STATUS_POUT, 1, cap=20)
            self.add_tantrum(hero, 1)
        if RELIC_ELMO in hero.relics and ctx.unblocked >= 8:
            self.add_status(hero, STATUS_POUT, 2, cap=20)
            crawl = self.make_card("ck_crawl_starter")
            crawl.cost_override = 0
            crawl.ephemeral = True
            crawl.exhaust = True
            if len(hero.hand) < 10:
                hero.hand.append(crawl)
            else:
                hero.discard.append(crawl)
            self.log("Elmo Sleepsuit stitches a free Crawl Away!")

    def _explode_fussiness(self, target: Actor) -> None:
        stacks = target.get_status(STATUS_FUSSINESS)
        if stacks <= 0:
            return
        sonic = stacks // 2
        if sonic <= 0:
            return
        allies = [a for a in self._team_of(target) if a.id != target.id and a.alive]
        for ally in allies:
            self.deal_damage(
                target,
                ally,
                sonic,
                "Sonic",
                None,
                is_attack=False,
                consume_pout=False,
            )
        self.log(f"{target.name}'s Fussiness pops for {sonic} Sonic to allies.")

    def _team_of(self, actor: Actor) -> list[Actor]:
        if actor.team == TEAM_PLAYER:
            return [self.hero]
        return self.enemies

    def card_in_hand(self, uid: str) -> CardInstance:
        for card in self.hero.hand:
            if card.uid == uid:
                return card
        raise IllegalPlay(f"Card {uid} is not in hand")

    def can_play(self, card: CardInstance, target: Actor | None = None) -> str | None:
        definition = get_card(card.def_id)
        cost = card_cost(card, self.hero)
        if self.hero.ap < cost:
            return "Not enough AP"
        if definition.id == "ck_epic_meltdown" and self.tantrum(self.hero) < 4:
            return "Needs 4+ Tantrum"
        if definition.target in {"enemy", "random"} and definition.id != "token_wooden_block":
            if definition.target == "enemy" and (target is None or not target.alive):
                return "Pick an enemy"
        return None

    def play(self, uid: str, target_id: str | None = None) -> None:
        if self.over:
            raise IllegalPlay("Combat is over")
        card = self.card_in_hand(uid)
        definition = get_card(card.def_id)
        target = self._resolve_target(definition.target, target_id)
        reason = self.can_play(card, target)
        if reason:
            raise IllegalPlay(reason)
        cost = card_cost(card, self.hero)
        self.hero.ap -= cost
        self.hero.hand.remove(card)
        self.log(f"{self.hero.name} plays {definition.name} ({cost} AP).")
        try:
            definition.resolve(self, self.hero, card, target)
        except IllegalPlay:
            self.hero.hand.append(card)
            self.hero.ap += cost
            raise
        if card.exhaust or definition.exhaust or card.ephemeral:
            self.hero.exile.append(card)
        else:
            self.hero.discard.append(card)
        if get_card(card.def_id).token or "Clutter" in definition.tags:
            clutter = self.hero.get_status("status_clutter")
            if clutter:
                self.hero.set_status("status_clutter", clutter - 1)
        self.emit("OnCardPlayed", self.hero, card)
        self._check_end()

    def _resolve_target(self, kind: str, target_id: str | None) -> Actor | None:
        if kind == "self":
            return self.hero
        if kind == "all_enemies" or kind == "all":
            return None
        if kind == "random":
            return self.random_living_enemy()
        if target_id:
            return self.actor_by_id(target_id)
        return self.frontmost_enemy()

    def start_combat(self, stance: str | None = None) -> None:
        if stance:
            self.set_stance(self.hero, stance, grant_swap_block=False)
        elif not self.hero.state.get("stance"):
            self.set_stance(self.hero, STANCE_SIEVE, grant_swap_block=False)
        self.hero.state.setdefault("tantrum_meter", 0)
        self.log("Combat start!")
        self.emit("OnCombatStart", self.hero)
        self.start_hero_turn()

    def start_hero_turn(self) -> None:
        if self.over:
            return
        self.turn += 1
        hero = self.hero
        hero.state["sieve_used"] = False
        drowsy = hero.remove_status(STATUS_DROWSY)
        retain_ap = hero.remove_status(STATUS_RETAIN_AP)
        max_ap = hero.max_ap
        if RELIC_SIPPY in hero.relics:
            max_ap += self.turn
            hero.state["sippy_max"] = max_ap
        hero.ap = 1 if drowsy else max_ap
        if retain_ap:
            hero.ap += retain_ap
        if drowsy:
            self.log("Drowsy: AP dropped to 1. Someone needs a nap.")
        retain = hero.remove_status(STATUS_RETAIN_BLOCK)
        if hero.state.get("stance") != STANCE_DOME:
            hero.shield = retain
        elif retain:
            hero.shield = max(hero.shield, retain)
        self.emit("OnTurnStart", hero)
        self.draw_cards(hero, 5)
        if RELIC_BINKY in hero.relics:
            self._golden_binky(hero)
        for enemy in self.living_enemies():
            self._roll_intent(enemy)

    def _golden_binky(self, hero: Actor) -> None:
        junk = [
            c
            for c in hero.hand
            if get_card(c.def_id).curse or "Clutter" in get_card(c.def_id).tags
        ]
        if not junk:
            return
        card = self.rng.choice(junk)
        hero.hand.remove(card)
        hero.exile.append(card)
        hero.ap += 1
        self.log(f"Golden Pacifier exhausts {get_card(card.def_id).name}. +1 AP.")

    def end_hero_turn(self) -> None:
        if self.over:
            return
        hero = self.hero
        for card in list(hero.hand):
            definition = get_card(card.def_id)
            if definition.id == "ck_sugar_crash":
                self.deal_damage(hero, hero, 3, "Recoil", card, is_attack=False, consume_pout=False)
                hero.hand.remove(card)
                hero.exile.append(card)
                self.log("Sugar Crash hits for 3.")
            elif card.ephemeral or definition.ephemeral:
                hero.hand.remove(card)
                hero.exile.append(card)
                self.log(f"{definition.name} fades.")
        leftover = hero.shield
        if hero.state.get("stance") == STANCE_DOME and leftover > 0:
            bonk = leftover // 2
            front = self.frontmost_enemy()
            if front is not None and bonk > 0:
                self.deal_damage(hero, front, bonk, "Physical", None, extra_tags=("Bonk",), consume_pout=False)
            self.log(f"Dome Mode keeps {hero.shield} Block.")
        if hero.get_status(STATUS_MELTDOWN):
            hero.remove_status(STATUS_MELTDOWN)
            hero.state["tantrum_meter"] = 0
            hero.state["meltdown_this_turn"] = False
            if RELIC_SIPPY not in hero.relics:
                hero.add_status(STATUS_DROWSY, 1)
                self.log("Meltdown fades. Drowsy next turn.")
            else:
                self.log("Bottomless Sippy Cup skips Drowsy.")
        self.emit("OnTurnEnd", hero)
        self._discard_hand(hero)
        if not self.over:
            self._enemy_turn()
        if not self.over:
            self.start_hero_turn()

    def _discard_hand(self, actor: Actor) -> None:
        for card in list(actor.hand):
            actor.hand.remove(card)
            if card.ephemeral or get_card(card.def_id).ephemeral:
                actor.exile.append(card)
            else:
                actor.discard.append(card)

    def _roll_intent(self, enemy: Actor) -> None:
        if enemy.get_status(STATUS_STUN):
            enemy.intent = Intent(kind="unknown", label="Stunned")
            return
        pattern: tuple[Intent, ...] = enemy.state["intent_pattern"]
        intent = pattern[enemy.intent_index % len(pattern)]
        enemy.intent_index += 1
        enemy.intent = Intent(
            kind=intent.kind,
            value=intent.value,
            times=intent.times,
            status=intent.status,
            status_stacks=intent.status_stacks,
            label=intent.label,
        )

    def _enemy_turn(self) -> None:
        for enemy in list(self.living_enemies()):
            if self.over:
                return
            if enemy.get_status(STATUS_STUN):
                enemy.remove_status(STATUS_STUN)
                self.log(f"{enemy.name} is stunned!")
                enemy.intent = None
                continue
            clutter = [c for c in enemy.hand if "Clutter" in get_card(c.def_id).tags]
            if clutter:
                card = clutter[0]
                enemy.hand.remove(card)
                enemy.exile.append(card)
                self.log(f"{enemy.name} wastes the turn clearing {get_card(card.def_id).name}.")
                enemy.intent = None
                continue
            intent = enemy.intent
            if intent is None:
                continue
            if intent.kind == "attack":
                if enemy.get_status(STATUS_NO_ATTACKS):
                    self.log(f"{enemy.name} cannot attack (Binky Peace).")
                else:
                    for _ in range(intent.times):
                        if not self.hero.alive:
                            break
                        self.deal_damage(enemy, self.hero, intent.value, "Physical", None)
            elif intent.kind == "block":
                self.gain_block(enemy, intent.value)
            elif intent.kind == "debuff" and intent.status:
                self.add_status(self.hero, intent.status, intent.status_stacks or 1)
            elif intent.kind == "buff":
                self.gain_block(enemy, intent.value)
            enemy.remove_status(STATUS_NO_ATTACKS)
            enemy.intent = None
        if self.hero.state.get("stance") != STANCE_DOME:
            pass

    def _check_end(self) -> None:
        if self.over:
            return
        if not self.hero.alive or self.hero.hp <= 0:
            self.hero.alive = False
            self.over = True
            self.winner = TEAM_ENEMY
            self.log("Colander Kid needs a real nap. Defeat.")
        elif not self.living_enemies():
            self.over = True
            self.winner = TEAM_PLAYER
            self.log("The villains fold. Victory!")

    def preview_damage(self, uid: str, target_id: str | None) -> int:
        card = self.card_in_hand(uid)
        definition = get_card(card.def_id)
        target = self._resolve_target(definition.target, target_id)
        if target is None:
            return 0
        # Conservative static preview for common attacks.
        amount = 0
        if definition.id.startswith("ck_bonk"):
            amount = 9 if card.upgraded else 6
        elif definition.id == "ck_waaah_blast":
            amount = 14 if card.upgraded else 10
        elif definition.id == "ck_tantrum_throw":
            stacks = self.hero.get_status(STATUS_POUT) + (3 if card.upgraded else 2)
            amount = (7 if card.upgraded else 5) * stacks
        elif definition.id == "ck_dome_slam":
            amount = self.hero.shield + (6 if card.upgraded else 0)
        ctx = DamageContext(
            source=self.hero,
            target=target,
            damage=amount,
            damage_type="Physical",
            card=card,
            is_preview=True,
            tags=definition.tags,
        )
        self._apply_pout(ctx)
        if self.hero.get_status(STATUS_MELTDOWN) and "Outburst" in definition.tags:
            ctx.damage *= 2
        return ctx.damage

    def public_state(self) -> dict:
        return {
            "turn": self.turn,
            "over": self.over,
            "winner": self.winner,
            "log": self.log_lines[-40:],
            "hero": self._public_actor(self.hero, hide_piles=False),
            "enemies": [self._public_actor(e, hide_piles=True) for e in self.enemies],
            "hand": [self._public_card(c, self.hero) for c in self.hero.hand],
        }

    def _public_card(self, card: CardInstance, actor: Actor) -> dict:
        definition = get_card(card.def_id)
        return {
            "uid": card.uid,
            "id": card.def_id,
            "name": definition.name + ("+" if card.upgraded else ""),
            "type": definition.type,
            "tags": list(definition.tags),
            "cost": card_cost(card, actor),
            "target": definition.target,
            "speed": definition.speed,
            "color": definition.color,
            "text": definition.upgrade_text if card.upgraded else definition.text,
            "upgraded": card.upgraded,
            "ephemeral": card.ephemeral,
            "clutter": "Clutter" in definition.tags or definition.curse,
            "playable": self.can_play(card, self.frontmost_enemy()) is None
            if definition.target != "enemy"
            else self.hero.ap >= card_cost(card, actor)
            and not (definition.id == "ck_epic_meltdown" and self.tantrum(self.hero) < 4),
        }

    def _public_actor(self, actor: Actor, *, hide_piles: bool) -> dict:
        data = {
            "id": actor.id,
            "name": actor.name,
            "team": actor.team,
            "hp": actor.hp,
            "max_hp": actor.max_hp,
            "ap": actor.ap,
            "max_ap": actor.state.get("sippy_max", actor.max_ap),
            "shield": actor.shield,
            "statuses": dict(actor.statuses),
            "tantrum": self.tantrum(actor) if actor.id == self.hero.id else 0,
            "stance": actor.state.get("stance"),
            "alive": actor.alive,
            "intent": None
            if actor.intent is None
            else {
                "kind": actor.intent.kind,
                "value": actor.intent.value,
                "times": actor.intent.times,
                "label": actor.intent.label,
            },
            "relics": list(actor.relics),
        }
        if not hide_piles:
            data["piles"] = {
                "draw": len(actor.draw),
                "discard": len(actor.discard),
                "exile": len(actor.exile),
                "hand": len(actor.hand),
            }
        return data


def make_hero(*, relics: list[str] | None = None, hp: int = 35) -> Actor:
    return Actor(
        id="hero_colander_kid",
        name="Colander Kid",
        team=TEAM_PLAYER,
        hp=hp,
        max_hp=hp,
        ap=3,
        max_ap=3,
        shield=0,
        relics=list(relics or [RELIC_MESH]),
        state={"tantrum_meter": 0, "stance": None},
    )


def make_enemy(definition: EnemyDef, *, index: int = 0) -> Actor:
    actor = Actor(
        id=f"{definition.id}_{index}",
        name=definition.name if index == 0 else f"{definition.name} {index + 1}",
        team=TEAM_ENEMY,
        hp=definition.hp,
        max_hp=definition.hp,
        ap=0,
        max_ap=0,
        state={"intent_pattern": definition.intents, "flavor": definition.flavor},
    )
    return actor


def build_starter_deck(combat: Combat) -> None:
    cards = [combat.make_card(card_id) for card_id in STARTER_DECK_IDS]
    combat.rng.shuffle(cards)
    combat.hero.draw = cards


def new_combat(
    *,
    enemy_defs: list[EnemyDef],
    relics: list[str] | None = None,
    seed: int = 1,
    deck_ids: list[str] | None = None,
    upgraded: set[str] | None = None,
) -> Combat:
    rng = random.Random(seed)
    hero = make_hero(relics=relics)
    enemies = [make_enemy(defn, index=i) for i, defn in enumerate(enemy_defs)]
    combat = Combat(hero=hero, enemies=enemies, rng=rng)
    if deck_ids is None:
        build_starter_deck(combat)
    else:
        cards = [
            combat.make_card(card_id, upgraded=card_id in (upgraded or set()))
            for card_id in deck_ids
        ]
        rng.shuffle(cards)
        hero.draw = cards
    return combat
