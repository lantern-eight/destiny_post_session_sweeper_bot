'''Writing files whole or not at all, keeping personal data private, and pruning.'''

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

# Personal data (data/) is readable by you alone.
PRIVATE_DIRECTORY_MODE = 0o700


class CommandLockError(RuntimeError):
  '''Another sweeper command is running.'''


def write_atomically(path: Path, content: str | bytes | Iterable[bytes]) -> None:
  '''Write to a temporary file beside *path*, then rename it into place.

  An interrupted write or a full disk leaves the previous file untouched.
  *content* can be text, bytes, or chunks of bytes (a streamed download).
  '''
  path.parent.mkdir(parents=True, exist_ok=True)
  temporary_path = path.with_name(f'{path.name}.tmp')
  try:
    if isinstance(content, str):
      temporary_path.write_text(content, encoding='utf-8')
    elif isinstance(content, bytes):
      temporary_path.write_bytes(content)
    else:
      with temporary_path.open('wb') as temporary_file:
        for chunk in content:
          temporary_file.write(chunk)
    os.replace(temporary_path, path)
  except BaseException:
    temporary_path.unlink(missing_ok=True)
    raise


def make_private_directory(directory: Path) -> None:
  '''Create *directory* readable by you alone, or tighten it if it exists.'''
  directory.mkdir(mode=PRIVATE_DIRECTORY_MODE, parents=True, exist_ok=True)
  if directory.stat().st_mode & 0o777 != PRIVATE_DIRECTORY_MODE:
    directory.chmod(PRIVATE_DIRECTORY_MODE)


def prune_old_files(directory: Path, pattern: str, keep: int) -> list[Path]:
  '''Delete all but the newest *keep* files matching *pattern*.

  File names start with a UTC timestamp, so name order is age order. Returns
  the deleted paths.
  '''
  if not directory.exists():
    return []
  paths = sorted(path for path in directory.glob(pattern) if path.is_file())
  stale_paths = paths[:-keep] if keep > 0 else paths
  for path in stale_paths:
    path.unlink(missing_ok=True)
  return stale_paths


@contextmanager
def hold_command_lock(path: Path) -> Iterator[None]:
  '''Run one sweeper command at a time.

  Two at once could each save their own copy of the state (undoing the other's
  lock records) or refresh the Bungie sign-in at the same moment.
  '''
  path.parent.mkdir(parents=True, exist_ok=True)
  with path.open('a') as lock_file:
    try:
      fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
      raise CommandLockError(
        'Another sweeper command is still running. Let it finish, then try again.'
      ) from error
    try:
      yield
    finally:
      fcntl.flock(lock_file, fcntl.LOCK_UN)
