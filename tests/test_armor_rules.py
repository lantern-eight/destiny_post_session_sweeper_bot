'''Tests for the armor keep rules: set coverage, stat champions, and exotics.'''

from __future__ import annotations

from dataclasses import replace

from src.armor_rules import decide_armor
from src.inventory import ArmorPiece
from src.preferences import ArmorPreferences, ExoticOverride, Preferences
from tests.fixtures import make_piece

ARMOR_PREFERENCES = ArmorPreferences(
  archetype_order=('Powerhouse', 'Colossus', 'Gunner'),
  archetype_override_min_tier=3,
  stat_priority=('Weapons', 'Super', 'Health', 'Grenade', 'Class', 'Melee'),
)
PREFERENCES = Preferences(
  classes=('warlock', 'hunter', 'titan'), armor=ARMOR_PREFERENCES
)


def reasons(decisions, instance_id: str) -> str:
  return ' | '.join(decisions[instance_id].reasons)


def techsec(instance_id: str, archetype: str, tier: int, **options) -> ArmorPiece:
  primary, secondary = {
    'Powerhouse': ('Weapons', 'Super'),
    'Colossus': ('Super', 'Health'),
    'Bulwark': ('Health', 'Class'),
  }[archetype]
  stats = {
    primary: 30,
    secondary: 25 if tier == 5 else 20,
    'Melee': 20 if tier == 5 else 15,
  }
  return make_piece(
    instance_id, stats, tier=tier, archetype=archetype, set_name='Techsec', **options
  )


def set_keeper(decisions) -> str:
  '''The piece kept for set coverage (shard reasons also name the set, so match
  only the keep reason's "<set> <slot> for <class>" form).'''
  keepers = [
    instance_id
    for instance_id, decision in decisions.items()
    if any(reason.startswith('Techsec Helmet for') for reason in decision.reasons)
  ]
  assert len(keepers) == 1
  return keepers[0]


def test_preferred_archetype_at_t3_beats_non_preferred_t5():
  pieces = [techsec('101', 'Powerhouse', 3), techsec('102', 'Bulwark', 5)]
  assert set_keeper(decide_armor(pieces, PREFERENCES, set())) == '101'


def test_preferred_archetype_below_min_tier_does_not_override():
  pieces = [techsec('101', 'Powerhouse', 2), techsec('102', 'Bulwark', 5)]
  assert set_keeper(decide_armor(pieces, PREFERENCES, set())) == '102'


def test_higher_tier_wins_between_preferred_archetypes():
  pieces = [techsec('101', 'Powerhouse', 4), techsec('102', 'Colossus', 5)]
  assert set_keeper(decide_armor(pieces, PREFERENCES, set())) == '102'


def test_archetype_order_decides_between_equal_t5_pieces():
  pieces = [techsec('101', 'Colossus', 5), techsec('102', 'Powerhouse', 5)]
  assert set_keeper(decide_armor(pieces, PREFERENCES, set())) == '102'


def test_ties_go_to_masterworked_then_kept_last_run_then_oldest():
  twins = [techsec('201', 'Powerhouse', 5), techsec('202', 'Powerhouse', 5)]
  assert set_keeper(decide_armor(twins, PREFERENCES, set())) == '201'
  assert set_keeper(decide_armor(twins, PREFERENCES, {'202'})) == '202'
  masterworked_twin = [
    techsec('201', 'Powerhouse', 5),
    techsec('202', 'Powerhouse', 5, masterworked=True),
  ]
  assert set_keeper(decide_armor(masterworked_twin, PREFERENCES, {'201'})) == '202'


def test_stat_champion_counts_the_t5_tuning_bonus():
  untuned = make_piece(
    '301', {'Melee': 30, 'Health': 25, 'Class': 20}, archetype='Brawler'
  )
  tuned = make_piece(
    '302', {'Melee': 30, 'Health': 25, 'Class': 20}, archetype='Brawler', tuned='Melee'
  )
  decisions = decide_armor([untuned, tuned], PREFERENCES, set())
  assert 'best Melee Helmet for Warlock (35 with tuning)' in reasons(decisions, '302')
  assert 'best Melee' not in reasons(decisions, '301')


