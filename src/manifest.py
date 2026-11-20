'''Bungie manifest tables, downloaded once per manifest version and cached.

The manifest is public: no API key or sign-in is needed. Each table is trimmed
to the definitions and fields the tool reads before it is cached, which turns
the 200 MB item table into a small fraction of that, so every command loads
quickly.
'''

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

from src.definitions import definition_name
from src.files import write_atomically
from src.game_data import ITEM_TYPE_ARMOR, ITEM_TYPE_WEAPON
from src.http_session import make_session
from src.paths import MANIFEST_CACHE_DIRECTORY

MANIFEST_URL = 'https://www.bungie.net/Platform/Destiny2/Manifest/'
CONTENT_HOST = 'www.bungie.net'
LANGUAGE = 'en'
TABLES_USED = (
  'DestinyInventoryItemDefinition',
  'DestinyInventoryBucketDefinition',
  'DestinyEquipableItemSetDefinition',
  'DestinyLoadoutNameDefinition',
  'DestinyPlugSetDefinition',
)
# Bump when a trim below changes, so cached tables are rebuilt.
TRIM_VERSION = 2
# Written into a version's folder once every table is cached. Only complete
# folders are used when Bungie cannot be reached.
COMPLETE_MARKER = 'complete.json'
VERSION_PATTERN = re.compile(r'[\w.\-]+')
DOWNLOAD_CHUNK_BYTES = 1 << 20


class ManifestError(RuntimeError):
  '''Bungie's manifest could not be fetched or looks wrong.'''


class Manifest:
  def __init__(
    self,
    cache_directory: Path = MANIFEST_CACHE_DIRECTORY,
    session: requests.Session | None = None,
    progress: Callable[[str], None] | None = None,
  ) -> None:
    self.cache_directory = cache_directory
    self.session = session or make_session()
    self.progress = progress or (lambda message: None)
    # True when Bungie could not be reached and a cached version is in use.
    self.offline = False
    self._metadata: dict | None = None
    self._version: str | None = None
    self._tables: dict[str, dict] = {}

  @property
  def version(self) -> str:
    '''Current manifest version, or the newest complete cached one if Bungie is
    unreachable.'''
    if self._version is None:
      try:
        version = self._fetch_metadata()['version']
      except requests.RequestException:
        cached_versions = sorted(
          (folder for folder in self._version_folders() if _is_complete(folder)),
          key=lambda folder: folder.stat().st_mtime,
        )
        if not cached_versions:
          raise
        self.offline = True
        version = cached_versions[-1].name
      if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
        raise ManifestError(f'Bungie sent an unexpected manifest version: {version!r}')
      self._version = version
    return self._version

  def table(self, name: str) -> dict:
    '''A trimmed definition table keyed by hash, downloading it if needed.'''
    if name not in self._tables:
      self.ensure_tables()
      path = self._table_path(name)
      self._tables[name] = json.loads(path.read_text(encoding='utf-8'))
    return self._tables[name]

  def ensure_tables(self, force: bool = False) -> str:
    '''Cache every table for the current version, then drop older versions.

    *force* downloads them again even when they are cached.
    '''
    folder = self.cache_directory / self.version
    if not force and _is_complete(folder):
      return self.version
    for name in TABLES_USED:
      path = self._table_path(name)
      if force or not path.exists():
        write_atomically(path, json.dumps(_trim(name, self._raw_table(name, force))))
    for stale_file in folder.iterdir():
      if stale_file.name not in {self._table_path(name).name for name in TABLES_USED}:
        stale_file.unlink()
    write_atomically(folder / COMPLETE_MARKER, json.dumps(_marker()))
    self._remove_old_versions()
    return self.version

  def _raw_table(self, name: str, force: bool) -> dict:
    '''The full table: from an untrimmed cache left by an older version of the
    tool, or downloaded.'''
    legacy_path = self.cache_directory / self.version / f'{name}.json'
    if legacy_path.exists() and not force:
      return json.loads(legacy_path.read_text(encoding='utf-8'))
    content_paths = self._fetch_metadata()['jsonWorldComponentContentPaths']
    url = urljoin(f'https://{CONTENT_HOST}', content_paths[LANGUAGE][name])
    parsed_url = urlparse(url)
    if parsed_url.scheme != 'https' or parsed_url.hostname != CONTENT_HOST:
      raise ManifestError(f'Bungie pointed the {name} download somewhere else: {url}')
    with self.session.get(url, timeout=300, stream=True) as response:
      response.raise_for_status()
      size = int(response.headers.get('Content-Length') or 0)
      self.progress(
        f'Downloading the {name} manifest table'
        + (f' ({size / 1_000_000:.0f} MB)' if size else '')
        + '...'
      )
      download_path = self.cache_directory / self.version / f'{name}.download'
      write_atomically(
        download_path, response.iter_content(chunk_size=DOWNLOAD_CHUNK_BYTES)
      )
    try:
      return json.loads(download_path.read_text(encoding='utf-8'))
    finally:
      download_path.unlink(missing_ok=True)

  def _fetch_metadata(self) -> dict:
    if self._metadata is None:
      response = self.session.get(MANIFEST_URL, timeout=60)
      response.raise_for_status()
      self._metadata = response.json()['Response']
    return self._metadata

  def _table_path(self, name: str) -> Path:
    return self.cache_directory / self.version / f'{name}.v{TRIM_VERSION}.json'

  def _version_folders(self) -> list[Path]:
    if not self.cache_directory.exists():
      return []
    return [child for child in self.cache_directory.iterdir() if child.is_dir()]

  def _remove_old_versions(self) -> None:
    for folder in self._version_folders():
      if folder.name != self.version:
        shutil.rmtree(folder)


