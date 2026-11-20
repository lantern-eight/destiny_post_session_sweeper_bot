'''Tests for plan assembly: user locks, saving, and the kept-last-run history.'''

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.armor_rules import Decision
from src.plan import (
  PLANS_TO_KEEP,
  Definitions,
  PlanFileError,
  Wishlists,
  apply_user_locks,
  build_plan,
  format_plan_summary,
  latest_plan_path,
  load_previous_keepers,
  read_plan,
  save_plan,
)
from src.session import PlanView
from src.wishlist import WishlistEntry
from tests.fixtures import (
  ITEM_DEFINITIONS,
  PREFERENCES,
  SET_DEFINITIONS,
  make_piece,
  make_profile,
  plug,
)
from tests.weapon_fixtures import (
  PLUG_SET_DEFINITIONS,
  WEAPON_DEFINITIONS,
  make_weapon,
  make_weapon_profile,
  weapon_definition,
)

NOW = datetime(2026, 9, 27, 3, 0, tzinfo=UTC)
NO_WISHLISTS = Wishlists(personal={}, voltron={})


WEAPON_DEFINITIONS_ONLY = Definitions(
  items=WEAPON_DEFINITIONS, sets={}, plug_sets=PLUG_SET_DEFINITIONS
)


def plan_for(
  profile: dict,
  definitions: Definitions,
  wishlists: Wishlists = NO_WISHLISTS,
  previous_keepers: frozenset[str] = frozenset(),
  tool_locked_ids: frozenset[str] = frozenset(),
) -> dict:
  return build_plan(
    profile,
    definitions,
    PREFERENCES,
    wishlists,
    previous_keepers=set(previous_keepers),
    tool_locked_ids=set(tool_locked_ids),
    now=NOW,
  )


def test_user_locked_shard_is_kept_and_listed_as_outdated():
  locked_piece = make_piece('11', {'Melee': 10}, tier=0, locked=True)
  decisions = {'11': Decision('shard', ['legacy piece, not the best'])}
  outdated = apply_user_locks([locked_piece], decisions, tool_locked_ids=set())
  assert decisions['11'].action == 'keep'
  assert outdated == [
    {
      'id': '11',
      'name': 'Helm',
      'class': 'Warlock',
      'slot': 'Helmet',
      'rules_say': 'legacy piece, not the best',
    }
  ]


def test_lock_placed_by_the_tool_does_not_protect_a_shard():
  locked_piece = make_piece('11', {'Melee': 10}, tier=0, locked=True)
  decisions = {'11': Decision('shard', ['legacy piece, not the best'])}
  outdated = apply_user_locks([locked_piece], decisions, tool_locked_ids={'11'})
  assert decisions['11'].action == 'shard'
  assert outdated == []


def test_locked_weapon_shard_is_listed_without_a_class():
  locked_weapon = make_weapon('31', [['Firefly']], locked=True)
  decisions = {'31': Decision('shard', ['another Fatebringer copy is kept'])}
  outdated = apply_user_locks([locked_weapon], decisions, tool_locked_ids=set())
  assert decisions['31'].action == 'keep'
  assert outdated == [
    {
      'id': '31',
      'name': 'Fatebringer',
      'slot': 'Energy',
      'rules_say': 'another Fatebringer copy is kept',
    }
  ]


def test_plan_describes_every_piece_and_feeds_the_next_run(tmp_path):
  plan = plan_for(
    make_profile(),
    Definitions(items=ITEM_DEFINITIONS, sets=SET_DEFINITIONS, plug_sets={}),
  )
  assert plan['account'] == 'Guardian#0007'
  assert plan['current_character'] == 'Warlock'
  assert set(plan['items']) == {'11', '12', '13'}
  helmet = plan['items']['11']
  assert helmet['kind'] == 'armor'
  assert helmet['action'] == 'keep'
  assert helmet['set'] == 'Techsec'
  assert helmet['tuned'] == 'Weapons'
  assert plan['summary']['armor']['items'] == 3
  assert plan['summary']['weapons']['items'] == 0

  plan_path = save_plan(plan, tmp_path)
  assert plan_path.name == '20260927T030000Z-plan.yaml'
  kept_ids = {
    instance_id
    for instance_id, entry in plan['items'].items()
    if entry['action'] == 'keep'
  }
  assert load_previous_keepers(tmp_path) == kept_ids


