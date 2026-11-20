'''Tests for turning a raw profile into armor pieces.'''

from __future__ import annotations

from src.inventory import build_armor_pieces
from tests.fixtures import ITEM_DEFINITIONS, SET_DEFINITIONS, make_profile


def pieces_by_id():
  pieces = build_armor_pieces(make_profile(), ITEM_DEFINITIONS, SET_DEFINITIONS)
  return {piece.instance_id: piece for piece in pieces}


def test_new_system_piece_reads_archetype_stats_tuning_and_set():
  helmet = pieces_by_id()['11']
  assert helmet.name == 'Techsec Hood'
  assert helmet.class_name == 'Warlock'
  assert helmet.slot == 'Helmet'
  assert helmet.tier == 5
  assert helmet.archetype == 'Powerhouse'
  # All six, in the game's order, whatever the archetype.
  assert list(helmet.display_stats.items()) == [
    ('Health', 20),
    ('Melee', 0),
    ('Grenade', 0),
    ('Super', 25),
    ('Class', 0),
    ('Weapons', 30),
  ]
  assert helmet.tuned_stat == 'Weapons'
  assert helmet.reachable_value('Weapons') == 35
  assert helmet.set_name == 'Techsec'
  assert helmet.is_masterworked
  assert not helmet.is_locked
  assert helmet.location == 'vault'


def test_raid_armor_is_named_after_its_raid_mod_socket():
  pieces = pieces_by_id()
  assert pieces['12'].raid == 'Vault of Glass'
  assert pieces['11'].raid is None


def test_named_armor_perks_are_read_and_stat_plugs_are_not():
  pieces = pieces_by_id()
  # The legacy helmet's stat plugs share the perk category but have no name.
  assert pieces['12'].perks == ("Iron Lord's Pride",)
  assert pieces['11'].perks == ()


def test_base_stats_ignore_mods_masterwork_and_tuning():
  helmet = pieces_by_id()['11']
  # The +10 Weapons mod, the masterwork's Melee bonus, and Balanced Tuning are
  # not part of the roll.
  assert helmet.base_stats['Weapons'] == 30
  assert helmet.base_stats['Melee'] == 0
  assert helmet.base_total == 75


def test_legacy_piece_sums_its_stat_plugs_and_has_no_tier_or_set():
  legacy_helmet = pieces_by_id()['12']
  assert legacy_helmet.class_name == 'Titan'
  assert legacy_helmet.is_legacy
  assert legacy_helmet.archetype is None
  assert legacy_helmet.set_name is None
  assert legacy_helmet.base_total == 30
  assert legacy_helmet.ordered_stats == ('Weapons', 'Grenade', 'Melee')
  assert legacy_helmet.is_locked
  assert legacy_helmet.location == 'postmaster'


def test_exotic_class_item_reads_perk_pair_and_has_no_single_tuned_stat():
  class_item = pieces_by_id()['13']
  assert class_item.is_exotic
  assert class_item.exotic_perks == ('Spirit of the Stag', 'Spirit of the Swarm')
  assert class_item.archetype == 'Bulwark'
  assert class_item.display_stats == {
    'Health': 30,
    'Melee': 0,
    'Grenade': 20,
    'Super': 0,
    'Class': 25,
    'Weapons': 0,
  }
  assert class_item.tuned_stat is None
