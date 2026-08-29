"""Card catalog for Colander Kid."""

from __future__ import annotations

from typing import TYPE_CHECKING

from meltdown.models import (
    STATUS_MELTDOWN,
    STATUS_NO_ATTACKS,
    STATUS_POUT,
    STATUS_RETAIN_AP,
    STATUS_RETAIN_BLOCK,
    STATUS_STUN,
    STANCE_SIEVE,
    CardDef,
    CardInstance,
)

if TYPE_CHECKING:
    from meltdown.engine import Combat


def _up(card: CardInstance) -> bool:
    return bool(card.upgraded)


def play_bonk(combat: Combat, source, card: CardInstance, target) -> None:
    combat.deal_damage(source, target, 9 if _up(card) else 6, "Physical", card)
    combat.add_status(target, "status_fussiness", 2 if _up(card) else 1)


def play_crawl(combat: Combat, source, card: CardInstance, target) -> None:
    combat.gain_block(source, 8 if _up(card) else 5)
    if _up(card) or source.state.get("stance") == STANCE_SIEVE:
        combat.draw_cards(source, 1)


def play_pucker(combat: Combat, source, card: CardInstance, target) -> None:
    combat.add_status(source, STATUS_POUT, 3 if _up(card) else 2, cap=20)
    combat.add_tantrum(source, 1)
    if _up(card):
        combat.draw_cards(source, 1)


def play_waaah(combat: Combat, source, card: CardInstance, target) -> None:
    dmg = 14 if _up(card) else 10
    stun = _up(card) or combat.tantrum(source) >= 3
    for enemy in combat.living_enemies():
        combat.deal_damage(source, enemy, dmg, "Sonic", card)
        if stun:
            combat.add_status(enemy, STATUS_STUN, 1)


def play_tantrum_throw(combat: Combat, source, card: CardInstance, target) -> None:
    combat.add_status(source, STATUS_POUT, 3 if _up(card) else 2, cap=20)
    stacks = source.remove_status(STATUS_POUT)
    per = 7 if _up(card) else 5
    if stacks:
        combat.deal_damage(
            source,
            target,
            per * stacks,
            "Physical",
            card,
            extra_tags=("Outburst",),
            consume_pout=False,
        )


def play_floor_thrash(combat: Combat, source, card: CardInstance, target) -> None:
    combat.add_tantrum(source, 2)
    dmg = 12 if _up(card) else 8
    for enemy in combat.living_enemies():
        combat.deal_damage(source, enemy, dmg, "Physical", card, extra_tags=("Outburst",))
    if not _up(card):
        combat.deal_damage(source, source, 2, "Recoil", card, is_attack=False, consume_pout=False)


def play_flailing(combat: Combat, source, card: CardInstance, target) -> None:
    hits = 4 if _up(card) else 3
    each = 4 if _up(card) else 3
    for _ in range(hits):
        enemy = combat.random_living_enemy()
        if enemy is None:
            break
        before = enemy.hp
        combat.deal_damage(source, enemy, each, "Physical", card)
        if enemy.hp < before or (enemy.shield == 0 and before == enemy.hp and each > 0):
            if enemy.alive or enemy.hp < before:
                combat.add_status(source, STATUS_POUT, 1, cap=20)
        elif not enemy.alive:
            combat.add_status(source, STATUS_POUT, 1, cap=20)
        else:
            # Hit still counts if damage was fully blocked.
            combat.add_status(source, STATUS_POUT, 1, cap=20)


def play_boiling(combat: Combat, source, card: CardInstance, target) -> None:
    combat.add_status(source, "power_boiling_point", 1)


def play_finale(combat: Combat, source, card: CardInstance, target) -> None:
    if combat.tantrum(source) < 4:
        raise combat.IllegalPlay("Grand Finale needs 4+ Tantrum")
    combat.deal_damage_aoe(source, 35 if _up(card) else 25, "Physical", card, extra_tags=("Outburst",))
    combat.enter_meltdown(source, forced_blast=False)
    if _up(card):
        combat.add_status(source, STATUS_RETAIN_AP, 1)