def test_plan_lists_weapons_with_their_perk_options():
  plan = plan_for(make_weapon_profile(), WEAPON_DEFINITIONS_ONLY)
  random_roll, crafted = plan['items']['21'], plan['items']['22']
  assert random_roll['kind'] == 'weapon'
  assert random_roll['perks'] == [
    'Arrowhead Brake | Smallbore',
    'Accurized Rounds',
    'Explosive Payload | Golden Tricorn',
    'Firefly',
  ]
  # The crafted copy can reach more perks, so it wins the only spot.
  assert crafted['crafted'] is True
  assert (crafted['action'], random_roll['action']) == ('keep', 'shard')
  assert random_roll['compare_with'] == ['22']
  assert PlanView.from_plan(Path('plan.yaml'), plan).compare_with == {'21': ('22',)}
  assert plan['summary']['weapons'] == {'items': 2, 'keep': 1, 'shard': 1, 'review': 0}


def test_no_previous_plan_means_no_previous_keepers(tmp_path):
  assert load_previous_keepers(tmp_path / 'missing') == set()


def test_misspelled_wishlist_names_are_flagged_with_a_suggestion():
  roll = WishlistEntry('mine', ('Explosive Payload', 'Fireflyy'))
  wishlists = Wishlists(
    personal={'fatebringer': [roll], 'Fatebringr': [roll]}, voltron={}
  )
  warnings = plan_for(make_weapon_profile(), WEAPON_DEFINITIONS_ONLY, wishlists)[
    'warnings'
  ]
  assert (
    "wishlist.yaml: 'fatebringer' is not a weapon name (did you mean Fatebringer?), "
    'so its rolls are ignored'
  ) in warnings
  assert (
    "wishlist.yaml: 'Fatebringr' is not a weapon name (did you mean Fatebringer?), "
    'so its rolls are ignored'
  ) in warnings
  assert (
    "wishlist.yaml: 'Fireflyy' in fatebringer 'mine' is not a perk name "
    '(did you mean Firefly?)'
  ) in warnings


def test_former_keepers_that_are_shards_now_are_listed():
  profile = make_weapon_profile()
  profile['profileInventory']['data']['items'][0]['state'] = 1  # the tool locked 21
  plan = plan_for(
    profile,
    WEAPON_DEFINITIONS_ONLY,
    previous_keepers=frozenset({'21', '22'}),
    tool_locked_ids=frozenset({'21'}),
  )
  assert [entry['id'] for entry in plan['newly_sharded']] == ['21']
  assert plan['newly_sharded'][0]['tool_locked'] is True
  assert plan['summary']['newly_sharded'] == 1
  summary = format_plan_summary(plan, 'plan.yaml')
  assert (
    '1 item the last plan kept is a shard now (see newly_sharded in the plan); '
    'apply unlocks the 1 the tool had locked'
  ) in summary


def test_a_rolls_origin_trait_is_secondary_like_its_barrel_and_magazine():
  definitions = Definitions(
    items={**WEAPON_DEFINITIONS, '3110': plug("Forge's Kin", 'origins')},
    sets={},
    plug_sets=PLUG_SET_DEFINITIONS,
  )
  roll = WishlistEntry(
    'mine', ('Explosive Payload', 'Firefly', "Forge's Kin", 'Ricochet Rounds')
  )
  wishlists = Wishlists(personal={'Fatebringer': [roll]}, voltron={})
  items = plan_for(make_weapon_profile(), definitions, wishlists)['items']
  # Neither copy can have Forge's Kin; only the crafted one has Ricochet Rounds.
  assert items['22']['reasons'] == [
    "matches your 'mine' roll with 1 of 2 secondary perks"
  ]
  assert items['21']['action'] == 'shard'
  assert 'your mine roll with 0 of 2 secondary perks' in items['21']['reasons'][0]


def test_columns_and_masterworks_are_checked_against_the_game():
  definitions = Definitions(
    items={
      **WEAPON_DEFINITIONS,
      '3111': plug(
        'Tier 3: Stability', 'v400.plugs.weapons.masterworks.stat.stability'
      ),
    },
    sets={},
    plug_sets=PLUG_SET_DEFINITIONS,
  )
  roll = WishlistEntry(
    'mine',
    ('Arrowhead Brake', 'Firefly', 'Accurized Rounds'),
    columns=('perk_1', 'perk_2', 'magazin'),
    masterwork=('Stabilty',),
  )
  wishlists = Wishlists(personal={'Fatebringer': [roll]}, voltron={})
  profile = make_weapon_profile()
  sockets = profile['itemComponents']['sockets']['data']['21']['sockets']
  profile['itemComponents']['sockets']['data']['21'] = {
    'sockets': [*sockets, {'plugHash': 3111}]
  }
  plan = plan_for(profile, definitions, wishlists)
  assert plan['warnings'] == [
    "wishlist.yaml: 'Arrowhead Brake' in Fatebringer 'mine' is under perk_1, but it "
    'is a barrel',
    "wishlist.yaml: 'magazin' in Fatebringer 'mine' is not a column name (did you "
    'mean magazine?)',
    "wishlist.yaml: 'Stabilty' in Fatebringer 'mine' is not a masterwork stat (did "
    'you mean Stability?)',
  ]
  # The plan shows the masterwork each copy rolled.
  assert plan['items']['21']['masterwork'] == 'Stability'


