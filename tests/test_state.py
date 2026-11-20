'''Tests for the tool state file.'''

from __future__ import annotations

import pytest

from src.state import (
  CurrentTie,
  Session,
  StateError,
  StateFile,
  ToolState,
  load_state,
  save_state,
)


def test_state_survives_a_round_trip(tmp_path):
  path = tmp_path / 'data' / 'state.json'
  session = Session(
    character_id='2305843009000000001',
    class_name='Warlock',
    started='2026-09-27T20:00:00+00:00',
    plan='20260927T200000Z-plan.yaml',
    carried=['11'],
    round=2,
    staged=['13'],
    tie_origins={'72': '2305843009000000002'},
    skipped_ties=['Hammerhead'],
    current_tie=CurrentTie(
      'Energy Gun',
      'Energy Gun',
      ['71', '72'],
      options=[['Firefly', 'Outlaw']],
      letters={'71': 'A', '72': 'B'},
      picked=[['Firefly', 'Outlaw']],
    ),
  )
  state = ToolState({'12', '41'}, {'43'}, session)
  save_state(state, path)
  assert load_state(path) == state
  assert load_state(path).session.current_tie.answered


def test_a_damaged_state_file_says_what_it_holds(tmp_path):
  path = tmp_path / 'state.json'
  path.write_text('{"tool_locked_ids": ["1"], "session": {"char')
  with pytest.raises(StateError, match='records the locks the tool placed'):
    load_state(path)


def test_a_dry_run_never_writes(tmp_path):
  path = tmp_path / 'state.json'
  state_file = StateFile(path, dry_run=True)
  state_file.state.tool_locked_ids.add('1')
  state_file.save()
  assert not path.exists()
  assert state_file.saves == 0


def test_saves_are_counted(tmp_path):
  state_file = StateFile(tmp_path / 'state.json')
  state_file.save()
  assert state_file.saves == 1