def play_mesh(combat: Combat, source, card: CardInstance, target) -> None:
    combat.gain_block(source, 9 if _up(card) else 6)
    if target is not None and target.intent is not None and target.intent.kind != "attack":
        combat.log(f"{source.name} sieves {target.name}'s {target.intent.label or target.intent.kind}!")
        target.intent = None
    if _up(card):
        combat.draw_cards(source, 1)


def play_dome_crush(combat: Combat, source, card: CardInstance, target) -> None:
    combat.set_stance(source, "Dome")
    if _up(card):
        combat.gain_block(source, 6)
    combat.deal_damage(source, target, source.shield, "Physical", card)


def play_blanket(combat: Combat, source, card: CardInstance, target) -> None:
    combat.add_status(source, "power_blanket_bunker", 6 if _up(card) else 4)


def play_kitchen(combat: Combat, source, card: CardInstance, target) -> None:
    combat.gain_block(source, 16 if _up(card) else 12)
    combat.swap_stance(source)
    if _up(card):
        combat.add_status(source, STATUS_RETAIN_BLOCK, 4)


def play_block_hurl(combat: Combat, source, card: CardInstance, target) -> None:
    combat.deal_damage(source, target, 7 if _up(card) else 4, "Physical", card)
    n = 2 if _up(card) else 1
    for _ in range(n):
        combat.push_clutter(target, "token_sharp_toy")


def play_binky(combat: Combat, source, card: CardInstance, target) -> None:
    combat.add_status(target, "status_fussiness", 5 if _up(card) else 3)
    combat.add_status(target, STATUS_NO_ATTACKS, 1)
    if _up(card):
        combat.draw_cards(source, 1)


def play_toybox(combat: Combat, source, card: CardInstance, target) -> None:
    toys = ("token_wooden_block", "token_rattle", "token_rubber_duck")
    for actor in (source, *combat.living_enemies()):
        while len(actor.hand) < 10:
            combat.push_clutter(actor, combat.rng.choice(toys))
    if _up(card):
        combat.add_status(source, "power_toy_free", 1)


def play_sippy(combat: Combat, source, card: CardInstance, target) -> None:
    combat.gain_ap(source, 3 if _up(card) else 2)
    if not _up(card):
        combat.shuffle_into_draw(source, combat.make_card("ck_sugar_crash"))


def play_sharp_toy(combat: Combat, source, card: CardInstance, target) -> None:
    combat.log(f"{source.name} clears a Sharp Toy.")


def play_wooden_block(combat: Combat, source, card: CardInstance, target) -> None:
    if target is not None and target.team != source.team:
        combat.deal_damage(source, target, 3, "Physical", card)
    else:
        combat.gain_block(source, 2)


def play_rattle(combat: Combat, source, card: CardInstance, target) -> None:
    if target is None:
        target = combat.random_living_enemy() if source.team == "PlayerTeam" else combat.hero
    if target is not None:
        combat.add_status(target, "status_fussiness", 1)


def play_rubber_duck(combat: Combat, source, card: CardInstance, target) -> None:
    combat.draw_cards(source, 1)


def play_sugar_crash(combat: Combat, source, card: CardInstance, target) -> None:
    combat.log(f"{source.name} rides out a Sugar Crash.")


def play_status_dummy(combat: Combat, source, card: CardInstance, target) -> None:
    combat.log(f"{source.name} discards {card.def_id}.")


def _card(**kwargs) -> CardDef:
    return CardDef(**kwargs)


CATALOG: dict[str, CardDef] = {}


def _register(card: CardDef) -> CardDef:
    CATALOG[card.id] = card
    return card