def test_legacy_armor_outside_a_raid_survives_only_as_a_stat_champion():
  new_piece = make_piece(
    '401', {'Weapons': 30, 'Super': 25, 'Health': 20}, archetype='Powerhouse'
  )
  best_melee_legacy = make_piece('402', {'Melee': 24, 'Class': 10}, tier=0)
  outclassed_legacy = make_piece('403', {'Melee': 12, 'Weapons': 10}, tier=0)
  decisions = decide_armor(
    [new_piece, best_melee_legacy, outclassed_legacy], PREFERENCES, set()
  )
  assert decisions['402'].action == 'keep'
  assert 'best Melee Helmet' in reasons(decisions, '402')
  assert decisions['403'].action == 'shard'
  assert reasons(decisions, '403').startswith('legacy piece')


def test_each_raid_keeps_one_piece_per_slot_for_its_mod_slot():
  stat_keeper = make_piece('411', {'Weapons': 30, 'Melee': 30}, archetype='Powerhouse')
  vault_of_glass = [
    make_piece('412', {'Weapons': 10, 'Melee': 10}, tier=0, raid='Vault of Glass'),
    make_piece('413', {'Weapons': 12, 'Melee': 9}, tier=0, raid='Vault of Glass'),
  ]
  last_wish = make_piece('414', {'Weapons': 5}, tier=0, raid='Last Wish')
  decisions = decide_armor(
    [stat_keeper, *vault_of_glass, last_wish], PREFERENCES, set()
  )
  assert {key: decision.action for key, decision in decisions.items()} == {
    '411': 'keep',
    '412': 'shard',
    '413': 'keep',
    '414': 'keep',
  }
  assert reasons(decisions, '413') == (
    'Vault of Glass Helmet for Warlock, for its raid mod slot (legacy, 21 total)'
  )
  assert 'Vault of Glass Helmet kept is 413' in reasons(decisions, '412')


def test_a_perk_in_perk_sets_keeps_one_piece_per_slot():
  preferences = replace(
    PREFERENCES,
    armor=replace(ARMOR_PREFERENCES, perk_sets=("Iron Lord's Pride",)),
  )
  stat_keeper = make_piece('421', {'Weapons': 30, 'Melee': 30}, archetype='Powerhouse')
  iron_banner = [
    make_piece('422', {'Weapons': 10}, tier=0, armor_perks=("Iron Lord's Pride",)),
    make_piece('423', {'Weapons': 12}, tier=0, armor_perks=("Iron Lord's Pride",)),
  ]
  decisions = decide_armor([stat_keeper, *iron_banner], preferences, set())
  assert {key: decision.action for key, decision in decisions.items()} == {
    '421': 'keep',
    '422': 'shard',
    '423': 'keep',
  }
  assert reasons(decisions, '423') == (
    "Iron Lord's Pride Helmet for Warlock, for that perk (legacy, 12 total)"
  )
  # Without the setting, the perk keeps nothing.
  assert decide_armor(iron_banner, PREFERENCES, set())['423'].action == 'keep'
  assert decide_armor([stat_keeper, *iron_banner], PREFERENCES, set())[
    '423'
  ].action == ('shard')


def test_a_shard_names_the_kept_pieces_of_the_same_name():
  hood = {'archetype': 'Powerhouse', 'set_name': 'Techsec', 'name': 'Techsec Hood'}
  kept = make_piece('431', {'Weapons': 30, 'Super': 25, 'Melee': 20}, **hood)
  duplicate = make_piece(
    '432', {'Weapons': 28, 'Super': 20, 'Melee': 15}, tier=4, **hood
  )
  other = make_piece('433', {'Weapons': 20, 'Super': 10}, tier=3, name='Other Hood')
  decisions = decide_armor([kept, duplicate, other], PREFERENCES, set())
  assert decisions['432'].action == 'shard'
  assert decisions['432'].compare_with == ('431',)
  assert decisions['433'].compare_with == ()  # no kept copy of that name


def test_an_exotic_class_item_shard_names_the_copy_with_its_perk_pair():
  stag_swarm = ('Spirit of the Stag', 'Spirit of the Swarm')
  copies = [
    make_piece('441', {'Health': 30, 'Class': 25}, exotic=True, perks=stag_swarm),
    make_piece('442', {'Health': 20, 'Class': 20}, exotic=True, perks=stag_swarm),
    make_piece(
      '443',
      {'Health': 25},
      exotic=True,
      perks=('Spirit of the Star', 'Spirit of the Swarm'),
    ),
  ]
  decisions = decide_armor(copies, PREFERENCES, set())
  assert decisions['442'].action == 'shard'
  assert decisions['442'].compare_with == ('441',)