def test_each_choice_in_a_roll_is_checked_on_its_own():
  roll = WishlistEntry('mine', ('Explosive Payload', 'Firefly | Fireflyy'))
  wishlists = Wishlists(personal={'Fatebringer': [roll]}, voltron={})
  warnings = plan_for(make_weapon_profile(), WEAPON_DEFINITIONS_ONLY, wishlists)[
    'warnings'
  ]
  assert warnings == [
    "wishlist.yaml: 'Fireflyy' in Fatebringer 'mine' is not a perk name "
    '(did you mean Firefly?)'
  ]


def test_items_kept_only_for_your_lock_are_not_kept_last_run(tmp_path):
  plan = {
    'generated': NOW.isoformat(),
    'locked_but_outdated': [{'id': '12'}],
    'items': {
      '11': {'action': 'keep'},
      '12': {'action': 'keep'},
      '13': {'action': 'shard'},
    },
  }
  save_plan(plan, tmp_path)
  assert load_previous_keepers(tmp_path) == {'11'}


def test_an_empty_plan_file_is_reported_instead_of_crashing(tmp_path):
  (tmp_path / '20260927T030000Z-plan.yaml').write_text('')
  assert load_previous_keepers(tmp_path) == set()
  with pytest.raises(PlanFileError, match='empty or cut short'):
    read_plan(latest_plan_path(tmp_path))


def test_only_the_newest_plans_are_kept(tmp_path):
  for minute in range(PLANS_TO_KEEP + 3):
    generated = datetime(2026, 9, 27, 3, minute, tzinfo=UTC)
    save_plan({'generated': generated.isoformat(), 'items': {}}, tmp_path)
  names = sorted(path.name for path in tmp_path.iterdir())
  assert len(names) == PLANS_TO_KEEP
  assert names[0] == '20260927T030300Z-plan.yaml'


def test_copies_kept_for_a_crafting_pattern_are_listed():
  definitions = Definitions(
    items={
      **WEAPON_DEFINITIONS,
      '3000': weapon_definition('Fatebringer', craftable=True),
    },
    sets={},
    plug_sets=PLUG_SET_DEFINITIONS,
  )
  profile = make_weapon_profile()
  profile['profileInventory']['data']['items'][1]['state'] = 0  # not crafted
  plan = plan_for(profile, definitions)
  assert (plan['items']['21']['action'], plan['items']['22']['action']) == (
    'keep',
    'keep',
  )
  assert plan['items']['21']['craftable'] is True
  assert plan['crafting'] == [{'weapon': 'Fatebringer', 'copies': 2}]
  assert (
    'Extra copies kept toward a crafting pattern, for Deepsight Harmonizers:\n'
    '  Fatebringer   2 copies\n'
  ) in format_plan_summary(plan, 'plan.yaml')


def test_an_armor_shard_lists_the_kept_piece_of_the_same_name():
  profile = make_profile()
  # A second Techsec Hood with the same roll, but not masterworked: it loses.
  profile['profileInventory']['data']['items'].append(
    {
      'itemHash': 1000,
      'itemInstanceId': '14',
      'bucketHash': profile['profileInventory']['data']['items'][0]['bucketHash'],
      'state': 0,
    }
  )
  components = profile['itemComponents']
  components['instances']['data']['14'] = {'gearTier': 5}
  components['sockets']['data']['14'] = components['sockets']['data']['11']
  components['reusablePlugs']['data']['14'] = components['reusablePlugs']['data']['11']
  definitions = Definitions(
    items=ITEM_DEFINITIONS, sets=SET_DEFINITIONS, plug_sets=PLUG_SET_DEFINITIONS
  )
  plan = plan_for(profile, definitions)
  assert plan['items']['14']['action'] == 'shard'
  assert plan['items']['14']['compare_with'] == ['11']


def test_tied_weapons_are_listed_one_to_a_line():
  plan = plan_for(make_weapon_profile(), WEAPON_DEFINITIONS_ONLY)
  plan['ties'] = [{'weapon': 'Fatebringer'}, {'weapon': 'Hammerhead'}]
  summary = format_plan_summary(plan, 'plan.yaml')
  assert (
    '\n\nTied rolls, every copy kept until you pick with `uv run sweeper ties`:\n'
    '  Fatebringer\n'
    '  Hammerhead\n\n'
  ) in summary
