'''The personal-detail check, and that nothing Git would commit fails it.

Example leaks below are built from pieces, so this file passes the check itself.
'''

import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.privacy_check import findings_in_file, findings_in_line, main

REPO_ROOT = Path(__file__).resolve().parent.parent


def kinds_in(line: str) -> list[str]:
  return [kind for kind, _ in findings_in_line(line)]


@pytest.mark.parametrize(
  ('line', 'kind'),
  [
    ('membershipId: ' + '4611686018' + '467284386', 'account or item ID'),
    ('"itemInstanceId": "' + '6917529' + '842130457211"', 'account or item ID'),
    ('Account: Some ' + 'Player#' + '1234', 'Bungie Name'),
    ('cd ' + '/Users/' + 'someone/projects', 'local path'),
    ('cache in ' + '/home/' + 'someone/.cache', 'local path'),
    ('C:\\' + 'Users\\' + 'someone\\Desktop', 'local path'),
    ('server at ' + '192.168' + '.1.20', 'private IP address'),
    ('server at ' + '10.0' + '.0.5', 'private IP address'),
    ('server at ' + '172.20' + '.3.4', 'private IP address'),
    ('contact ' + 'someone' + '@' + 'mail.net', 'email'),
  ],
)
def test_personal_details_are_found(line, kind):
  assert kinds_in(line) == [kind]


@pytest.mark.parametrize(
  'line',
  [
    'character_id = "2305843009000000001"',
    'Account: Guardian#0042',
    'Account: Guardian#0007',
    'itemHash 3628991658, timestamp 1727740800000',
    'color: #1234',
    "return f'{name}#{code:04d}'",
    'redirect to https://localhost:6821/callback',
    'public DNS 8.8.8.8',
    'contact player@example.com',
    'Co-Authored-By: Claude <noreply' + '@anthropic.com>',
    'Author: player <12345+player@users.' + 'noreply.github.com>',
    'paths relative to the repo: src/main.py',
  ],
)
def test_placeholders_and_ordinary_text_pass(line):
  assert kinds_in(line) == []


def test_binary_files_are_skipped(tmp_path):
  binary_path = tmp_path / 'image.png'
  binary_path.write_bytes(b'\x89PNG\r\n\x1a\n\xff\xfe')
  assert findings_in_file(binary_path) == []


def test_main_reports_findings_and_fails(tmp_path, capsys):
  leaky_path = tmp_path / 'notes.md'
  leaky_path.write_text('ok line\nsee ' + '/Users/' + 'someone/data\n')
  assert main([str(leaky_path)]) == 1
  assert f'{leaky_path}:2: local path' in capsys.readouterr().out


def test_main_passes_clean_files(tmp_path):
  clean_path = tmp_path / 'clean.md'
  clean_path.write_text('Account: Guardian#0042\n')
  assert main([str(clean_path)]) == 0


def files_git_would_commit() -> list[Path]:
  '''Tracked files, plus untracked ones that are not gitignored.'''
  listing = subprocess.run(
    ['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'],
    cwd=REPO_ROOT,
    capture_output=True,
    check=True,
  ).stdout.decode()
  return [REPO_ROOT / name for name in listing.split('\0') if name]


@pytest.mark.skipif(
  shutil.which('git') is None or not (REPO_ROOT / '.git').exists(),
  reason='needs a Git checkout',
)
def test_nothing_git_would_commit_has_personal_details():
  # Gitignored files (data/, .cache/, .env, notes/) are private and not checked.
  findings = [
    str(finding)
    for path in files_git_would_commit()
    if path.is_file()
    for finding in findings_in_file(path)
  ]
  assert findings == []
