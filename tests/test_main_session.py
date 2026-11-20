'''Tests for plan, apply, next, and done through the CLI.'''

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from src import main
from src.bungie_api import BungieApiError
from src.files import hold_command_lock
from src.session import start_session
from src.state import StateFile
from tests.account_fixtures import (
  ENERGY_GUN,
  KINETIC_GUN,
  NOW,
  WARLOCK,
  FakeBungie,
  FakeItem,
  make_plan,
  open_account,
)
from tests.fixtures import PREFERENCES
from tests.main_fixtures import FakeConnection, FakeGameData


class World:
  def __init__(self, fake: FakeBungie, state_path: Path, plans: list) -> None:
    self.fake = fake
    self.state_path = state_path
    self.plans = plans
    self.connection = FakeConnection(fake)

  def state(self):
    return StateFile(self.state_path).state


@pytest.fixture
def world(tmp_path, monkeypatch):
  '''The Warlock carries a keeper you locked, a new keeper, and a new shard.'''
  fake = FakeBungie(
    [
      FakeItem('11', KINETIC_GUN, 'inventory', WARLOCK, locked=True),
      FakeItem('12', KINETIC_GUN, 'inventory', WARLOCK),
      FakeItem('13', ENERGY_GUN, 'inventory', WARLOCK),
      FakeItem('21', ENERGY_GUN),
    ]
  )
  plans = [make_plan({'11': 'keep', '12': 'keep', '13': 'shard', '21': 'shard'})]
  world = World(fake, tmp_path / 'state.json', plans)
  monkeypatch.setattr(
    main, '_connect_for_actions', lambda status: (world.connection, FakeGameData())
  )
  monkeypatch.setattr(
    main.PlanView,
    'load_latest',
    classmethod(lambda cls, runs_directory=None: plans[-1]),
  )
  monkeypatch.setattr(
    main, 'StateFile', lambda dry_run=False: StateFile(world.state_path, dry_run)
  )
  monkeypatch.setattr(main, '_now', lambda: NOW + timedelta(minutes=5))
  return world


def test_apply_next_and_done_run_a_whole_session(world, capsys):
  assert main.main(['apply']) == 0
  output = capsys.readouterr().out
  assert 'Round 1 on your Warlock:' in output
  assert 'Locked 1 keeper.' in output
  assert 'Moved 2 keepers from your inventory to the vault (1 you were carrying' in (
    output
  )
  assert world.fake.where('21') == ('inventory', WARLOCK)

  world.fake.dismantle('13', '21')
  assert main.main(['next']) == 0
  assert 'You dismantled 2 shards since the last round.' in capsys.readouterr().out

  assert main.main(['done']) == 0
  assert 'Session finished.' in capsys.readouterr().out
  assert world.fake.where('11') == ('inventory', WARLOCK)  # you were carrying it
  assert world.fake.where('12') == ('vault', None)  # a new keeper stays put
  assert world.state().session is None


def test_each_step_that_does_something_is_named_as_it_starts(world, capsys):
  assert main.main(['apply']) == 0
  # Away from a terminal, one line per step; on one, a line that counts up.
  assert capsys.readouterr().err.splitlines() == [
    'Moving keepers from your inventory to the vault (2)...',
    'Locking keepers (1)...',
    'Bringing shards into your inventory...',
  ]
  assert main.main(['done']) == 0
  assert capsys.readouterr().err.splitlines() == [
    'Putting back the keepers you were carrying (1)...'
  ]


def test_a_stale_session_is_finished_without_mixing_in_new_keepers(world, capsys):
  # Yesterday's session never ended: 11 went to the vault and is still there.
  yesterday = NOW - timedelta(hours=13)
  old_plan = make_plan({'11': 'keep'}, generated=yesterday)
  del world.fake.items['12'], world.fake.items['13']
  start_session(
    open_account(world.fake), old_plan, StateFile(world.state_path), yesterday
  )
  # Today you picked up 12, a new keeper, and planned again.
  world.fake.items['12'] = FakeItem('12', KINETIC_GUN, 'inventory', WARLOCK)
  world.plans.append(make_plan({'11': 'keep', '12': 'keep', '21': 'shard'}))

  assert main.main(['apply']) == 0
  output = capsys.readouterr().out
  assert 'was never finished; putting that gear back first' in output
  assert world.state().session.carried == ['11']  # 12 is new, not carried

  main.main(['done'])
  assert world.fake.where('11') == ('inventory', WARLOCK)
  assert world.fake.where('12') == ('vault', None)


