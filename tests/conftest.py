'''Keep every test away from the real data/ folder and the command lock.'''

from __future__ import annotations

import pytest

from src import main


@pytest.fixture(autouse=True)
def isolated_command_files(tmp_path, monkeypatch):
  monkeypatch.setattr(main, 'COMMAND_LOCK_PATH', tmp_path / '.command.lock')
  monkeypatch.setattr(main, '_secure_files', lambda: None)
  # Commands under test write plans and snapshots to a temporary folder.
  monkeypatch.setattr(main, 'RUNS_DIRECTORY', tmp_path / 'runs')
  monkeypatch.setattr(main, 'WHAT_IF_DIRECTORY', tmp_path / 'runs' / 'what-if')
