'''Tests for the tie step: trait pairs, picks, and the loop back to the rounds.'''

from __future__ import annotations

import pytest

from src.session import finish_session, run_round, start_session
from src.state import CurrentTie, StateFile
from src.ties import (
  bring_copies_in,
  copy_letters,
  format_pick_outcome,
  format_tie_step,
  options_in_saved_order,
  pairs_for_pick,
  parse_pick,
  rolls_for_pairs,
  trait_pair_options,
)
from src.voltron import VoltronEntry
from tests.account_fixtures import (
  ENERGY_GUN,
  HUNTER,
  KINETIC_GUN,
  NOW,
  WARLOCK,
  FakeBungie,
  FakeItem,
  make_plan,
  open_account,
)
from tests.weapon_fixtures import make_weapon

COLUMNS = ('barrels', 'magazines', 'frames', 'frames')
ENTRIES = [
  VoltronEntry(
    ('Arrowhead Brake', 'Accurized Rounds', 'Explosive Payload', 'Firefly'),
    COLUMNS,
  ),
  VoltronEntry(
    ('Smallbore', 'Accurized Rounds', 'Explosive Payload', 'Firefly'), COLUMNS
  ),
  VoltronEntry(
    ('Arrowhead Brake', 'Ricochet Rounds', 'Opening Shot', 'Kill Clip'), COLUMNS
  ),
  VoltronEntry(
    ('Arrowhead Brake', 'Accurized Rounds', 'Rapid Hit', 'Kill Clip'), COLUMNS
  ),
  VoltronEntry(('Smallbore', 'Accurized Rounds', 'Rapid Hit', 'Kill Clip'), COLUMNS),
]
TIE = {
  'weapon': 'Energy Gun',
  'group_name': 'Energy Gun',
  'shiny': False,
  'tied': ['71', '72', '73'],
  'copies': ['71', '72', '73'],
}


def test_your_pairs_come_first_then_the_most_suggested():
  copy = make_weapon(
    '1', [['Arrowhead Brake'], ['Accurized Rounds'], ['Opening Shot'], ['Kill Clip']]
  )
  options = trait_pair_options(ENTRIES, {'A': copy})
  assert [option.traits for option in options] == [
    ('Opening Shot', 'Kill Clip'),
    ('Explosive Payload', 'Firefly'),
    ('Rapid Hit', 'Kill Clip'),
  ]
  assert options[0].owners == ('A',)
  assert options[1].owners == ()
  assert options[1].suggestions == 2
  assert options[1].parts == (
    ('barrels', ('Arrowhead Brake', 'Smallbore')),
    ('magazines', ('Accurized Rounds',)),
  )


def test_a_waiting_tie_keeps_its_numbers_when_voltron_changes():
  copy = make_weapon('1', [['Opening Shot'], ['Kill Clip']])
  saved = [['Rapid Hit', 'Kill Clip'], ['Outlaw', 'Rampage']]
  # Voltron now lists a new pair first and has dropped Outlaw + Rampage.
  options = options_in_saved_order(saved, trait_pair_options(ENTRIES, {'A': copy}), {})
  assert [option.traits for option in options] == [
    ('Rapid Hit', 'Kill Clip'),
    ('Outlaw', 'Rampage'),
  ]
  assert options[0].suggestions == 2
  assert options[1].suggestions == 0


def test_the_tie_step_lists_numbered_pairs_with_your_copies_marked():
  copy = make_weapon(
    '71', [['Arrowhead Brake'], ['Accurized Rounds'], ['Opening Shot'], ['Kill Clip']]
  )
  options = trait_pair_options(ENTRIES, {'A': copy})
  text = format_tie_step(TIE, {'A': copy}, {'71': 'from the vault'}, options, 3)
  assert (
    'Tie step: Energy Gun (Hand Cannon). 2 more tied weapons after this one.' in text
  )
  assert 'Its copies, in your inventory in the Energy slot unless noted:' in text
  assert '   1. Opening Shot + Kill Clip   <- you have: A' in text
  assert '   2. Explosive Payload + Firefly' in text
  assert '        barrels: Arrowhead Brake, Smallbore' in text