def _marker() -> dict:
  return {'tables': list(TABLES_USED), 'trim_version': TRIM_VERSION}


def _is_complete(folder: Path) -> bool:
  '''Every table this version of the tool reads is cached in *folder*.'''
  try:
    marker = json.loads((folder / COMPLETE_MARKER).read_text())
  except (OSError, ValueError):
    return False
  return marker == _marker() and all(
    (folder / f'{name}.v{TRIM_VERSION}.json').exists() for name in TABLES_USED
  )


def _trim(name: str, table: dict) -> dict:
  '''Keep only the definitions and fields the tool reads.'''
  if name == 'DestinyInventoryItemDefinition':
    return {
      item_hash: _trim_item(definition) for item_hash, definition in table.items()
    }
  if name == 'DestinyInventoryBucketDefinition':
    return {
      bucket_hash: {'itemCount': definition.get('itemCount')}
      for bucket_hash, definition in table.items()
    }
  if name == 'DestinyPlugSetDefinition':
    return {
      plug_set_hash: {
        'reusablePlugItems': [
          {'plugItemHash': plug_item.get('plugItemHash')}
          for plug_item in definition.get('reusablePlugItems', [])
        ]
      }
      for plug_set_hash, definition in table.items()
    }
  if name == 'DestinyLoadoutNameDefinition':
    return {
      name_hash: {'name': definition.get('name', '')}
      for name_hash, definition in table.items()
    }
  return {
    definition_hash: {'displayProperties': {'name': definition_name(definition)}}
    for definition_hash, definition in table.items()
  }


def _trim_item(definition: dict) -> dict:
  '''Weapons, armor, and plugs (perks, mods, stat plugs) with what the rules
  read; everything else by name only.

  Names matter beyond gear: Voltron lists some weapons' rolls under a preview
  ("dummy") definition of the weapon, and matches them by name.
  '''
  item_type = definition.get('itemType')
  plug = definition.get('plug')
  trimmed: dict = {'displayProperties': {'name': definition_name(definition)}}
  if item_type not in (ITEM_TYPE_ARMOR, ITEM_TYPE_WEAPON) and plug is None:
    if item_type is not None:
      trimmed['itemType'] = item_type
    return trimmed
  for key in (
    'itemType',
    'itemTypeDisplayName',
    'classType',
    'isHolofoil',
    'isAdept',
    'nonTransferrable',
  ):
    if key in definition:
      trimmed[key] = definition[key]
  inventory = definition.get('inventory') or {}
  trimmed['inventory'] = {
    key: inventory[key] for key in ('bucketTypeHash', 'tierType') if key in inventory
  }
  if inventory.get('recipeItemHash'):
    # Craftable weapons: their crafting pattern.
    trimmed['inventory']['recipeItemHash'] = inventory['recipeItemHash']
  if item_type == ITEM_TYPE_ARMOR or plug is not None:
    trimmed['investmentStats'] = [
      {
        key: stat[key]
        for key in ('statTypeHash', 'value', 'isConditionallyActive')
        if key in stat
      }
      for stat in definition.get('investmentStats') or []
    ]
  set_hash = (definition.get('equippingBlock') or {}).get('equipableItemSetHash')
  if set_hash:
    trimmed['equippingBlock'] = {'equipableItemSetHash': set_hash}
  sockets = definition.get('sockets')
  if sockets and item_type in (ITEM_TYPE_ARMOR, ITEM_TYPE_WEAPON):
    trimmed['sockets'] = {
      'socketCategories': [
        {
          'socketCategoryHash': category.get('socketCategoryHash'),
          'socketIndexes': category.get('socketIndexes', []),
        }
        for category in sockets.get('socketCategories', [])
      ],
      'socketEntries': [
        {
          key: entry[key]
          for key in ('randomizedPlugSetHash', 'reusablePlugSetHash')
          if key in entry
        }
        for entry in sockets.get('socketEntries', [])
      ],
    }
  if plug is not None:
    trimmed['plug'] = {'plugCategoryIdentifier': plug.get('plugCategoryIdentifier', '')}
  return trimmed
