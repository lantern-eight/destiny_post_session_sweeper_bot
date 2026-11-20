'''Tests for turning a raw profile into weapons and matching perk rolls.'''

from __future__ import annotations

from src.game_data import (
  ENHANCER_PLUG_CATEGORY,
  INTRINSIC_TRAITS_SOCKET_CATEGORY_HASH,
  VAULT_BUCKET_HASH,
  WEAPON_PERKS_SOCKET_CATEGORY_HASH,
)
from src.weapons import build_weapons, normalize_weapon_name
from tests.fixtures import plug
from tests.weapon_fixtures import (
  ENERGY_BUCKET_HASH,
  PLUG_SET_DEFINITIONS,
  WEAPON_DEFINITIONS,
  make_weapon_profile,
  weapon_definition,
)


def weapons_by_id():
  weapons = build_weapons(
    make_weapon_profile(), WEAPON_DEFINITIONS, PLUG_SET_DEFINITIONS
  )
  return {weapon.instance_id: weapon for weapon in weapons}


def test_random_roll_lists_every_option_per_column_without_the_tracker():
  weapon = weapons_by_id()['21']
  assert weapon.perk_columns == (
    ('Arrowhead Brake', 'Smallbore'),
    ('Accurized Rounds',),
    ('Explosive Payload', 'Golden Tricorn'),
    ('Firefly',),
  )
  assert weapon.selected_perks == (
    'Arrowhead Brake',
    'Accurized Rounds',
    'Explosive Payload',
    'Firefly',
  )


def test_weapon_reads_name_group_slot_tier_and_flags():
  weapon = weapons_by_id()['21']
  assert weapon.name == 'Fatebringer (Adept)'
  assert weapon.group_name == 'Fatebringer'
  assert weapon.slot == 'Energy'
  assert weapon.rarity == 'legendary'
  assert weapon.tier == 5
  assert weapon.is_adept
  assert not weapon.is_shiny
  assert not weapon.is_crafted


def test_a_version_with_a_crafting_pattern_is_craftable():
  assert not any(weapon.is_craftable for weapon in weapons_by_id().values())
  definitions = {
    **WEAPON_DEFINITIONS,
    '3000': weapon_definition('Fatebringer', craftable=True),
  }
  weapons = build_weapons(make_weapon_profile(), definitions, PLUG_SET_DEFINITIONS)
  assert all(weapon.is_craftable for weapon in weapons)


def test_ergo_sum_reads_its_random_frame_and_exotic_trait():
  definitions = {
    '4100': {
      'itemType': 3,
      'displayProperties': {'name': 'Ergo Sum'},
      'inventory': {'bucketTypeHash': ENERGY_BUCKET_HASH, 'tierType': 6},
      'sockets': {
        'socketCategories': [
          {
            'socketCategoryHash': INTRINSIC_TRAITS_SOCKET_CATEGORY_HASH,
            'socketIndexes': [0],
          },
          {
            'socketCategoryHash': WEAPON_PERKS_SOCKET_CATEGORY_HASH,
            'socketIndexes': [1, 2, 3],
          },
        ],
        'socketEntries': [
          {'randomizedPlugSetHash': 1},  # the exotic trait
          {'reusablePlugSetHash': 2},  # the same on every copy
          {'randomizedPlugSetHash': 3},  # the blade
          {'randomizedPlugSetHash': 4},  # the frame
        ],
      },
    },
    '4000': plug('Arc Conductor', 'intrinsics'),
    '4001': plug('Transcendent Duelist', 'frames'),
    '4002': plug('Hungry Edge', 'blades'),
    '4003': plug('Caster Frame', 'frames'),
  }
  profile = {
    'profileInventory': {
      'data': {
        'items': [
          {
            'itemHash': 4100,
            'itemInstanceId': '91',
            'bucketHash': VAULT_BUCKET_HASH,
            'state': 0,
          }
        ]
      }
    },
    'characterInventories': {'data': {}},
    'characterEquipment': {'data': {}},
    'itemComponents': {
      'sockets': {
        'data': {
          '91': {
            'sockets': [
              {'plugHash': 4000},
              {'plugHash': 4001},
              {'plugHash': 4002},
              {'plugHash': 4003},
            ]
          }
        }
      }
    },
  }
  (ergo_sum,) = build_weapons(profile, definitions, {})
  assert ergo_sum.exotic_pair == ('Caster Frame', 'Arc Conductor')


def test_crafted_copy_can_reach_its_whole_perk_pool():
  crafted = weapons_by_id()['22']
  assert crafted.is_crafted
  assert crafted.perk_columns[1] == ('Accurized Rounds', 'Ricochet Rounds')
  assert crafted.perk_columns[3] == ('Firefly', 'Opening Shot')
  assert crafted.can_roll(['Golden Tricorn', 'Opening Shot'])