def test_a_tie_without_trait_pairs_says_to_skip():
  copy = make_weapon('71', [['Arrowhead Brake']])
  text = format_tie_step(TIE, {'A': copy}, {}, [], 1)
  assert 'nothing to pick. Skip it for this session.' in text


@pytest.mark.parametrize(
  ('answer', 'numbers'),
  [('2, 18', [2, 18]), ('2 18', [2, 18]), (' 3,3 ', [3]), ('', None), ('skip', None)],
)
def test_picks_are_comma_separated_numbers(answer, numbers):
  assert parse_pick(answer, 20) == numbers


@pytest.mark.parametrize(
  ('answer', 'option_count', 'message'),
  [
    ('2, x', 20, "'x'"),
    ('21', 20, 'no pair 21'),
    (',', 20, 'at least one pair number'),
    ('1', 0, 'no pairs to pick'),
  ],
)
def test_bad_picks_say_what_is_wrong(answer, option_count, message):
  with pytest.raises(ValueError, match=message):
    parse_pick(answer, option_count)


def test_a_pick_becomes_rolls_labeled_by_the_pair():
  options = [['Opening Shot', 'Kill Clip'], ['Explosive Payload', 'Firefly']]
  pairs = pairs_for_pick(options, [2])
  rolls = rolls_for_pairs(pairs, '2026-09-27', '244213.26.06.29.2000-1-bnet.65864')
  assert [(roll.label, roll.perks) for roll in rolls] == [
    ('Explosive Payload + Firefly', ('Explosive Payload', 'Firefly'))
  ]
  assert (rolls[0].added, rolls[0].game_version) == (
    '2026-09-27',
    '244213.26.06.29.2000-1-bnet.65864',
  )
  traits = {
    'Explosive Payload': frozenset({'frames'}),
    'Firefly': frozenset({'frames'}),
  }
  (labeled,) = rolls_for_pairs(pairs, categories_by_perk=traits)
  assert labeled.columns == ('perk_1', 'perk_2')


def test_the_outcome_uses_the_letters_you_were_shown():
  # Copy 70 was dismantled in an earlier round, so the listing lettered only
  # 71 and 72.
  tie = CurrentTie(
    'Energy Gun',
    'Energy Gun',
    ['70', '71', '72'],
    letters=copy_letters(['71', '72']),
  )
  new_plan = {'items': {'71': {'action': 'shard'}, '72': {'action': 'keep'}}}
  assert (
    format_pick_outcome(tie, new_plan, {}) == 'Energy Gun: keeping B; A is now a shard.'
  )


def test_the_outcome_names_other_versions_the_pick_decides():
  tie = CurrentTie('Hammerhead', 'Hammerhead', ['71', '72'], letters={'71': 'A'})
  new_plan = {
    'items': {
      '71': {'action': 'keep'},
      '81': {
        'name': 'Hammerhead',
        'kind': 'weapon',
        'shiny': True,
        'tier': 4,
        'location': 'vault',
        'action': 'shard',
      },
    }
  }
  outcome = format_pick_outcome(tie, new_plan, {'71': 'keep', '81': 'keep'})
  assert outcome == (
    'Hammerhead: keeping A. The pick covers every version of Hammerhead, so it '
    'also decides: Hammerhead (shiny, T4, vault) is now a shard.'
  )


def test_copies_are_lettered_in_plan_order():
  assert copy_letters(['5', '9', '12']) == {'5': 'A', '9': 'B', '12': 'C'}