def test_a_dry_run_apply_changes_nothing(world, capsys):
  assert main.main(['apply', '--dry-run']) == 0
  output = capsys.readouterr().out
  # 1 lock, 2 keepers out, and 1 shard staged.
  assert 'Dry run: nothing was changed. A real run would send 4 actions' in output
  assert world.fake.calls == []
  assert not world.state_path.exists()


def test_only_one_command_runs_at_a_time(world, capsys):
  with hold_command_lock(main.COMMAND_LOCK_PATH):
    assert main.main(['apply']) == 1
  assert 'Another sweeper command is still running' in capsys.readouterr().err
  assert world.fake.calls == []


def test_a_failure_before_anything_changed_says_nothing_about_resuming(world, capsys):
  world.connection.failing_fetches = ['SystemDisabled']
  assert main.main(['apply']) == 1
  error_output = capsys.readouterr().err
  assert 'SystemDisabled' in error_output
  assert 'Run the same command again' not in error_output


def test_a_failure_part_way_says_the_progress_is_recorded(world, capsys):
  world.fake.failures['21'] = 'SystemDisabled'  # Bungie goes down mid-run
  assert main.main(['apply']) == 1
  assert 'Everything done so far is recorded' in capsys.readouterr().err
  assert main.main(['apply']) == 0
  assert world.fake.where('21') == ('inventory', WARLOCK)


def test_a_what_if_plan_is_kept_apart_from_the_plans_apply_uses(
  tmp_path, monkeypatch, capsys
):
  snapshot = tmp_path / 'profile-20260927T010000Z.json'
  snapshot.write_text(json.dumps(FakeBungie([FakeItem('11', KINETIC_GUN)]).profile()))
  monkeypatch.setattr(
    main, 'GameData', lambda http, preferences, status: FakeGameData()
  )
  monkeypatch.setattr(main, 'load_preferences', lambda: PREFERENCES)
  monkeypatch.setattr(main, 'load_wishlist', dict)
  monkeypatch.setattr(main, 'load_previous_keepers', lambda runs_directory: set())
  monkeypatch.setattr(main, 'RUNS_DIRECTORY', tmp_path / 'runs')
  monkeypatch.setattr(main, 'WHAT_IF_DIRECTORY', tmp_path / 'runs' / 'what-if')
  monkeypatch.setattr(
    main, 'StateFile', lambda dry_run=False: StateFile(tmp_path / 'state.json', True)
  )
  assert main.main(['plan', '--profile', str(snapshot)]) == 0
  output = capsys.readouterr().out
  assert 'what-if plan from profile-20260927T010000Z.json; apply never uses it' in (
    output
  )
  assert len(list((tmp_path / 'runs' / 'what-if').glob('*-plan.yaml'))) == 1
  assert not list((tmp_path / 'runs').glob('*-plan.yaml'))


def test_a_missing_snapshot_lists_the_saved_ones(tmp_path, monkeypatch, capsys):
  monkeypatch.setattr(main, 'load_preferences', lambda: PREFERENCES)
  monkeypatch.setattr(
    main, 'StateFile', lambda dry_run=False: StateFile(tmp_path / 'state.json', True)
  )
  assert main.main(['plan', '--profile', 'profile-typo.json']) == 1
  assert 'No profile snapshot at profile-typo.json' in capsys.readouterr().err


def test_a_session_error_is_one_clear_line(world, capsys):
  assert main.main(['next']) == 1
  error_output = capsys.readouterr().err
  assert error_output.strip() == (
    'Error: No session is in progress. Start one with `uv run sweeper apply`.'
  )


@pytest.mark.parametrize('error_status', ['SystemDisabled'])
def test_bungie_errors_are_not_tracebacks(world, capsys, error_status):
  def fail():
    raise BungieApiError(f'GET profile failed: {error_status}', error_status)

  world.connection.fetch_profile = lambda components=None: fail()
  assert main.main(['apply']) == 1
  assert 'Traceback' not in capsys.readouterr().err
