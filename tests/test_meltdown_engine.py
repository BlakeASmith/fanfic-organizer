from __future__ import annotations

import pytest

from meltdown.cards import STARTER_DECK_IDS, card_cost, get_card
from meltdown.enemies import BANK_ROBBER, SUIT_GOON
from meltdown.engine import IllegalPlay, new_combat
from meltdown.models import (
    STATUS_DROWSY,
    STATUS_FUSSINESS,
    STATUS_MELTDOWN,
    STATUS_POUT,
    STANCE_DOME,
    STANCE_SIEVE,
)
from meltdown.relics import RELIC_BINKY, RELIC_ELMO, RELIC_MESH, RELIC_SIPPY
from meltdown.run import new_run


def _combat(deck=None, enemies=None, relics=None, seed=1, stance=STANCE_SIEVE):
    combat = new_combat(
        enemy_defs=enemies or [SUIT_GOON],
        relics=relics or [RELIC_MESH],
        seed=seed,
        deck_ids=deck or list(STARTER_DECK_IDS),
    )
    combat.start_combat(stance)
    return combat


def _hand_named(combat, name):
    return [c for c in combat.hero.hand if get_card(c.def_id).name == name]


def test_starter_deck_has_ten_named_cards():
    names = [get_card(cid).name for cid in STARTER_DECK_IDS]
    assert names.count("Colander Bonk") == 4
    assert names.count("Crawl Away") == 4
    assert names.count("Pucker Pout") == 1
    assert names.count("WAAAH-Blast") == 1


def test_bonk_deals_damage_and_applies_fussiness():
    combat = _combat(deck=["ck_bonk_starter"] * 10)
    bonk = combat.hero.hand[0]
    enemy = combat.enemies[0]
    combat.play(bonk.uid, enemy.id)
    assert enemy.hp == enemy.max_hp - 6
    assert enemy.get_status(STATUS_FUSSINESS) == 1


def test_bonk_upgrade_hits_harder():
    combat = new_combat(
        enemy_defs=[SUIT_GOON],
        deck_ids=["ck_bonk_starter"] * 10,
        upgraded={"ck_bonk_starter"},
        seed=2,
    )
    combat.start_combat(STANCE_SIEVE)
    bonk = combat.hero.hand[0]
    enemy = combat.enemies[0]
    combat.play(bonk.uid, enemy.id)
    assert enemy.hp == enemy.max_hp - 9
    assert enemy.get_status(STATUS_FUSSINESS) == 2


def test_crawl_away_block_and_sieve_draw():
    combat = _combat(deck=["ck_crawl_starter"] * 5 + ["ck_bonk_starter"] * 5)
    before = len(combat.hero.hand)
    crawl = _hand_named(combat, "Crawl Away")[0]
    combat.play(crawl.uid)
    assert combat.hero.shield == 5
    assert len(combat.hero.hand) == before  # spent 1, drew 1 in Sieve


def test_crawl_no_draw_in_dome_unless_upgraded():
    combat = _combat(
        deck=["ck_crawl_starter"] * 10,
        stance=STANCE_DOME,
    )
    before_draw = len(combat.hero.draw)
    crawl = combat.hero.hand[0]
    combat.play(crawl.uid)
    assert combat.hero.shield == 5
    assert len(combat.hero.draw) == before_draw


def test_pucker_pout_raises_tantrum_and_pout():
    combat = _combat(deck=["ck_pout_starter"] * 10)
    card = combat.hero.hand[0]
    combat.play(card.uid)
    assert combat.hero.get_status(STATUS_POUT) == 2
    assert combat.tantrum(combat.hero) == 1


def test_waaah_blast_hits_all_and_stuns_at_tantrum_3():
    combat = _combat(deck=["ck_waaah_blast"] * 10, enemies=[SUIT_GOON, SUIT_GOON])
    combat.hero.state["tantrum_meter"] = 3
    card = combat.hero.hand[0]
    combat.play(card.uid)
    for enemy in combat.enemies:
        assert enemy.hp == enemy.max_hp - 10
        assert enemy.get_status("status_stun") == 1


