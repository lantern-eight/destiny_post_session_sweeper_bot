'''Tests for reading in-game loadouts and calling out the shards they use.'''

from __future__ import annotations

from datetime import UTC, datetime

from src.game_data import UNSET_HASH
from src.loadouts import Loadout, LoadoutGear, read_loadouts
from src.plan import Definitions, Wishlists, build_plan, format_plan_summary
from tests.fixtures import PREFERENCES, plug
from tests.weapon_fixtures import (
  PLUG_SET_DEFINITIONS,
  WEAPON_DEFINITIONS,
  make_weapon_profile,
)

WARLOCK = '2305843009000000001'
MOD_DEFINITIONS = {
  '4000': plug('Backup Mag', 'v400.weapon.mod_magazine'),
  '4001': plug('Empty Mod Socket', 'v400.weapon.mod_empty'),
  '4002': plug('Harmonic Loader', 'enhancements.v2_arms'),
  '4003': plug('Default Shader', 'shader'),
}
LOADOUT_NAMES = {'555': {'name': 'PvE'}}


def with_loadouts(profile: dict) -> dict:
  '''The weapon fixture profile, plus a Warlock with two saved loadouts.'''
  profile['characters'] = {'data': {WARLOCK: {'classType': 2}}}
  unused_slot = {
    'nameHash': UNSET_HASH,
    'items': [{'itemInstanceId': '0', 'plugItemHashes': [UNSET_HASH]}],
  }
  profile['characterLoadouts'] = {
    'data': {
      WARLOCK: {
        'loadouts': [
          unused_slot,
          {
            'nameHash': 555,
            'items': [
              {'itemInstanceId': '21', 'plugItemHashes': [4003, 4000, 4001, 4000]},
              {'itemInstanceId': '22', 'plugItemHashes': [UNSET_HASH]},
              {'itemInstanceId': '99', 'plugItemHashes': [4002]},  # a subclass
            ],
          },
        ]
      }
    }
  }
  return profile


def test_loadouts_list_their_gear_and_mods():
  loadouts = read_loadouts(
    with_loadouts(make_weapon_profile()),
    {**WEAPON_DEFINITIONS, **MOD_DEFINITIONS},
    LOADOUT_NAMES,
  )
  assert loadouts == [
    Loadout(
      'Warlock',
      2,
      'PvE',
      (
        LoadoutGear('21', ('Backup Mag', 'Backup Mag')),
        LoadoutGear('22', ()),
        LoadoutGear('99', ('Harmonic Loader',)),
      ),
    )
  ]
  assert loadouts[0].label == 'Warlock loadout 2 (PvE)'


def test_the_plan_calls_out_loadouts_that_lose_a_shard():
  definitions = Definitions(
    items={**WEAPON_DEFINITIONS, **MOD_DEFINITIONS},
    sets={},
    plug_sets=PLUG_SET_DEFINITIONS,
    loadout_names=LOADOUT_NAMES,
  )
  plan = build_plan(
    with_loadouts(make_weapon_profile()),
    definitions,
    PREFERENCES,
    Wishlists(personal={}, voltron={}),
    previous_keepers=set(),
    tool_locked_ids=set(),
    now=datetime(2026, 9, 27, 3, 0, tzinfo=UTC),
  )
  # The crafted copy (22) is kept, so the random roll (21) is a shard. Keepers
  # list their loadouts too: removing your lock can make one a shard mid-run.
  assert plan['items']['21']['loadouts'] == ['Warlock loadout 2 (PvE)']
  assert plan['items']['22']['loadouts'] == ['Warlock loadout 2 (PvE)']
  assert plan['loadouts_affected'] == [
    {
      'loadout': 'Warlock loadout 2 (PvE)',
      'shards': ['Fatebringer (Adept)'],
      'gear': [
        {
          'name': 'Fatebringer (Adept)',
          'slot': 'Energy',
          'mods': ['Backup Mag', 'Backup Mag'],
          'shard': True,
        },
        {'name': 'Fatebringer (Adept)', 'slot': 'Energy', 'mods': []},
      ],
    }
  ]
  assert plan['summary']['loadouts_affected'] == 1
  summary = format_plan_summary(plan, 'plan.yaml')
  assert (
    'In-game loadouts that use a shard (to rebuild them, see loadouts_affected):\n'
    '  Warlock loadout 2 (PvE)   Fatebringer (Adept)\n'
  ) in summary
