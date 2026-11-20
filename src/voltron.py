'''The community Voltron wishlist (DIM's default), cached for a day at a time.

Entries are converted from item and perk hashes to names, so they cover every
version of a weapon (Adept, shiny, reissues) and enhanced perks match their base.
A download that looks broken is refused in favor of the previous copy, and the
parsed rolls are cached so each plan does not parse 28 MB again.
'''

from __future__ import annotations

import hashlib
import json
import os
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import requests

from src.definitions import definition_name, normalize_perk_name, plug_category
from src.files import write_atomically
from src.game_data import TRAIT_PLUG_CATEGORIES
from src.http_session import make_session
from src.paths import VOLTRON_CACHE_PATH, VOLTRON_PARSED_CACHE_PATH
from src.weapons import normalize_weapon_name

VOLTRON_URL = (
  'https://raw.githubusercontent.com/48klocs/dim-wish-list-sources/master/voltron.txt'
)
MAXIMUM_AGE_SECONDS = 24 * 60 * 60
ENTRY_PREFIX = 'dimwishlist:'
PVE_WORDS = ('pve',)
PVP_WORDS = ('pvp', 'crucible', 'trials')
# A new download with fewer rolls than this share of the previous one is
# probably broken (a format change, a truncated file), so the old copy is kept.
MINIMUM_ENTRY_SHARE = 0.5
# Normally about 0.05% of Voltron's rolls name a perk or weapon the manifest
# does not know. Far more means the hashes changed under one of them. (Only
# checked on a wishlist big enough for the share to mean something.)
MAXIMUM_UNKNOWN_SHARE = 0.05
MINIMUM_ROLLS_TO_CHECK = 1000
# Bump when parsing changes, so the parsed cache is rebuilt.
PARSER_VERSION = 1


class VoltronError(RuntimeError):
  '''The Voltron wishlist could not be downloaded or read.'''


@dataclass(frozen=True)
class VoltronEntry:
  perks: tuple[str, ...]
  # Plug category of each perk, read from its hash: names alone are ambiguous
  # ("Demolitionist" is an armor archetype and a weapon trait).
  perk_categories: tuple[str, ...] = field(default=(), compare=False)

  @property
  def traits(self) -> tuple[str, ...]:
    '''The trait and origin-trait perks, in column order.'''
    return tuple(
      perk
      for perk, category in self._categorized()
      if category in TRAIT_PLUG_CATEGORIES
    )

  @property
  def parts(self) -> tuple[tuple[str, str], ...]:
    '''(plug category, perk) for everything else: barrels, magazines, and so on.'''
    return tuple(
      (category, perk)
      for perk, category in self._categorized()
      if category not in TRAIT_PLUG_CATEGORIES
    )

  def _categorized(self) -> list[tuple[str, str]]:
    categories = self.perk_categories or ('',) * len(self.perks)
    return list(zip(self.perks, categories, strict=True))


@dataclass(frozen=True)
class VoltronDownload:
  path: Path
  # Something to tell you, such as "the download looked broken, so the
  # previous copy is in use".
  note: str = ''


def ensure_voltron(
  cache_path: Path = VOLTRON_CACHE_PATH,
  session: requests.Session | None = None,
  now: float | None = None,
  refresh: bool = False,
  accept_smaller: bool = False,
  progress: Callable[[str], None] | None = None,
) -> VoltronDownload:
  '''The cached wishlist, downloaded again when missing, a day old, or on
  *refresh*.

  If the download fails or looks broken (far fewer rolls than before), the
  older copy is used with a note. *accept_smaller* takes the download anyway.
  '''
  current_time = time.time() if now is None else now
  if (
    not refresh
    and cache_path.exists()
    and current_time - cache_path.stat().st_mtime < MAXIMUM_AGE_SECONDS
  ):
    return VoltronDownload(cache_path)
  if progress:
    progress('Downloading the Voltron wishlist (about 30 MB)...')
  try:
    response = (session or make_session()).get(VOLTRON_URL, timeout=120)
    response.raise_for_status()
  except requests.RequestException as error:
    if cache_path.exists():
      return VoltronDownload(
        cache_path,
        f'Could not download a fresh Voltron wishlist ({error}); using the copy '
        f'from {_file_date(cache_path)}.',
      )
    raise VoltronError(f'Could not download the Voltron wishlist: {error}') from error
  new_count = _entry_count(response.content)
  old_count = _entry_count(cache_path.read_bytes()) if cache_path.exists() else 0
  too_small = new_count < old_count * MINIMUM_ENTRY_SHARE and not accept_smaller
  if new_count == 0 or too_small:
    if not cache_path.exists():
      raise VoltronError('The Voltron download has no rolls in it.')
    # Keep the old copy, and wait a day before trying again.
    os.utime(cache_path, (current_time, current_time))
    return VoltronDownload(
      cache_path,
      f'The new Voltron wishlist has {new_count:,} rolls, under half of the '
      f'{old_count:,} before, so the previous copy is still in use. If Voltron '
      'really shrank, run `uv run sweeper update-data --force`.',
    )
  write_atomically(cache_path, response.content)
  return VoltronDownload(cache_path)