def test_tantrum_throw_consumes_pout_for_five_each():
    combat = _combat(deck=["ck_tantrum_throw"] * 10)
    combat.add_status(combat.hero, STATUS_POUT, 2)
    enemy = combat.enemies[0]
    card = combat.hero.hand[0]
    combat.play(card.uid, enemy.id)
    # Gain 2 more (4 total), consume all: 5 * 4 = 20
    assert enemy.hp == enemy.max_hp - 20
    assert combat.hero.get_status(STATUS_POUT) == 0


def test_outburst_pipeline_adds_two_per_pout_then_clears():
    combat = _combat(deck=["ck_floor_thrash"] * 10)
    combat.add_status(combat.hero, STATUS_POUT, 3)
    enemy = combat.enemies[0]
    card = combat.hero.hand[0]
    combat.hero.ap = 3
    combat.play(card.uid)
    # 8 base + 6 pout, plus 2 recoil on the hero
    assert enemy.hp == enemy.max_hp - 14
    assert combat.hero.get_status(STATUS_POUT) == 0
    assert combat.hero.hp == combat.hero.max_hp - 2


def test_fussiness_reduces_outgoing_attack_damage():
    combat = _combat(deck=["ck_bonk_starter"] * 10)
    combat.add_status(combat.hero, STATUS_FUSSINESS, 4)
    enemy = combat.enemies[0]
    card = combat.hero.hand[0]
    combat.play(card.uid, enemy.id)
    assert enemy.hp == enemy.max_hp - 2


def test_fussiness_explodes_to_allies_on_unblocked_hit():
    combat = _combat(deck=["ck_bonk_starter"] * 10, enemies=[SUIT_GOON, SUIT_GOON])
    a, b = combat.enemies
    combat.add_status(a, STATUS_FUSSINESS, 6)
    card = combat.hero.hand[0]
    combat.play(card.uid, a.id)
    assert b.hp == b.max_hp - 3


def test_unblocked_damage_raises_tantrum_and_triggers_meltdown_at_five():
    combat = _combat(deck=["ck_crawl_starter"] * 10)
    combat.hero.shield = 0
    combat.deal_damage(combat.enemies[0], combat.hero, 4, "Physical")
    combat.deal_damage(combat.enemies[0], combat.hero, 4, "Physical")
    combat.deal_damage(combat.enemies[0], combat.hero, 4, "Physical")
    combat.deal_damage(combat.enemies[0], combat.hero, 4, "Physical")
    assert combat.tantrum(combat.hero) == 4
    combat.deal_damage(combat.enemies[0], combat.hero, 4, "Physical")
    assert combat.hero.get_status(STATUS_MELTDOWN) == 1
    assert any("Forced WAAAH-Blast" in line for line in combat.log_lines)


def test_meltdown_makes_outbursts_free_and_double():
    combat = _combat(deck=["ck_tantrum_throw"] * 10)
    combat.enter_meltdown(combat.hero, forced_blast=False)
    card = combat.hero.hand[0]
    assert card_cost(card, combat.hero) == 0
    enemy = combat.enemies[0]
    combat.play(card.uid, enemy.id)
    # Gain 2 pout, consume 2 * 5 = 10, doubled by meltdown = 20
    assert enemy.hp == enemy.max_hp - 20


def test_meltdown_applies_drowsy_and_resets_tantrum():
    combat = _combat(deck=["ck_crawl_starter"] * 20)
    combat.enter_meltdown(combat.hero, forced_blast=False)
    for enemy in combat.enemies:
        enemy.intent = None
        enemy.add_status("status_stun", 1)
    combat.end_hero_turn()
    assert combat.tantrum(combat.hero) == 0
    assert combat.hero.ap == 1