def test_the_whole_loop_brings_copies_in_and_sends_keepers_home(tmp_path):
  fake = FakeBungie(
    [
      FakeItem('71', ENERGY_GUN),
      FakeItem('72', ENERGY_GUN, 'inventory', HUNTER),
      FakeItem('73', ENERGY_GUN, 'inventory', WARLOCK, locked=True),
    ]
  )
  plan = make_plan(dict.fromkeys(TIE['copies'], 'keep'), ties=(TIE,))
  state_file = StateFile(tmp_path / 'state.json')
  start_session(open_account(fake), plan, state_file, NOW)
  assert fake.where('73') == ('vault', None)  # you were carrying it

  result = bring_copies_in(open_account(fake), plan, state_file, TIE)
  assert result.report.problems == []
  assert result.where == {
    '71': 'from the vault',
    '72': 'from your Hunter',
    '73': 'from the vault',
  }
  for copy_id in TIE['copies']:
    assert fake.where(copy_id) == ('inventory', WARLOCK)
  session = state_file.state.session
  assert session.tie_origins == {'71': 'vault', '72': HUNTER}  # 73 comes back anyway

  # You pick a pair copies B and C can roll, so A becomes a shard.
  session.current_tie.picked = [['Outlaw', 'Kill Clip']]
  picked_plan = make_plan(
    {'71': 'shard', '72': 'keep', '73': 'keep'}, name='20260927T201000Z-plan.yaml'
  )
  report = run_round(open_account(fake), picked_plan, state_file)
  assert report.new_plan == '20260927T201000Z-plan.yaml'
  assert report.unlocked == ['Energy Gun']
  assert fake.where('71') == ('inventory', WARLOCK)
  assert fake.where('72') == ('vault', None)
  assert fake.where('73') == ('vault', None)

  session.current_tie = None
  fake.dismantle('71')
  finish_session(open_account(fake), picked_plan, state_file)
  assert fake.where('72') == ('inventory', HUNTER)
  assert fake.where('73') == ('inventory', WARLOCK)


def test_copies_that_do_not_fit_stay_where_they_are(tmp_path):
  shard_ids = [str(100 + number) for number in range(8)]
  fake = FakeBungie(
    [
      *(FakeItem(shard_id, ENERGY_GUN) for shard_id in shard_ids),
      FakeItem('71', ENERGY_GUN),
      FakeItem('72', ENERGY_GUN),
    ]
  )
  tie = {**TIE, 'tied': ['71', '72'], 'copies': ['71', '72']}
  plan = make_plan(
    {**dict.fromkeys(shard_ids, 'shard'), '71': 'keep', '72': 'keep'}, ties=(tie,)
  )
  state_file = StateFile(tmp_path / 'state.json')
  start_session(open_account(fake), plan, state_file, NOW)
  result = bring_copies_in(open_account(fake), plan, state_file, tie)
  assert result.left_out == 1
  assert fake.where('71') == ('inventory', WARLOCK)
  assert fake.where('72') == ('vault', None)
  assert result.where['72'] == 'in the vault, no room to bring it in'
  assert state_file.state.session.current_tie.weapon == 'Energy Gun'


def test_room_is_counted_per_slot(tmp_path):
  # "True Prophecy" style: one weapon name in two slots.
  shard_ids = [str(100 + number) for number in range(9)]
  fake = FakeBungie(
    [
      *(FakeItem(shard_id, KINETIC_GUN) for shard_id in shard_ids),
      FakeItem('71', KINETIC_GUN),
      FakeItem('72', ENERGY_GUN),
    ]
  )
  tie = {**TIE, 'tied': ['71', '72'], 'copies': ['71', '72']}
  plan = make_plan(
    {**dict.fromkeys(shard_ids, 'shard'), '71': 'keep', '72': 'keep'}, ties=(tie,)
  )
  state_file = StateFile(tmp_path / 'state.json')
  start_session(open_account(fake), plan, state_file, NOW)
  result = bring_copies_in(open_account(fake), plan, state_file, tie)
  assert fake.where('71') == ('vault', None)  # the Kinetic slot is full of shards
  assert fake.where('72') == ('inventory', WARLOCK)
  assert result.left_out == 1
  assert result.report.problems == []