_register(
    _card(
        id="ck_bonk_starter",
        name="Colander Bonk",
        type="Attack",
        tags=("Attack", "Physical"),
        cost=1,
        target="enemy",
        speed="Standard",
        color="starter",
        text="Deal 6 damage. Apply 1 Fussiness.",
        upgrade_text="Deal 9 damage. Apply 2 Fussiness.",
        art_prompt="Retro comic cell, toddler wearing a cream strainer swinging his head forward.",
        resolve=play_bonk,
    )
)
_register(
    _card(
        id="ck_bonk_starter_2",
        name="Colander Bonk",
        type="Attack",
        tags=("Attack", "Physical"),
        cost=1,
        target="enemy",
        speed="Standard",
        color="starter",
        text="Deal 6 damage. Apply 1 Fussiness.",
        upgrade_text="Deal 9 damage. Apply 2 Fussiness.",
        art_prompt="Dynamic low-angle comic frame, Colander Kid delivering an energetic headbutt.",
        resolve=play_bonk,
    )
)
_register(
    _card(
        id="ck_bonk_starter_3",
        name="Colander Bonk",
        type="Attack",
        tags=("Attack", "Physical"),
        cost=1,
        target="enemy",
        speed="Standard",
        color="starter",
        text="Deal 6 damage. Apply 1 Fussiness.",
        upgrade_text="Deal 9 damage. Apply 2 Fussiness.",
        art_prompt="Side-profile panel, toddler charging into a bank robber's knee.",
        resolve=play_bonk,
    )
)
_register(
    _card(
        id="ck_bonk_starter_4",
        name="Colander Bonk",
        type="Attack",
        tags=("Attack", "Physical"),
        cost=1,
        target="enemy",
        speed="Standard",
        color="starter",
        text="Deal 6 damage. Apply 1 Fussiness.",
        upgrade_text="Deal 9 damage. Apply 2 Fussiness.",
        art_prompt="Golden Age pop-art panel, strainer glistening with motion blur.",
        resolve=play_bonk,
    )
)
_register(
    _card(
        id="ck_crawl_starter",
        name="Crawl Away",
        type="Skill",
        tags=("Skill", "Maneuver"),
        cost=1,
        target="self",
        speed="Standard",
        color="starter",
        text="Gain 5 Block. If in Sieve Mode, draw 1.",
        upgrade_text="Gain 8 Block. Draw 1 card.",
        art_prompt="Colander Kid speeding away on all fours underneath a coffee table.",
        resolve=play_crawl,
    )
)
_register(
    _card(
        id="ck_crawl_starter_2",
        name="Crawl Away",
        type="Skill",
        tags=("Skill", "Maneuver"),
        cost=1,
        target="self",
        speed="Standard",
        color="starter",
        text="Gain 5 Block. If in Sieve Mode, draw 1.",
        upgrade_text="Gain 8 Block. Draw 1 card.",
        art_prompt="Baby scrambling between the legs of confused henchmen.",
        resolve=play_crawl,
    )
)
_register(
    _card(
        id="ck_crawl_starter_3",
        name="Crawl Away",
        type="Skill",
        tags=("Skill", "Maneuver"),
        cost=1,
        target="self",
        speed="Standard",
        color="starter",
        text="Gain 5 Block. If in Sieve Mode, draw 1.",
        upgrade_text="Gain 8 Block. Draw 1 card.",
        art_prompt="Top-down grid panel, swift diaper sprint on the rug.",
        resolve=play_crawl,
    )
)
_register(
    _card(
        id="ck_crawl_starter_4",
        name="Crawl Away",
        type="Skill",
        tags=("Skill", "Maneuver"),
        cost=1,
        target="self",
        speed="Standard",
        color="starter",
        text="Gain 5 Block. If in Sieve Mode, draw 1.",
        upgrade_text="Gain 8 Block. Draw 1 card.",
        art_prompt="Toddler sliding behind an armchair with a mesh energy bubble.",
        resolve=play_crawl,
    )
)
_register(
    _card(
        id="ck_pout_starter",
        name="Pucker Pout",
        type="Skill",
        tags=("Skill", "Outburst"),
        cost=1,
        target="self",
        speed="Standard",
        color="red",
        text="Gain 2 Pout. +1 Tantrum.",
        upgrade_text="Gain 3 Pout. +1 Tantrum. Draw 1.",
        art_prompt="Ultra close-up vintage comic panel, dramatic pursed lips.",
        resolve=play_pucker,
    )
)
_register(
    _card(
        id="ck_waaah_blast",
        name="WAAAH-Blast",
        type="Attack",
        tags=("Attack", "Sonic"),
        cost=2,
        target="all_enemies",
        speed="Standard",
        color="red",
        text="Deal 10 Sonic to all. If Tantrum ≥ 3, apply 1 Stun.",
        upgrade_text="Deal 14 Sonic. Stun all enemies.",
        art_prompt="Wide cinematic panel, Colander Kid levitating, shockwave rings.",
        resolve=play_waaah,
    )
)
_register(
    _card(
        id="ck_tantrum_throw",
        name="Tantrum Throw",
        type="Attack",
        tags=("Attack", "Outburst"),
        cost=1,
        target="enemy",
        speed="Standard",
        color="red",
        text="Gain 2 Pout. Consume all Pout: 5 damage per stack.",
        upgrade_text="Gain 3 Pout. 7 damage per stack consumed.",
        art_prompt="Toddler flinging wooden alphabet blocks with blazing energy.",
        resolve=play_tantrum_throw,
        rarity="common",
    )
)
_register(
    _card(
        id="ck_floor_thrash",
        name="Floor Thrash",
        type="Attack",
        tags=("Attack", "Outburst"),
        cost=2,
        target="all_enemies",
        speed="Standard",
        color="red",
        text="+2 Tantrum. Deal 8 to all. Take 2 recoil.",
        upgrade_text="Deal 12 to all. No recoil.",
        art_prompt="Colander Kid rolling furiously on a patterned carpet.",
        resolve=play_floor_thrash,
        rarity="uncommon",
    )
)
_register(
    _card(
        id="ck_screaming_fist",
        name="Flailing Fury",
        type="Attack",
        tags=("Attack", "Physical"),
        cost=1,
        target="random",
        speed="Standard",
        color="red",
        text="Hit 3 times for 3. Each hit gains 1 Pout.",
        upgrade_text="Hit 4 times for 4.",
        art_prompt="Multi-arm blur, windmill toddler fist strike.",
        resolve=play_flailing,
        rarity="common",
    )
)
_register(
    _card(
        id="ck_red_face",
        name="Boiling Point",
        type="Power",
        tags=("Power", "Tantrum"),
        cost=1,
        target="self",
        speed="Standard",
        color="red",
        text="When you take unblocked damage, gain 1 Pout and +1 Tantrum.",
        upgrade_text="Costs 0 AP.",
        art_prompt="Kid's face turning tomato red with steam from colander vents.",
        resolve=play_boiling,
        upgrade_cost=0,
        exhaust=True,
        rarity="uncommon",
    )
)
_register(
    _card(
        id="ck_epic_meltdown",
        name="Grand Finale",
        type="Attack",
        tags=("Attack", "Outburst"),
        cost=3,
        target="all_enemies",
        speed="Standard",
        color="red",
        text="Play only at 4+ Tantrum. Deal 25. Enter Meltdown.",
        upgrade_text="Deal 35. Retain 1 AP next turn.",
        art_prompt="Splash page, fiery aura, I DEMAND NAPTIME!",
        resolve=play_finale,
        rarity="rare",
    )
)
_register(
    _card(
        id="ck_sieve_filter",
        name="Mesh Deflection",
        type="Skill",
        tags=("Skill", "Bastion"),
        cost=1,
        target="enemy",
        speed="Instant",
        color="blue",
        text="Cancel a non-attack intent. Gain 6 Block.",
        upgrade_text="Gain 9 Block and draw 1.",
        art_prompt="Colander helmet refracting laser beams through mesh holes.",
        resolve=play_mesh,
        rarity="common",
    )
)
_register(
    _card(
        id="ck_dome_slam",
        name="Dome Crush",
        type="Attack",
        tags=("Attack", "Bastion"),
        cost=2,
        target="enemy",
        speed="Standard",
        color="blue",
        text="Swap to Dome Mode. Deal damage equal to your Block.",
        upgrade_text="Gain 6 Block first, then deal Block damage.",
        art_prompt="Colander Kid diving head-first like a cannonball.",
        resolve=play_dome_crush,
        rarity="uncommon",
    )
)
_register(
    _card(
        id="ck_colander_fort",
        name="Blanket Bunker",
        type="Power",
        tags=("Power", "Bastion"),
        cost=2,
        target="self",
        speed="Standard",
        color="blue",
        text="While in Dome Mode, gain 4 Block whenever you draw.",
        upgrade_text="Gain 6 Block whenever you draw.",
        art_prompt="Baby under a quilted fort wearing the colander like a crown.",
        resolve=play_blanket,
        exhaust=True,
        rarity="uncommon",
    )
)
_register(
    _card(
        id="ck_impenetrable",
        name="Kitchen Armor",
        type="Skill",
        tags=("Skill", "Bastion"),
        cost=1,
        target="self",
        speed="Standard",
        color="blue",
        text="Gain 12 Block. Swap Stance.",
        upgrade_text="Gain 16 Block. Swap Stance. Retain 4 Block.",
        art_prompt="Blueprint-style comic of the colander helmet's integrity.",
        resolve=play_kitchen,
        rarity="common",
    )
)
_register(
    _card(
        id="ck_throw_block",
        name="Block Hurl",
        type="Attack",
        tags=("Attack", "Item"),
        cost=0,
        target="enemy",
        speed="Standard",
        color="yellow",
        text="Deal 4. Put 1 Sharp Toy into the target's hand.",
        upgrade_text="Deal 7. Put 2 Sharp Toys into their hand.",
        art_prompt="Sharp wooden alphabet block flying at a villain's foot.",
        resolve=play_block_hurl,
        rarity="common",
    )
)
_register(
    _card(
        id="ck_pacifier_plug",
        name="Binky Peace",
        type="Skill",
        tags=("Skill", "Item"),
        cost=1,
        target="enemy",
        speed="Standard",
        color="yellow",
        text="Apply 3 Fussiness. Target cannot play Attacks this turn.",
        upgrade_text="Apply 5 Fussiness. Draw 1.",
        art_prompt="Toddler offering a glowing golden pacifier.",
        resolve=play_binky,
        rarity="uncommon",
    )
)
_register(
    _card(
        id="ck_toybox_spill",
        name="Toybox Explosion",
        type="Skill",
        tags=("Skill", "Chaos"),
        cost=2,
        target="all",
        speed="Standard",
        color="yellow",
        text="Fill both hands with ephemeral Toy Tokens.",
        upgrade_text="Your Toy Tokens cost 0 for the rest of combat.",
        art_prompt="Toy trunk bursting with rubber ducks and rattling rings.",
        resolve=play_toybox,
        rarity="rare",
    )
)
_register(
    _card(
        id="ck_sippy_chug",
        name="Juice Box Surge",
        type="Skill",
        tags=("Skill", "Item"),
        cost=1,
        target="self",
        speed="Standard",
        color="yellow",
        text="Gain 2 AP. Shuffle 1 Sugar Crash into your draw pile.",
        upgrade_text="Gain 3 AP. No Sugar Crash.",
        art_prompt="Colander Kid drinking a juice box, lightning in his eyes.",
        resolve=play_sippy,
        rarity="uncommon",
    )
)
_register(
    _card(
        id="ck_sugar_crash",
        name="Sugar Crash",
        type="Curse",
        tags=("Curse", "Status"),
        cost=1,
        target="self",
        speed="Standard",
        color="curse",
        text="Ethereal. If this is in your hand at end of turn, take 3 damage.",
        upgrade_text="",
        art_prompt="Empty juice box with a comic headache spiral.",
        resolve=play_sugar_crash,
        exhaust=True,
        curse=True,
        status=True,
        rarity="curse",
    )
)
_register(
    _card(
        id="token_sharp_toy",
        name="Sharp Toy",
        type="Item",
        tags=("Item", "Clutter", "Token"),
        cost=1,
        target="self",
        speed="Standard",
        color="clutter",
        text="Clutter. Spend 1 AP to toss it out.",
        upgrade_text="",
        art_prompt="Jagged wooden block with a comic BAN! starburst.",
        resolve=play_sharp_toy,
        exhaust=True,
        ephemeral=True,
        token=True,
        rarity="token",
    )
)
_register(
    _card(
        id="token_wooden_block",
        name="Wooden Block",
        type="Item",
        tags=("Item", "Token", "Toy"),
        cost=0,
        target="enemy",
        speed="Standard",
        color="yellow",
        text="Ephemeral. Deal 3 damage.",
        upgrade_text="",
        art_prompt="Alphabet block with a bold A.",
        resolve=play_wooden_block,
        exhaust=True,
        ephemeral=True,
        token=True,
        rarity="token",
    )
)
_register(
    _card(
        id="token_rattle",
        name="Rattle Trap",
        type="Item",
        tags=("Item", "Token", "Toy"),
        cost=1,
        target="enemy",
        speed="Standard",
        color="yellow",
        text="Ephemeral. Apply 1 Fussiness.",
        upgrade_text="",
        art_prompt="Plastic rattle ringing with sonic lines.",
        resolve=play_rattle,
        exhaust=True,
        ephemeral=True,
        token=True,
        rarity="token",
    )
)
_register(
    _card(
        id="token_rubber_duck",
        name="Rubber Duck",
        type="Item",
        tags=("Item", "Token", "Toy"),
        cost=0,
        target="self",
        speed="Standard",
        color="yellow",
        text="Ephemeral. Draw 1 card.",
        upgrade_text="",
        art_prompt="Determined rubber duck wearing a tiny colander.",
        resolve=play_rubber_duck,
        exhaust=True,
        ephemeral=True,
        token=True,
        rarity="token",
    )
)
_register(
    _card(
        id="ck_dummy_status",
        name="Soggy Sock",
        type="Status",
        tags=("Status",),
        cost=1,
        target="self",
        speed="Standard",
        color="curse",
        text="Status. Exhausts when played.",
        upgrade_text="",
        art_prompt="A damp sock with a frown.",
        resolve=play_status_dummy,
        exhaust=True,
        status=True,
        rarity="status",
    )
)


