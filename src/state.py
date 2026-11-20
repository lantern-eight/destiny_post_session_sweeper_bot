'''Tool state kept between runs in data/state.json (gitignored).

Holds the locks the tool placed, so it only ever unlocks its own, and while an
end-of-session run is in progress, everything needed to put your gear back.
The file is rewritten after every lock change, so an interrupted run can pick
up where it stopped.
'''

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from src.files import write_atomically
from src.paths import STATE_PATH


class StateError(RuntimeError):
  '''data/state.json cannot be read.'''


@dataclass
class CurrentTie:
  '''The tied weapon whose copies are in your inventory, waiting for a pick.'''

  weapon: str  # e.g. "Hammerhead (shiny)"
  group_name: str
  copies: list[str]
  # The trait pairs shown to you; pick number N is options[N - 1].
  options: list[list[str]] = field(default_factory=list)
  # Instance ID -> the letter it was listed under, so results use the same ones.
  letters: dict[str, str] = field(default_factory=dict)
  # Your answer. It is kept until the round after it has run, so a pick that
  # was interrupted (Bungie down, sign-in expired) finishes on the next `ties`.
  picked: list[list[str]] | None = None
  skipped: bool = False

  @property
  def answered(self) -> bool:
    return self.picked is not None or self.skipped


@dataclass
class Session:
  character_id: str
  class_name: str
  started: str  # ISO 8601
  plan: str  # file name of the plan last applied
  # Keepers you were carrying when the session started: they wait in the
  # vault during the session and come back at done.
  carried: list[str] = field(default_factory=list)
  round: int = 0
  # Shards waiting in your inventory for you to dismantle.
  staged: list[str] = field(default_factory=list)
  # Kept weapons brought in next to their shards to compare against; they go
  # back to the vault once those shards are gone.
  compared: list[str] = field(default_factory=list)
  # Where each tie-step copy came from: 'vault' or a character ID.
  tie_origins: dict[str, str] = field(default_factory=dict)
  skipped_ties: list[str] = field(default_factory=list)
  current_tie: CurrentTie | None = None

  @property
  def started_at(self) -> datetime:
    return datetime.fromisoformat(self.started)


@dataclass
class ToolState:
  tool_locked_ids: set[str] = field(default_factory=set)
  # Locks sent to Bungie whose reply never came back (a timeout, or Ctrl-C).
  # The next run settles them: an item locked by then was locked by the tool.
  pending_lock_ids: set[str] = field(default_factory=set)
  session: Session | None = None


def load_state(path: Path = STATE_PATH) -> ToolState:
  if not path.exists():
    return ToolState()
  try:
    raw_state = json.loads(path.read_text())
    raw_session = raw_state.get('session')
    return ToolState(
      tool_locked_ids=set(raw_state.get('tool_locked_ids', [])),
      pending_lock_ids=set(raw_state.get('pending_lock_ids', [])),
      session=_session_from_json(raw_session) if raw_session else None,
    )
  except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
    raise StateError(
      f'{path} is damaged ({error}). It records the locks the tool placed and '
      'any session in progress. Restore it from a backup if you have one, or '
      'move it aside to start fresh: the tool then treats every lock as yours '
      'and never unlocks it.'
    ) from error


def save_state(state: ToolState, path: Path = STATE_PATH) -> None:
  '''Write atomically, so an interrupted run never leaves a half-written file.'''
  raw_state = {
    'tool_locked_ids': sorted(state.tool_locked_ids),
    'pending_lock_ids': sorted(state.pending_lock_ids),
    'session': asdict(state.session) if state.session else None,
  }
  write_atomically(path, json.dumps(raw_state, indent=1))


class StateFile:
  '''The state plus where it lives. A dry run works on it but never saves.'''

  def __init__(self, path: Path = STATE_PATH, dry_run: bool = False) -> None:
    self.path = path
    self.dry_run = dry_run
    self.state = load_state(path)
    self.saves = 0

  def save(self) -> None:
    if not self.dry_run:
      save_state(self.state, self.path)
      self.saves += 1


def _session_from_json(raw_session: dict) -> Session:
  raw_tie = raw_session.get('current_tie')
  return Session(
    character_id=raw_session['character_id'],
    class_name=raw_session['class_name'],
    started=raw_session['started'],
    plan=raw_session['plan'],
    carried=list(raw_session.get('carried', [])),
    round=raw_session.get('round', 0),
    staged=list(raw_session.get('staged', [])),
    compared=list(raw_session.get('compared', [])),
    tie_origins=dict(raw_session.get('tie_origins', {})),
    skipped_ties=list(raw_session.get('skipped_ties', [])),
    current_tie=CurrentTie(**raw_tie) if raw_tie else None,
  )