def load_voltron_entries(
  path: Path,
  item_definitions: dict,
  modes: tuple[str, ...],
  manifest_version: str = '',
  parsed_cache_path: Path = VOLTRON_PARSED_CACHE_PATH,
) -> dict[str, list[VoltronEntry]]:
  '''Voltron rolls keyed by weapon name, as perk names, for the given modes.

  Entries marked only for another mode are dropped; unmarked entries are kept.
  With a *manifest_version*, the result is cached until the file, the
  manifest, or the modes change. Raises VoltronError when far more rolls than
  usual name perks the manifest does not know.
  '''
  content = path.read_bytes()
  cache_key = ''
  if manifest_version:
    cache_key = ':'.join(
      (
        str(PARSER_VERSION),
        hashlib.sha256(content).hexdigest(),
        manifest_version,
        ','.join(sorted(modes)),
      )
    )
    cached = _read_parsed_cache(parsed_cache_path, cache_key)
    if cached is not None:
      return cached
  entries, unknown_count, total_count = _parse(
    content.decode('utf-8'), item_definitions, modes
  )
  if (
    total_count >= MINIMUM_ROLLS_TO_CHECK
    and unknown_count / total_count > MAXIMUM_UNKNOWN_SHARE
  ):
    raise VoltronError(
      f'{unknown_count:,} of {total_count:,} Voltron rolls name weapons or perks '
      "this manifest doesn't know, far more than usual, so Voltron can't be "
      'trusted right now. Run `uv run sweeper update-data`; if that does not '
      'help, Bungie or Voltron changed their hashes and it should settle in a day.'
    )
  if cache_key:
    write_atomically(
      parsed_cache_path,
      json.dumps(
        {
          'key': cache_key,
          'entries': {
            weapon_name: [
              [list(entry.perks), list(entry.perk_categories)]
              for entry in weapon_entries
            ]
            for weapon_name, weapon_entries in entries.items()
          },
        }
      ),
    )
  return entries


def classify_mode(*texts: str) -> str:
  '''pve, pvp, or any, from the first text that mentions a mode.'''
  for text in texts:
    lowered = text.lower()
    mentions_pve = any(word in lowered for word in PVE_WORDS)
    mentions_pvp = any(word in lowered for word in PVP_WORDS)
    if mentions_pve and mentions_pvp:
      return 'any'
    if mentions_pve:
      return 'pve'
    if mentions_pvp:
      return 'pvp'
  return 'any'


def _parse(
  text: str, item_definitions: dict, modes: tuple[str, ...]
) -> tuple[dict[str, list[VoltronEntry]], int, int]:
  '''(entries, rolls naming unknown hashes, all rolls).

  Notes come from the entry itself, else its block's //notes: line, else the
  block's heading comment, else the section title.
  '''
  entries: dict[str, list[VoltronEntry]] = defaultdict(list)
  seen_rolls: dict[str, set[tuple[str, ...]]] = defaultdict(set)
  title = block_heading = block_notes = ''
  unknown_count = total_count = 0
  for raw_line in text.splitlines():
    line = raw_line.strip()
    if not line:
      block_heading = block_notes = ''
    elif line.startswith('title:'):
      title, block_heading, block_notes = line[len('title:') :], '', ''
    elif line.startswith('//notes:'):
      block_notes = line[len('//notes:') :]
    elif line.startswith('//'):
      block_heading = block_heading or line[2:]
    elif line.startswith(ENTRY_PREFIX):
      total_count += 1
      parsed = _parse_entry(line[len(ENTRY_PREFIX) :], item_definitions)
      if parsed is None:
        unknown_count += 1
        continue
      if not parsed:
        continue  # trash lists and wildcards
      weapon_name, perks, categories, inline_notes = parsed
      mode = classify_mode(inline_notes, block_notes, block_heading, title)
      if mode != 'any' and mode not in modes:
        continue
      if perks in seen_rolls[weapon_name]:
        continue
      seen_rolls[weapon_name].add(perks)
      entries[weapon_name].append(VoltronEntry(perks, categories))
  return dict(entries), unknown_count, total_count


def _parse_entry(
  entry_text: str, item_definitions: dict
) -> tuple[str, tuple[str, ...], tuple[str, ...], str] | tuple[()] | None:
  '''(weapon name, perk names, perk categories, inline notes); () for entries
  that are not rolls; None when the manifest does not know a hash.'''
  body, _, inline_notes = entry_text.partition('#notes:')
  fields = dict(part.split('=', 1) for part in body.split('&') if '=' in part)
  item_hash = fields.get('item', '')
  if not item_hash or item_hash.startswith('-'):
    return ()  # trash lists and wildcards; Voltron has none today
  item_definition = item_definitions.get(item_hash)
  if not item_definition:
    return None
  perk_names = []
  perk_categories = []
  for perk_hash in fields.get('perks', '').split(','):
    perk_definition = item_definitions.get(perk_hash)
    if not perk_definition:
      return None  # a perk missing from the manifest can never match
    perk_names.append(normalize_perk_name(definition_name(perk_definition)))
    perk_categories.append(plug_category(perk_definition))
  weapon_name = normalize_weapon_name(definition_name(item_definition))
  return weapon_name, tuple(perk_names), tuple(perk_categories), inline_notes


def _read_parsed_cache(
  path: Path, cache_key: str
) -> dict[str, list[VoltronEntry]] | None:
  try:
    cached = json.loads(path.read_text(encoding='utf-8'))
  except (OSError, ValueError):
    return None
  if not isinstance(cached, dict) or cached.get('key') != cache_key:
    return None
  return {
    weapon_name: [
      VoltronEntry(tuple(perks), tuple(categories)) for perks, categories in rolls
    ]
    for weapon_name, rolls in cached['entries'].items()
  }


def _entry_count(content: bytes) -> int:
  return content.count(b'\n' + ENTRY_PREFIX.encode()) + content.startswith(
    ENTRY_PREFIX.encode()
  )


def _file_date(path: Path) -> str:
  return datetime.fromtimestamp(path.stat().st_mtime).strftime('%Y-%m-%d')
