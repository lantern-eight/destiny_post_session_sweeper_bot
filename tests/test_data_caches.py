'''Tests for the manifest and Voltron download caches.'''

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock

import pytest
import requests

from src.manifest import (
  COMPLETE_MARKER,
  MANIFEST_URL,
  TABLES_USED,
  TRIM_VERSION,
  Manifest,
  ManifestError,
)
from src.voltron import MAXIMUM_AGE_SECONDS, ensure_voltron

WEAPON = {
  'displayProperties': {'name': 'Fatebringer', 'description': 'long lore text'},
  'itemType': 3,
  'itemTypeDisplayName': 'Hand Cannon',
  'inventory': {
    'bucketTypeHash': 1,
    'tierType': 5,
    'maxStackSize': 1,
    'recipeItemHash': 700,
  },
  'screenshot': '/big.jpg',
}
FIREFLY = {
  'displayProperties': {'name': 'Firefly'},
  'plug': {'plugCategoryIdentifier': 'frames', 'plugStyle': 0},
}
TABLES = {
  'DestinyInventoryItemDefinition': {
    '100': WEAPON,
    '200': FIREFLY,
    '300': {'displayProperties': {'name': 'Ghost Shell'}, 'itemType': 24},
  },
  'DestinyInventoryBucketDefinition': {'138197802': {'itemCount': 1300, 'extra': 1}},
  'DestinyEquipableItemSetDefinition': {
    '555': {'displayProperties': {'name': 'Techsec'}}
  },
  'DestinyLoadoutNameDefinition': {'1': {'name': 'PvE', 'index': 3}},
  'DestinyPlugSetDefinition': {
    '9': {'reusablePlugItems': [{'plugItemHash': 200, 'currentlyCanRoll': True}]}
  },
}
VOLTRON_LINE = b'dimwishlist:item=100&perks=200\n'


class FakeResponse:
  def __init__(self, payload: object = None, content: bytes = b'') -> None:
    self.payload = payload
    self.content = content
    self.headers = {'Content-Length': str(len(content))}

  def json(self) -> object:
    return self.payload

  def raise_for_status(self) -> None:
    return None

  def iter_content(self, chunk_size: int):
    for start in range(0, len(self.content), 7):
      yield self.content[start : start + 7]

  def __enter__(self) -> FakeResponse:
    return self

  def __exit__(self, *details: object) -> None:
    return None


class FakeManifestSession:
  def __init__(self, version: str, content_host: str = 'https://www.bungie.net'):
    self.version = version
    self.content_host = content_host
    self.urls: list[str] = []
    self.offline = False

  def get(self, url: str, timeout: float, stream: bool = False) -> FakeResponse:
    if self.offline:
      raise requests.ConnectionError('offline')
    self.urls.append(url)
    if url == MANIFEST_URL:
      paths = {name: f'{self.content_host}/common/{name}.json' for name in TABLES}
      return FakeResponse(
        {
          'Response': {
            'version': self.version,
            'jsonWorldComponentContentPaths': {'en': paths},
          }
        }
      )
    name = url.rsplit('/', 1)[-1].removesuffix('.json')
    return FakeResponse(content=json.dumps(TABLES[name]).encode())


def test_tables_are_trimmed_once_then_read_from_the_cache(tmp_path):
  session = FakeManifestSession('v1')
  items = Manifest(tmp_path, session).table('DestinyInventoryItemDefinition')
  # Anything that is not gear or a plug keeps only its name and type.
  assert items['300'] == {'displayProperties': {'name': 'Ghost Shell'}, 'itemType': 24}
  assert items['100'] == {
    'displayProperties': {'name': 'Fatebringer'},
    'itemType': 3,
    'itemTypeDisplayName': 'Hand Cannon',
    'inventory': {'bucketTypeHash': 1, 'tierType': 5, 'recipeItemHash': 700},
  }
  assert items['200']['plug'] == {'plugCategoryIdentifier': 'frames'}
  assert len(session.urls) == 1 + len(TABLES_USED)

  second_session = FakeManifestSession('v1')
  plug_sets = Manifest(tmp_path, second_session).table('DestinyPlugSetDefinition')
  assert plug_sets == {'9': {'reusablePlugItems': [{'plugItemHash': 200}]}}
  assert second_session.urls == [MANIFEST_URL]  # tables come from the cache


