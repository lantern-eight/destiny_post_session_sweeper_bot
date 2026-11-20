'''Tests for the ties command's list, pick, and skip paths through the CLI.'''

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from src import main
from src.session import start_session
from src.state import StateFile
from src.voltron import VoltronEntry
from tests.account_fixtures import (
  ENERGY_GUN,
  HUNTER,
  NOW,
  WARLOCK,
  FakeBungie,
  FakeItem,
  make_plan,
  open_account,
)
from tests.main_fixtures import FakeConnection, FakeGameData

TIE = {
  'weapon': 'Energy Gun',
  'group_name': 'Energy Gun',
  'shiny': False,
  'tied': ['71', '72'],
  'copies': ['71', '72'],
}
CATEGORIES = ('frames', 'frames')
VOLTRON = {
  'Energy Gun': [
    VoltronEntry(('Outlaw', 'Kill Clip'), CATEGORIES),
    VoltronEntry(('Firefly', 'Rampage'), CATEGORIES),
  ]
}


@dataclass
class World:
  fake: FakeBungie
  connection: FakeConnection
  game_data: FakeGameData
  state_path: Path
  plans: list
  saved_rolls: list

  def state(self):
    return StateFile(self.state_path).state


@pytest.fixture
def world(tmp_path, monkeypatch):
  '''A session in progress on the Warlock with one tied weapon.'''
  fake = FakeBungie(
    [FakeItem('71', ENERGY_GUN), FakeItem('72', ENERGY_GUN, 'inventory', HUNTER)]
  )
  plans = [make_plan({'71': 'keep', '72': 'keep'}, ties=(TIE,))]
  state_path = tmp_path / 'state.json'
  start_session(open_account(fake), plans[0], StateFile(state_path), NOW)
  fake.calls.clear()
  saved_rolls = []
  connection = FakeConnection(fake)
  game_data = FakeGameData(voltron=VOLTRON)

  def build_new_plan(profile, game_data, tool_locked_ids, wishlist, snapshot_name=None):
    # The pick keeps copy B (72); copy A (71) no longer makes the cut.
    return {
      'generated': NOW.isoformat(),
      'items': {'71': {'action': 'shard'}, '72': {'action': 'keep'}},
    }

  def save_new_plan(plan, runs_directory=None):
    plans.append(make_plan({'71': 'shard', '72': 'keep'}, name='later-plan.yaml'))
    return Path('later-plan.yaml')

  monkeypatch.setattr(
    main, '_connect_for_actions', lambda status: (connection, game_data)
  )
  monkeypatch.setattr(
    main.PlanView,
    'load_latest',
    classmethod(lambda cls, runs_directory=None: plans[-1]),
  )
  monkeypatch.setattr(
    main, 'StateFile', lambda dry_run=False: StateFile(state_path, dry_run)
  )
  monkeypatch.setattr(main, '_build_plan', build_new_plan)
  monkeypatch.setattr(main, 'save_plan', save_new_plan)
  monkeypatch.setattr(main, 'load_wishlist', dict)
  monkeypatch.setattr(
    main, 'add_rolls', lambda weapon, rolls: saved_rolls.extend(rolls) or rolls
  )
  monkeypatch.setattr('sys.stdin.isatty', lambda: False)
  return World(fake, connection, game_data, state_path, plans, saved_rolls)


def test_ties_lists_the_pairs_and_waits_for_an_answer(world, capsys):
  assert main.main(['ties']) == 0
  output = capsys.readouterr().out
  assert 'Tie step: Energy Gun' in output
  assert '1. Firefly + Rampage' in output or '1. Outlaw + Kill Clip' in output
  assert 'sweeper ties --pick 2,18' in output
  assert world.fake.where('72') == ('inventory', WARLOCK)
  current_tie = world.state().session.current_tie
  assert current_tie.weapon == 'Energy Gun'
  assert current_tie.letters == {'71': 'A', '72': 'B'}


def test_a_pick_is_saved_and_the_loop_goes_back_to_the_rounds(world, capsys):
  main.main(['ties'])
  options = world.state().session.current_tie.options
  assert main.main(['ties', '--pick', '2']) == 0
  output = capsys.readouterr().out
  assert [list(roll.perks) for roll in world.saved_rolls] == [options[1]]
  assert world.saved_rolls[0].game_version == '244213.26.06.29.2000-1-bnet.65864'
  assert world.saved_rolls[0].added
  assert 'Energy Gun: keeping B; A is now a shard.' in output
  assert world.fake.where('71') == ('inventory', WARLOCK)  # a shard, to dismantle
  assert not world.fake.items['71'].locked
  assert world.fake.where('72') == ('vault', None)  # kept, and out of the way
  session = world.state().session
  assert session.current_tie is None
  assert session.tie_origins == {'71': 'vault', '72': HUNTER}


