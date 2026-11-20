'''Tests for saving and summarizing profile snapshots.'''

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from src.profile_snapshot import (
  POSTMASTER_BUCKET_HASH,
  SNAPSHOTS_TO_KEEP,
  VAULT_BUCKET_HASH,
  IncompleteProfileError,
  SnapshotError,
  check_profile_is_complete,
  format_profile_summary,
  load_profile_snapshot,
  save_profile_snapshot,
)

PROFILE = {
  'characters': {
    'data': {
      'warlock-id': {
        'classType': 2,
        'light': 450,
        'dateLastPlayed': '2026-09-25T21:00:00Z',
      },
      'titan-id': {
        'classType': 0,
        'light': 440,
        'dateLastPlayed': '2026-09-01T18:00:00Z',
      },
    }
  },
  'characterInventories': {
    'data': {
      'warlock-id': {
        'items': [{'bucketHash': 1}, {'bucketHash': POSTMASTER_BUCKET_HASH}]
      },
      'titan-id': {'items': []},
    }
  },
  'characterEquipment': {'data': {'warlock-id': {'items': [{}, {}]}}},
  'profileInventory': {
    'data': {'items': [{'bucketHash': VAULT_BUCKET_HASH}, {'bucketHash': 999}]}
  },
}
MEMBERSHIP = {
  'membershipType': 3,
  'bungieGlobalDisplayName': 'Guardian',
  'bungieGlobalDisplayNameCode': 42,
}


def test_summary_lists_most_recent_character_first_with_counts():
  summary = format_profile_summary(PROFILE, MEMBERSHIP).splitlines()
  assert summary[0] == 'Account: Guardian#0042'
  assert summary[2].strip().startswith('Warlock')
  assert '1 carried, 2 equipped, 1 in Postmaster' in summary[2]
  assert summary[3].strip().startswith('Titan')
  assert summary[-1] == 'Vault: 1 item'


def test_snapshot_is_saved_under_a_timestamped_name(tmp_path):
  path = save_profile_snapshot(
    {'ok': True}, tmp_path, now=datetime(2026, 9, 26, 21, 14, tzinfo=UTC)
  )
  assert path.name == 'profile-20260926T211400Z.json'
  assert json.loads(path.read_text()) == {'ok': True}


def test_only_the_newest_snapshots_are_kept(tmp_path):
  for hour in range(SNAPSHOTS_TO_KEEP + 2):
    save_profile_snapshot({}, tmp_path, now=datetime(2026, 9, 26, hour, tzinfo=UTC))
  names = sorted(path.name for path in tmp_path.iterdir())
  assert len(names) == SNAPSHOTS_TO_KEEP
  assert names[0] == 'profile-20260926T020000Z.json'


def test_a_snapshot_can_be_named_without_its_folder(tmp_path):
  path = save_profile_snapshot(
    {'ok': True}, tmp_path, now=datetime(2026, 9, 26, 21, 14, tzinfo=UTC)
  )
  profile, found_path = load_profile_snapshot(path.name, tmp_path)
  assert (profile, found_path) == ({'ok': True}, path)


def test_a_missing_snapshot_lists_the_saved_ones(tmp_path):
  save_profile_snapshot({}, tmp_path, now=datetime(2026, 9, 26, 21, 14, tzinfo=UTC))
  with pytest.raises(SnapshotError, match='Saved snapshots: profile-20260926T211400Z'):
    load_profile_snapshot('profile-typo.json', tmp_path)


def test_a_profile_without_characters_is_incomplete():
  profile = {
    'characters': {'data': {}},
    'profileInventory': {'data': {}},
    'characterInventories': {'data': {}},
    'characterEquipment': {'data': {}},
  }
  with pytest.raises(IncompleteProfileError, match='your characters'):
    check_profile_is_complete(profile)
