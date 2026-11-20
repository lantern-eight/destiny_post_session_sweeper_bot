'''Raw profile snapshots (gitignored, under data/) and a terminal summary.'''

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from src.files import prune_old_files, write_atomically
from src.game_data import CLASS_NAMES, POSTMASTER_BUCKET_HASH, VAULT_BUCKET_HASH
from src.paths import PROFILE_SNAPSHOT_DIRECTORY
from src.text import count_label

SNAPSHOT_PATTERN = 'profile-*.json'
SNAPSHOTS_TO_KEEP = 5
# The parts of a profile every command relies on. Bungie occasionally sends a
# profile with one left out, and a missing vault would look like dismantled gear.
REQUIRED_PROFILE_PARTS = {
  'characters': 'your characters',
  'profileInventory': 'the vault',
  'characterInventories': "your characters' inventories",
  'characterEquipment': 'your equipped gear',
}


class IncompleteProfileError(RuntimeError):
  '''Bungie sent a profile with parts missing, so the tool will not act on it.'''


class SnapshotError(RuntimeError):
  '''A saved profile snapshot is missing or cannot be read.'''


def check_profile_is_complete(profile: dict) -> None:
  '''Raise IncompleteProfileError unless every part the tool needs is there.'''
  missing = [
    description
    for key, description in REQUIRED_PROFILE_PARTS.items()
    if 'data' not in (profile.get(key) or {})
  ]
  characters = (profile.get('characters') or {}).get('data') or {}
  if not missing and not characters:
    missing.append('your characters')
  for key in ('characterInventories', 'characterEquipment'):
    held = (profile.get(key) or {}).get('data')
    if held is not None and any(character not in held for character in characters):
      missing.append(REQUIRED_PROFILE_PARTS[key])
  if missing:
    raise IncompleteProfileError(
      f'Bungie sent an incomplete profile (missing {", ".join(missing)}). '
      'Nothing was changed; try again in a minute.'
    )


def save_profile_snapshot(
  profile: dict,
  directory: Path = PROFILE_SNAPSHOT_DIRECTORY,
  now: datetime | None = None,
) -> Path:
  '''Save the raw profile, keeping only the newest SNAPSHOTS_TO_KEEP.'''
  timestamp = (now or datetime.now(UTC)).strftime('%Y%m%dT%H%M%SZ')
  path = directory / f'profile-{timestamp}.json'
  write_atomically(path, json.dumps(profile))
  prune_old_files(directory, SNAPSHOT_PATTERN, SNAPSHOTS_TO_KEEP)
  return path


def load_profile_snapshot(
  name: str, directory: Path = PROFILE_SNAPSHOT_DIRECTORY
) -> tuple[dict, Path]:
  '''A saved snapshot by path, or by file name in data/profiles/.'''
  path = Path(name)
  if not path.exists() and path.parent == Path('.'):
    path = directory / name
  if not path.exists():
    saved = sorted(directory.glob(SNAPSHOT_PATTERN), reverse=True)[:SNAPSHOTS_TO_KEEP]
    choices = (
      f' Saved snapshots: {", ".join(snapshot.name for snapshot in saved)}.'
      if saved
      else ' There are no saved snapshots yet; `uv run sweeper fetch` saves one.'
    )
    raise SnapshotError(f'No profile snapshot at {name}.{choices}')
  try:
    return json.loads(path.read_text()), path
  except (OSError, UnicodeDecodeError, ValueError) as error:
    raise SnapshotError(f'{path.name} is not a readable snapshot ({error}).') from error


def format_profile_summary(profile: dict, membership: dict) -> str:
  '''Describe the account's characters and where its items are.'''
  lines = [f'Account: {display_name(membership)}']
  characters = profile.get('characters', {}).get('data', {})
  inventories = profile.get('characterInventories', {}).get('data', {})
  equipment = profile.get('characterEquipment', {}).get('data', {})
  characters_by_recency = sorted(
    characters.items(),
    key=lambda entry: entry[1].get('dateLastPlayed', ''),
    reverse=True,
  )
  lines.append('Characters, most recently played first:')
  for character_id, character in characters_by_recency:
    class_name = CLASS_NAMES.get(character.get('classType'), 'Unknown')
    inventory_items = inventories.get(character_id, {}).get('items', [])
    postmaster_count = sum(
      1 for item in inventory_items if item.get('bucketHash') == POSTMASTER_BUCKET_HASH
    )
    carried_count = len(inventory_items) - postmaster_count
    equipped_count = len(equipment.get(character_id, {}).get('items', []))
    power = character.get('light', '?')
    last_played = character.get('dateLastPlayed', '?')
    lines.append(
      f'  {class_name:<8} power {power}, last played {last_played}: '
      f'{carried_count} carried, {equipped_count} equipped, '
      f'{postmaster_count} in Postmaster'
    )
  profile_items = profile.get('profileInventory', {}).get('data', {}).get('items', [])
  vault_count = sum(
    1 for item in profile_items if item.get('bucketHash') == VAULT_BUCKET_HASH
  )
  lines.append(f'Vault: {count_label(vault_count, "item")}')
  return '\n'.join(lines)


def display_name(user_info: dict) -> str:
  '''Bungie Name such as Guardian#0042, from a membership or profile userInfo.'''
  name = user_info.get('bungieGlobalDisplayName') or user_info.get('displayName')
  code = user_info.get('bungieGlobalDisplayNameCode')
  if name and code is not None:
    return f'{name}#{code:04d}'
  return name or 'unknown'