def test_sippy_relic_skips_drowsy_and_grows_max_ap():
    combat = _combat(deck=["ck_crawl_starter"] * 20, relics=[RELIC_MESH, RELIC_SIPPY])
    combat.enter_meltdown(combat.hero, forced_blast=False)
    combat.end_hero_turn()
    assert combat.hero.get_status(STATUS_DROWSY) == 0
    assert combat.hero.ap >= 4


def test_sieve_exiles_first_status_and_redraws():
    combat = new_combat(
        enemy_defs=[SUIT_GOON],
        deck_ids=["ck_dummy_status", "ck_bonk_starter"] + ["ck_crawl_starter"] * 8,
        seed=3,
    )
    combat.hero.draw = [
        *[combat.make_card("ck_crawl_starter") for _ in range(8)],
        combat.make_card("ck_bonk_starter"),
        combat.make_card("ck_dummy_status"),
    ]
    combat.start_combat(STANCE_SIEVE)
    names = [get_card(c.def_id).name for c in combat.hero.hand]
    assert "Soggy Sock" not in names
    assert any(c.def_id == "ck_dummy_status" for c in combat.hero.exile)


def test_sieve_block_holds_through_enemy_turn():
    combat = _combat(deck=["ck_crawl_starter"] * 20, stance=STANCE_SIEVE)
    combat.hero.shield = 10
    combat.end_hero_turn()
    assert combat.hero.hp == combat.hero.max_hp
    assert combat.hero.shield == 0


def test_dome_keeps_block_and_bonks_front_enemy():
    combat = _combat(deck=["ck_crawl_starter"] * 20, stance=STANCE_DOME)
    combat.hero.shield = 10
    enemy_hp = combat.enemies[0].hp
    combat.end_hero_turn()
    # 50% leftover Block bonks (5), then the goon's 7-damage swing hits retained Block.
    assert combat.enemies[0].hp == enemy_hp - 5
    assert combat.hero.shield == 3
    assert combat.hero.hp == combat.hero.max_hp


def test_stance_swap_from_relic_grants_block():
    combat = _combat(deck=["ck_impenetrable"] * 10, stance=STANCE_SIEVE)
    card = combat.hero.hand[0]
    combat.play(card.uid)
    # 12 block + 4 from relic swap
    assert combat.hero.shield == 16
    assert combat.hero.state["stance"] == STANCE_DOME


def test_elmo_onesie_triggers_on_big_hit():
    combat = _combat(deck=["ck_bonk_starter"] * 10, relics=[RELIC_MESH, RELIC_ELMO])
    combat.deal_damage(combat.enemies[0], combat.hero, 9, "Physical")
    assert combat.hero.get_status(STATUS_POUT) == 2
    assert any(get_card(c.def_id).name == "Crawl Away" for c in combat.hero.hand)


def test_golden_binky_eats_curse_after_draw():
    combat = new_combat(
        enemy_defs=[SUIT_GOON],
        relics=[RELIC_MESH, RELIC_BINKY],
        deck_ids=["ck_sugar_crash"] + ["ck_bonk_starter"] * 9,
        seed=4,
    )
    combat.hero.draw = [combat.make_card("ck_bonk_starter") for _ in range(9)] + [
        combat.make_card("ck_sugar_crash")
    ]
    combat.start_combat(STANCE_DOME)
    assert not any(c.def_id == "ck_sugar_crash" for c in combat.hero.hand)
    assert any(c.def_id == "ck_sugar_crash" for c in combat.hero.exile)
    assert combat.hero.ap == 4


def test_block_hurl_clutters_enemy_hand():
    combat = _combat(deck=["ck_throw_block"] * 10)
    enemy = combat.enemies[0]
    card = combat.hero.hand[0]
    combat.play(card.uid, enemy.id)
    assert any(c.def_id == "token_sharp_toy" for c in enemy.hand)
    combat.end_hero_turn()
    # Enemy spends the turn clearing clutter instead of attacking.
    assert combat.hero.hp == combat.hero.max_hp