STARTER_DECK_IDS = (
    "ck_bonk_starter",
    "ck_bonk_starter_2",
    "ck_bonk_starter_3",
    "ck_bonk_starter_4",
    "ck_crawl_starter",
    "ck_crawl_starter_2",
    "ck_crawl_starter_3",
    "ck_crawl_starter_4",
    "ck_pout_starter",
    "ck_waaah_blast",
)

REWARD_POOL_IDS = (
    "ck_tantrum_throw",
    "ck_floor_thrash",
    "ck_screaming_fist",
    "ck_red_face",
    "ck_epic_meltdown",
    "ck_sieve_filter",
    "ck_dome_slam",
    "ck_colander_fort",
    "ck_impenetrable",
    "ck_throw_block",
    "ck_pacifier_plug",
    "ck_toybox_spill",
    "ck_sippy_chug",
)


def get_card(card_id: str) -> CardDef:
    return CATALOG[card_id]


def display_text(card: CardInstance) -> str:
    definition = get_card(card.def_id)
    return definition.upgrade_text if card.upgraded else definition.text


def card_cost(card: CardInstance, actor=None) -> int:
    definition = get_card(card.def_id)
    if card.cost_override is not None:
        return card.cost_override
    if card.upgraded and definition.upgrade_cost is not None:
        base = definition.upgrade_cost
    else:
        base = definition.cost
    if (
        actor is not None
        and actor.get_status("power_toy_free")
        and "Toy" in definition.tags
    ):
        return 0
    if (
        actor is not None
        and actor.get_status(STATUS_MELTDOWN)
        and "Outburst" in definition.tags
    ):
        return 0
    return base