def test_listing_again_keeps_the_numbers_you_saw(world, capsys):
  main.main(['ties'])
  options = world.state().session.current_tie.options
  # Voltron refreshes overnight and now suggests a different pair.
  world.game_data.voltron = {
    'Energy Gun': [VoltronEntry(('Headstone', 'Kill Clip'), CATEGORIES)]
  }
  capsys.readouterr()
  main.main(['ties'])
  listing = capsys.readouterr().out
  assert world.state().session.current_tie.options == options
  for number, pair in enumerate(options, start=1):
    assert f'{number:>4}. {" + ".join(pair)}' in listing
  assert 'Headstone' not in listing


def test_a_skip_keeps_every_copy_for_the_session(world, capsys):
  main.main(['ties'])
  assert main.main(['ties', '--skip']) == 0
  assert world.saved_rolls == []
  session = world.state().session
  assert session.skipped_ties == ['Energy Gun']
  assert world.fake.items['71'].locked
  assert world.fake.items['72'].locked
  main.main(['ties'])
  assert 'No tied weapons are waiting for a pick' in capsys.readouterr().out


def test_skip_works_before_the_tie_step_starts(world, capsys):
  assert main.main(['ties', '--skip']) == 0
  assert 'Skipped Energy Gun' in capsys.readouterr().out
  assert world.state().session.skipped_ties == ['Energy Gun']


def test_a_tie_with_nothing_to_pick_can_be_skipped(world, capsys):
  world.game_data.voltron = {}
  main.main(['ties'])
  output = capsys.readouterr().out
  assert 'nothing to pick' in output
  assert 'sweeper ties --skip' in output
  assert main.main(['ties', '--pick', '1']) == 1
  assert main.main(['ties', '--skip']) == 0
  assert world.state().session.skipped_ties == ['Energy Gun']


def test_a_pick_out_of_range_is_refused(world, capsys):
  main.main(['ties'])
  assert main.main(['ties', '--pick', '7']) == 1
  assert 'there is no pair 7' in capsys.readouterr().err


def test_a_real_pick_needs_the_real_list_first(world, capsys):
  assert main.main(['ties', '--pick', '1']) == 1
  assert 'No tie step is in progress' in capsys.readouterr().err
  assert world.saved_rolls == []


def test_a_dry_run_listing_does_not_invite_a_real_pick(world, capsys):
  assert main.main(['ties', '--dry-run']) == 0
  output = capsys.readouterr().out
  assert 'start the tie step for real' in output
  assert 'sweeper ties --pick 2,18' not in output
  assert world.state().session.current_tie is None
  assert world.fake.where('72') == ('inventory', HUNTER)


def test_a_dry_run_pick_previews_the_new_plan(world, capsys):
  assert main.main(['ties', '--pick', '1', '--dry-run']) == 0
  output = capsys.readouterr().out
  assert 'Would save to wishlist.yaml under Energy Gun' in output
  assert 'Planning again would give: Energy Gun: keeping B; A is now a shard.' in output
  assert 'Saved to wishlist.yaml' not in output
  assert world.saved_rolls == []
  assert world.state().session.current_tie is None
  assert world.fake.calls == []


def test_an_interrupted_pick_finishes_when_run_again(world, capsys):
  main.main(['ties'])
  # Bungie goes down right after the pick is saved to wishlist.yaml.
  world.connection.failing_fetches = ['SystemDisabled']
  assert main.main(['ties', '--pick', '2']) == 1
  error_output = capsys.readouterr().err
  assert 'Run the same command again' in error_output
  assert world.state().session.current_tie.picked is not None
  assert len(world.saved_rolls) == 1

  assert main.main(['ties', '--pick', '2']) == 0
  output = capsys.readouterr().out
  assert 'Finishing your earlier pick for Energy Gun first.' in output
  assert 'Energy Gun: keeping B; A is now a shard.' in output
  assert len(world.saved_rolls) == 1  # saved once
  assert world.state().session.current_tie is None
  assert world.fake.where('72') == ('vault', None)


def test_a_dry_run_error_says_nothing_changed(world, capsys):
  world.connection.failing_fetches = ['SystemDisabled']
  assert main.main(['ties', '--dry-run']) == 1
  error_output = capsys.readouterr().err
  assert 'Dry run: nothing was changed.' in error_output
  assert 'Run the same command again' not in error_output
