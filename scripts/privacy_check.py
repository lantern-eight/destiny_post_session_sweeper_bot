'''Blocks personal details from being committed (see AGENTS.md).

Run by pre-commit on staged files and on the commit message, and by
tests/test_privacy.py over every file Git would commit. It looks for the shapes
a leak takes in this project: Destiny account and item IDs, Bungie Names, local
paths, private IP addresses, and emails. Placeholders that are clearly made up
pass: the Bungie Name Guardian#0042 (any code), and IDs with a run of six zeros.

Usage: python scripts/privacy_check.py FILE [FILE ...]
Exits 1 and lists each finding if any file has one.
'''

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Membership, character, and item instance IDs are 19-digit numbers. Shorter
# numbers (item hashes, timestamps) are not personal, so they are left alone.
LONG_ID_PATTERN = re.compile(r'\b\d{16,20}\b')
PLACEHOLDER_ID_ZEROS = '000000'

# A Bungie Name is a display name, '#', and a 4-digit code. Only the last word
# of the name is matched, which is enough to catch it.
BUNGIE_NAME_PATTERN = re.compile(r'\b(\w+)#(\d{4})\b')
PLACEHOLDER_BUNGIE_NAME = 'Guardian'

LOCAL_PATH_PATTERN = re.compile(r'(/Users/|/home/|[A-Za-z]:\\Users\\)[A-Za-z0-9._-]')

PRIVATE_IP_PATTERN = re.compile(
  r'\b(10\.\d{1,3}|192\.168|172\.(1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b'
)

EMAIL_PATTERN = re.compile(r'\b[\w.+-]+@([A-Za-z0-9-]+\.)+[A-Za-z]{2,}\b')
# Domains reserved for examples, which can never be someone's real address
PLACEHOLDER_EMAIL_DOMAINS = ('example.com', 'example.org', 'example.net')
# No-reply addresses are public on purpose: the Co-Authored-By line in commit
# messages, and GitHub's private-email stand-in for a commit author.
NO_REPLY_LOCAL_PARTS = ('noreply', 'no-reply')
NO_REPLY_DOMAIN = 'users.noreply.github.com'


def email_is_public(address: str) -> bool:
  local_part, domain = address.lower().rsplit('@', 1)
  return (
    domain in PLACEHOLDER_EMAIL_DOMAINS
    or domain == NO_REPLY_DOMAIN
    or local_part in NO_REPLY_LOCAL_PARTS
  )


@dataclass(frozen=True)
class Finding:
  path: str
  line_number: int
  kind: str
  text: str

  def __str__(self) -> str:
    return f'{self.path}:{self.line_number}: {self.kind}: {self.text}'


def findings_in_line(line: str) -> list[tuple[str, str]]:
  '''Each (kind, matched text) in one line that looks like a personal detail.'''
  found = []
  for match in LONG_ID_PATTERN.finditer(line):
    if PLACEHOLDER_ID_ZEROS not in match.group():
      found.append(('account or item ID', match.group()))
  for match in BUNGIE_NAME_PATTERN.finditer(line):
    if match.group(1) != PLACEHOLDER_BUNGIE_NAME:
      found.append(('Bungie Name', match.group()))
  for match in LOCAL_PATH_PATTERN.finditer(line):
    found.append(('local path', match.group()))
  for match in PRIVATE_IP_PATTERN.finditer(line):
    found.append(('private IP address', match.group()))
  for match in EMAIL_PATTERN.finditer(line):
    if not email_is_public(match.group()):
      found.append(('email', match.group()))
  return found


def findings_in_file(path: Path) -> list[Finding]:
  '''Every finding in a text file. Binary files are skipped.'''
  try:
    text = path.read_text(encoding='utf-8')
  except UnicodeDecodeError:
    return []
  return [
    Finding(str(path), line_number, kind, matched_text)
    for line_number, line in enumerate(text.splitlines(), start=1)
    for kind, matched_text in findings_in_line(line)
  ]


def main(arguments: list[str]) -> int:
  findings = [
    finding for argument in arguments for finding in findings_in_file(Path(argument))
  ]
  for finding in findings:
    print(finding)
  if findings:
    print(
      '\nThese look like personal details, which must not be committed '
      '(see "Committed files are public" in AGENTS.md). Use the placeholders '
      'there instead.'
    )
    return 1
  return 0


if __name__ == '__main__':
  sys.exit(main(sys.argv[1:]))