def test_exotics_do_not_take_stat_champion_spots():
  exotic = make_piece('501', {'Melee': 30}, exotic=True, name='Exotic Helm')
  legendary = make_piece('502', {'Melee': 25, 'Health': 20}, archetype='Brawler')
  decisions = decide_armor([exotic, legendary], PREFERENCES, set())
  assert 'best Melee Helmet' in reasons(decisions, '502')
  assert 'best Melee' not in reasons(decisions, '501')


def test_new_system_exotic_copy_replaces_legacy_copy():
  legacy_copy = make_piece('601', {'Melee': 20, 'Health': 20}, tier=0, exotic=True)
  new_copy = make_piece(
    '602',
    {'Weapons': 30, 'Super': 25, 'Health': 20},
    archetype='Powerhouse',
    exotic=True,
  )
  decisions = decide_armor([legacy_copy, new_copy], PREFERENCES, set())
  assert decisions['602'].action == 'keep'
  assert decisions['601'].action == 'shard'


def test_exotic_class_items_keep_one_copy_per_perk_pair():
  stag_swarm = ('Spirit of the Stag', 'Spirit of the Swarm')
  stag_star = ('Spirit of the Stag', 'Spirit of the Star')
  pieces = [
    make_piece('701', {'Health': 30}, exotic=True, name='Solipsism', perks=stag_swarm),
    make_piece(
      '702', {'Health': 30}, exotic=True, name='Solipsism', perks=stag_swarm, tier=0
    ),
    make_piece('703', {'Health': 30}, exotic=True, name='Solipsism', perks=stag_star),
  ]
  decisions = decide_armor(pieces, PREFERENCES, set())
  assert [decisions[instance_id].action for instance_id in ('701', '702', '703')] == [
    'keep',
    'shard',
    'keep',
  ]


def test_exotic_override_keeps_the_best_copy_of_each_listed_archetype():
  preferences = Preferences(
    classes=PREFERENCES.classes,
    armor=ArmorPreferences(
      archetype_order=ARMOR_PREFERENCES.archetype_order,
      archetype_override_min_tier=3,
      stat_priority=ARMOR_PREFERENCES.stat_priority,
      exotic_overrides={
        'Exotic Helm': ExoticOverride(keep_per_archetype=('Powerhouse', 'Gunner'))
      },
    ),
  )
  pieces = [
    make_piece(
      '801', {'Weapons': 30}, exotic=True, name='Exotic Helm', archetype='Powerhouse'
    ),
    make_piece(
      '802', {'Weapons': 30}, exotic=True, name='Exotic Helm', archetype='Gunner'
    ),
    make_piece(
      '803', {'Health': 30}, exotic=True, name='Exotic Helm', archetype='Bulwark'
    ),
  ]
  decisions = decide_armor(pieces, preferences, set())
  assert decisions['801'].action == 'keep'
  assert decisions['802'].action == 'keep'
  assert decisions['803'].action == 'shard'


def test_classes_missing_from_config_go_to_review():
  preferences = Preferences(classes=('warlock',), armor=ARMOR_PREFERENCES)
  titan_piece = make_piece('901', {'Health': 30}, class_name='Titan')
  decisions = decide_armor([titan_piece], preferences, set())
  assert decisions['901'].action == 'review'


def test_white_green_and_blue_armor_never_take_a_spot():
  # A starter helmet with the highest Weapons stat still loses to legendaries.
  starter = make_piece(
    '951', {'Weapons': 40}, tier=0, rarity='white', name='Brave Titan Helm'
  )
  blue = make_piece('952', {'Health': 40}, tier=0, rarity='blue')
  legendary = make_piece('953', {'Weapons': 10, 'Health': 10}, tier=0)
  decisions = decide_armor([starter, blue, legendary], PREFERENCES, set())
  assert decisions['951'].action == 'shard'
  assert decisions['951'].reasons == [
    'white armor: only legendary and exotic armor competes for a spot'
  ]
  assert decisions['952'].action == 'shard'
  assert decisions['953'].action == 'keep'
  assert 'best Weapons Helmet' in reasons(decisions, '953')