def test_grand_finale_requires_tantrum():
    combat = _combat(deck=["ck_epic_meltdown"] * 10)
    card = combat.hero.hand[0]
    combat.hero.ap = 3
    with pytest.raises(IllegalPlay):
        combat.play(card.uid)
    combat.hero.state["tantrum_meter"] = 4
    combat.play(card.uid)
    assert combat.hero.get_status(STATUS_MELTDOWN) == 1
    assert combat.enemies[0].hp == max(0, SUIT_GOON.hp - 25)


def test_mesh_deflection_cancels_non_attack_intent():
    combat = _combat(deck=["ck_sieve_filter"] * 10, enemies=[BANK_ROBBER])
    enemy = combat.enemies[0]
    enemy.intent_index = 0
    combat._roll_intent(enemy)
    assert enemy.intent.kind == "block"
    card = combat.hero.hand[0]
    combat.play(card.uid, enemy.id)
    assert enemy.intent is None
    assert combat.hero.shield == 6


def test_binky_peace_blocks_enemy_attacks():
    combat = _combat(deck=["ck_pacifier_plug"] * 10)
    enemy = combat.enemies[0]
    enemy.intent = enemy.state["intent_pattern"][0]
    card = combat.hero.hand[0]
    combat.play(card.uid, enemy.id)
    hp = combat.hero.hp
    combat.end_hero_turn()
    assert combat.hero.hp == hp


def test_juice_box_adds_sugar_crash():
    combat = _combat(deck=["ck_sippy_chug"] * 5 + ["ck_bonk_starter"] * 5)
    card = [c for c in combat.hero.hand if c.def_id == "ck_sippy_chug"][0]
    ap_before = combat.hero.ap
    combat.play(card.uid)
    assert combat.hero.ap == ap_before - 1 + 2
    pile = combat.hero.draw + combat.hero.discard + combat.hero.hand
    assert any(c.def_id == "ck_sugar_crash" for c in pile)


def test_boiling_point_gains_pout_when_hit():
    combat = _combat(deck=["ck_red_face"] * 10)
    card = combat.hero.hand[0]
    combat.play(card.uid)
    combat.deal_damage(combat.enemies[0], combat.hero, 3, "Physical")
    assert combat.hero.get_status(STATUS_POUT) == 1


def test_dome_crush_uses_current_block():
    combat = _combat(deck=["ck_dome_slam"] * 10, stance=STANCE_SIEVE)
    combat.hero.shield = 8
    card = combat.hero.hand[0]
    combat.hero.ap = 3
    enemy = combat.enemies[0]
    combat.play(card.uid, enemy.id)
    # Swap Sieve→Dome grants +4 block (relic), damage = 12
    assert enemy.hp == enemy.max_hp - 12
    assert combat.hero.state["stance"] == STANCE_DOME


def test_run_choosing_stance_starts_first_fight():
    run = new_run(seed=11)
    assert run.phase == "stance_select"
    run.choose_stance("Dome")
    assert run.phase == "combat"
    assert run.combat is not None
    assert run.combat.hero.state["stance"] == "Dome"


def test_run_victory_after_clearing_path(monkeypatch):
    run = new_run(seed=5)
    run.choose_stance("Sieve")
    # Instantly win each node.
    while run.phase == "combat":
        for enemy in run.combat.enemies:
            enemy.hp = 0
            enemy.alive = False
        run.combat._check_end()
        run.after_combat()
        if run.phase == "relic":
            run.take_relic(run.relic_choices[0] if run.relic_choices else None)
        if run.phase == "reward":
            run.take_reward(None)
        if run.phase == "upgrade":
            run.upgrade_card(None)
    assert run.phase == "victory"


def test_illegal_play_when_broke():
    combat = _combat(deck=["ck_waaah_blast"] * 10)
    combat.hero.ap = 1
    with pytest.raises(IllegalPlay):
        combat.play(combat.hero.hand[0].uid)