def test_an_enhanced_copy_is_not_crafted_and_keeps_its_own_options():
  # Bungie flags an enhanced weapon as crafted too. Its enhancement plug tells
  # them apart: it can't be reshaped, so it has only the options it rolled.
  definitions = {
    **WEAPON_DEFINITIONS,
    '3110': plug('Enhancement 1', ENHANCER_PLUG_CATEGORY),
  }
  profile = make_weapon_profile()
  components = profile['itemComponents']
  sockets = components['sockets']['data']['22']['sockets']
  components['sockets']['data']['22'] = {'sockets': [*sockets, {'plugHash': 3110}]}
  components['reusablePlugs']['data']['22'] = {
    'plugs': {'4': [{'plugItemHash': 3105}, {'plugItemHash': 3107}]}
  }
  weapons = build_weapons(profile, definitions, PLUG_SET_DEFINITIONS)
  enhanced = {weapon.instance_id: weapon for weapon in weapons}['22']
  assert enhanced.is_enhanced
  assert not enhanced.is_crafted
  assert enhanced.perk_columns == (
    ('Arrowhead Brake',),
    ('Accurized Rounds',),
    ('Explosive Payload',),
    ('Firefly', 'Opening Shot'),
  )
  assert not enhanced.can_roll(['Golden Tricorn', 'Opening Shot'])


def test_an_empty_enhancement_socket_is_not_an_enhancement():
  definitions = {
    **WEAPON_DEFINITIONS,
    '3110': plug('Empty Enhancement Socket', ENHANCER_PLUG_CATEGORY),
  }
  profile = make_weapon_profile()
  for instance_id in ('21', '22'):
    sockets = profile['itemComponents']['sockets']['data'][instance_id]['sockets']
    profile['itemComponents']['sockets']['data'][instance_id] = {
      'sockets': [*sockets, {'plugHash': 3110}]
    }
  weapons = {
    weapon.instance_id: weapon
    for weapon in build_weapons(profile, definitions, PLUG_SET_DEFINITIONS)
  }
  assert not weapons['21'].is_enhanced
  assert weapons['22'].is_crafted
  assert not weapons['22'].is_enhanced


def test_a_random_roll_keeps_its_masterwork_and_a_crafted_copy_takes_any():
  stat = 'v400.plugs.weapons.masterworks.stat'
  definitions = {
    **WEAPON_DEFINITIONS,
    '3111': plug('Tier 3: Stability', f'{stat}.stability'),
    '3112': plug('Tier 4: Stability', f'{stat}.stability'),
    '3113': plug('', 'v400.plugs.weapons.masterworks'),
    '3114': plug('Tier 1: Range', f'{stat}.range'),
    '3115': plug('Tier 1: Handling', f'{stat}.handling'),
  }
  profile = make_weapon_profile()
  components = profile['itemComponents']
  for instance_id, masterwork, options in (
    ('21', 3111, [3112]),
    ('22', 3113, [3114, 3115]),
  ):
    sockets = components['sockets']['data'][instance_id]['sockets']
    components['sockets']['data'][instance_id] = {
      'sockets': [*sockets, {'plugHash': masterwork}]
    }
    plugs = components['reusablePlugs']['data'].setdefault(instance_id, {'plugs': {}})
    plugs['plugs']['6'] = [{'plugItemHash': option} for option in options]
  weapons = {
    weapon.instance_id: weapon
    for weapon in build_weapons(profile, definitions, PLUG_SET_DEFINITIONS)
  }
  # The kill tracker shares the masterwork's plug category prefix; it is skipped.
  assert (weapons['21'].masterwork, weapons['21'].masterwork_options) == (
    'Stability',
    ('Stability',),
  )
  assert weapons['21'].can_have_masterwork(['stability'])
  assert not weapons['21'].can_have_masterwork(['Range', 'Handling'])
  assert (weapons['22'].masterwork, weapons['22'].masterwork_options) == (
    '',
    ('Range', 'Handling'),
  )
  assert weapons['22'].can_have_masterwork(['Handling'])


def test_two_perks_from_the_same_column_cannot_be_active_together():
  weapon = weapons_by_id()['21']
  assert weapon.can_roll(['Explosive Payload', 'Firefly'])
  assert not weapon.can_roll(['Explosive Payload', 'Golden Tricorn'])
  assert not weapon.can_roll(['Explosive Payload', 'Opening Shot'])
  assert (
    weapon.matchable_perk_count(['Explosive Payload', 'Golden Tricorn', 'Smallbore'])
    == 2
  )


def test_a_spot_with_choices_matches_any_one_of_them():
  weapon = weapons_by_id()['21']
  assert weapon.can_roll(['Explosive Payload', 'Kill Clip | Firefly'])
  assert weapon.can_roll(['Opening Shot | Explosive Payload', 'Firefly'])
  # Both choices sit in Explosive Payload's column, so neither fits beside it.
  assert not weapon.can_roll(['Explosive Payload', 'Opening Shot | Golden Tricorn'])


def test_variant_suffixes_fold_but_other_parentheses_stay():
  assert normalize_weapon_name('Fatebringer (Timelost)') == 'Fatebringer'
  assert normalize_weapon_name('Cataphract GL3 (Adept)') == 'Cataphract GL3'
  assert normalize_weapon_name("Traveler's Chosen (Damaged)") == (
    "Traveler's Chosen (Damaged)"
  )
