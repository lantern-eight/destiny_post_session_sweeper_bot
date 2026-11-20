'''Tests for whole-or-nothing writes, private folders, and pruning.'''

from __future__ import annotations

import stat

import pytest

from src.files import make_private_directory, prune_old_files, write_atomically


def test_a_failed_write_leaves_the_old_file_alone(tmp_path):
  path = tmp_path / 'wishlist.yaml'
  path.write_text('old rolls')

  def chunks():
    yield b'new rolls, half written'
    raise OSError('No space left on device')

  with pytest.raises(OSError, match='No space'):
    write_atomically(path, chunks())
  assert path.read_text() == 'old rolls'
  assert list(tmp_path.iterdir()) == [path]


def test_text_bytes_and_chunks_are_all_written(tmp_path):
  write_atomically(tmp_path / 'a.txt', 'text')
  write_atomically(tmp_path / 'b.bin', b'bytes')
  write_atomically(tmp_path / 'c.bin', iter([b'chu', b'nks']))
  assert (tmp_path / 'a.txt').read_text() == 'text'
  assert (tmp_path / 'b.bin').read_bytes() == b'bytes'
  assert (tmp_path / 'c.bin').read_bytes() == b'chunks'


def test_personal_data_folders_are_made_private(tmp_path):
  data = tmp_path / 'data'
  data.mkdir(mode=0o755)
  make_private_directory(data)
  assert stat.S_IMODE(data.stat().st_mode) == 0o700


def test_pruning_keeps_the_newest_files(tmp_path):
  for hour in range(4):
    (tmp_path / f'profile-20260927T0{hour}0000Z.json').write_text('{}')
  (tmp_path / 'notes.txt').write_text('not a snapshot')
  deleted = prune_old_files(tmp_path, 'profile-*.json', keep=2)
  assert [path.name for path in deleted] == [
    'profile-20260927T000000Z.json',
    'profile-20260927T010000Z.json',
  ]
  assert sorted(path.name for path in tmp_path.iterdir()) == [
    'notes.txt',
    'profile-20260927T020000Z.json',
    'profile-20260927T030000Z.json',
  ]