def test_older_versions_are_dropped_after_a_complete_download(tmp_path):
  (tmp_path / 'v0').mkdir()
  (tmp_path / 'v0' / 'DestinyStatDefinition.json').write_text('{}')
  version = Manifest(tmp_path, FakeManifestSession('v1')).ensure_tables()
  assert version == 'v1'
  assert not (tmp_path / 'v0').exists()
  assert (tmp_path / 'v1' / COMPLETE_MARKER).exists()


def test_an_untrimmed_cache_is_trimmed_without_downloading_again(tmp_path):
  folder = tmp_path / 'v1'
  folder.mkdir()
  for name, table in TABLES.items():
    (folder / f'{name}.json').write_text(json.dumps(table))
  session = FakeManifestSession('v1')
  Manifest(tmp_path, session).ensure_tables()
  assert session.urls == [MANIFEST_URL]
  assert sorted(path.name for path in folder.iterdir()) == sorted(
    [COMPLETE_MARKER, *(f'{name}.v{TRIM_VERSION}.json' for name in TABLES_USED)]
  )


def test_offline_only_a_complete_cached_version_is_used(tmp_path):
  Manifest(tmp_path / 'cache', FakeManifestSession('v1')).ensure_tables()
  (tmp_path / 'cache' / 'v2').mkdir()  # a half-finished download of a newer one
  session = FakeManifestSession('v2')
  session.offline = True
  manifest = Manifest(tmp_path / 'cache', session)
  assert manifest.version == 'v1'
  assert manifest.offline


def test_an_odd_manifest_version_is_refused(tmp_path):
  with pytest.raises(ManifestError, match='unexpected manifest version'):
    _ = Manifest(tmp_path, FakeManifestSession('../../outside')).version


def test_a_download_pointing_away_from_bungie_is_refused(tmp_path):
  session = FakeManifestSession('v1', content_host='https://example.com')
  with pytest.raises(ManifestError, match='somewhere else'):
    Manifest(tmp_path, session).ensure_tables()


def test_fresh_voltron_cache_is_reused(tmp_path):
  cache_path = tmp_path / 'voltron.txt'
  cache_path.write_bytes(VOLTRON_LINE)
  session = MagicMock()
  download = ensure_voltron(cache_path, session, now=cache_path.stat().st_mtime + 60)
  session.get.assert_not_called()
  assert download.note == ''


def old_voltron(tmp_path, rolls: int = 1):
  cache_path = tmp_path / 'voltron.txt'
  cache_path.write_bytes(VOLTRON_LINE * rolls)
  modified_time = 1_800_000_000
  os.utime(cache_path, (modified_time, modified_time))
  return cache_path, modified_time + MAXIMUM_AGE_SECONDS + 1


def test_day_old_voltron_cache_is_downloaded_again(tmp_path):
  cache_path, now = old_voltron(tmp_path)
  session = MagicMock()
  session.get.return_value.content = VOLTRON_LINE * 2
  ensure_voltron(cache_path, session, now=now)
  assert cache_path.read_bytes() == VOLTRON_LINE * 2


def test_refresh_downloads_even_a_fresh_copy(tmp_path):
  cache_path = tmp_path / 'voltron.txt'
  cache_path.write_bytes(VOLTRON_LINE)
  session = MagicMock()
  session.get.return_value.content = VOLTRON_LINE * 3
  ensure_voltron(cache_path, session, refresh=True)
  assert cache_path.read_bytes() == VOLTRON_LINE * 3


def test_failed_voltron_download_falls_back_to_the_old_cache(tmp_path):
  cache_path, now = old_voltron(tmp_path)
  session = MagicMock()
  session.get.side_effect = requests.ConnectionError('offline')
  download = ensure_voltron(cache_path, session, now=now)
  assert download.path.read_bytes() == VOLTRON_LINE
  assert 'Could not download a fresh Voltron wishlist' in download.note


def test_a_much_smaller_voltron_download_is_not_trusted(tmp_path):
  cache_path, now = old_voltron(tmp_path, rolls=10)
  session = MagicMock()
  session.get.return_value.content = VOLTRON_LINE * 2
  download = ensure_voltron(cache_path, session, now=now)
  assert cache_path.read_bytes() == VOLTRON_LINE * 10
  assert 'has 2 rolls, under half of the 10 before' in download.note
  # Checked again tomorrow, not on every command.
  assert cache_path.stat().st_mtime == now

  ensure_voltron(cache_path, session, refresh=True, accept_smaller=True)
  assert cache_path.read_bytes() == VOLTRON_LINE * 2
