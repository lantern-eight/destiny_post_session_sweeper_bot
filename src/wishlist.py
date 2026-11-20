'''Your own weapon rolls from wishlist.yaml. Each roll keeps its best copy.'''

from __future__ import annotations

import io
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ruamel.yaml import YAML, YAMLError
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.constructor import DuplicateKeyError

from src.definitions import normalize_perk_name
from src.files import write_atomically
from src.game_data import (
  COLUMN_PLUG_CATEGORIES,
  MASTERWORK_COLUMN,
  MODE_NAMES,
  TRAIT_COLUMN_PREFIX,
  TRAIT_PLUG_CATEGORY,
)
from src.paths import WISHLIST_PATH
from src.weapons import ALTERNATIVES_SEPARATOR, alternatives, normalize_weapon_name

DUPLICATE_KEY_PATTERN = re.compile(r'found duplicate key "(?P<key>.*?)" with value')
TRAIT_COLUMN_PATTERN = re.compile(rf'{TRAIT_COLUMN_PREFIX}[1-9][0-9]*')
# Every column a roll can name, for suggestions when one is misspelled.
COLUMN_NAMES = (
  f'{TRAIT_COLUMN_PREFIX}1',
  f'{TRAIT_COLUMN_PREFIX}2',
  *COLUMN_PLUG_CATEGORIES,
  MASTERWORK_COLUMN,
)


class WishlistError(ValueError):
  '''wishlist.yaml is not shaped like the example at the top of the file.'''


@dataclass(frozen=True)
class WishlistEntry:
  label: str
  perks: tuple[str, ...]
  # When the roll was added and the game version then (Bungie's manifest
  # version), since perk pools and the meta change. Optional in the file.
  added: str = ''
  game_version: str = ''
  # pve or pvp, for a roll meant for one of them. Optional in the file.
  mode: str = ''
  # The column each perk is listed under, such as perk_1 or barrel ("" in the
  # older one-line layout). Only a label: perks match by name.
  columns: tuple[str, ...] = field(default=(), compare=False)
  # The masterwork stats the roll wants, any one of them, such as ("Range",).
  masterwork: tuple[str, ...] = ()

  @property
  def title(self) -> str:
    '''How messages name the roll: "add clear", "PvE", or "add clear (PvE)".'''
    if not self.mode or self.label == MODE_NAMES[self.mode]:
      return self.label
    return f'{self.label} ({MODE_NAMES[self.mode]})'

  def primary_perks(self, primary_perk_names: frozenset[str]) -> tuple[str, ...]:
    '''The perks a copy must have to match: the traits.

    The rest of the roll (origin trait, barrel, magazine, and the like) is
    secondary: optional, and it only ranks the copies that have the traits. A
    spot with choices ("Firefly | Rampage") counts as a trait when its choices
    are. A roll that lists no traits requires everything.
    '''
    primary = tuple(
      perk
      for perk in self.perks
      if any(choice in primary_perk_names for choice in alternatives(perk))
    )
    return primary or self.perks


def load_wishlist(path: Path = WISHLIST_PATH) -> dict[str, list[WishlistEntry]]:
  '''Rolls keyed by weapon name with variant suffixes removed.'''
  if not path.exists():
    return {}
  raw_wishlist = _read_document(path.read_text()) or {}
  if not isinstance(raw_wishlist, dict):
    raise WishlistError('wishlist.yaml must map weapon names to lists of rolls.')
  wishlist: dict[str, list[WishlistEntry]] = {}
  for weapon_name, raw_entries in raw_wishlist.items():
    if raw_entries is not None and not isinstance(raw_entries, list):
      raise WishlistError(f'{weapon_name} in wishlist.yaml must be a list of rolls.')
    entries = []
    for position, raw_entry in enumerate(raw_entries or [], start=1):
      roll = f'{weapon_name} roll {position}'
      raw_perks = raw_entry.get('perks') if isinstance(raw_entry, dict) else None
      perks, columns, masterwork = _read_perks(raw_perks, roll)
      mode = _mode(raw_entry.get('mode'), roll)
      entries.append(
        WishlistEntry(
          label=str(
            raw_entry.get('label') or MODE_NAMES.get(mode) or f'roll {position}'
          ),
          perks=perks,
          added=str(raw_entry.get('added') or ''),
          game_version=str(raw_entry.get('game_version') or ''),
          mode=mode,
          columns=columns,
          masterwork=masterwork,
        )
      )
    wishlist.setdefault(normalize_weapon_name(str(weapon_name)), []).extend(entries)
  return wishlist


def add_rolls(
  weapon_name: str, rolls: list[WishlistEntry], path: Path = WISHLIST_PATH
) -> list[WishlistEntry]:
  '''Add rolls under a weapon, keeping the file's comments and layout.

  A weapon new to the file goes at the end, after a blank line. Rolls whose
  perks are already listed for the weapon are not repeated. Returns the rolls
  that were added.
  '''
  round_trip = _round_trip_yaml()
  text = path.read_text() if path.exists() else ''
  document = _read_document(text)
  if document is not None and not isinstance(document, dict):
    raise WishlistError('wishlist.yaml must map weapon names to lists of rolls.')
  existing_key = next(
    (
      key
      for key in document or {}
      if normalize_weapon_name(str(key)) == normalize_weapon_name(weapon_name)
    ),
    None,
  )
  existing_entries = document.get(existing_key) if existing_key is not None else None
  if existing_entries is not None and not isinstance(existing_entries, list):
    raise WishlistError(f'{existing_key} in wishlist.yaml must be a list of rolls.')
  known_rolls = {
    frozenset(_read_perks(entry.get('perks'), f'{existing_key} roll {position}')[0])
    for position, entry in enumerate(existing_entries or [], start=1)
    if isinstance(entry, dict) and entry.get('perks')
  }
  new_rolls = []
  for roll in rolls:
    if frozenset(roll.perks) not in known_rolls:
      known_rolls.add(frozenset(roll.perks))
      new_rolls.append(roll)
  if not new_rolls:
    return []

  if existing_key is None:
    # Added as text, so the rest of the file stays as you wrote it: dumping the
    # whole file again would rewrap its long perk lists. (A file holding only
    # comments loads as nothing, and gets the same.)
    buffer = io.StringIO()
    round_trip.dump(CommentedMap({weapon_name: _roll_list(new_rolls)}), buffer)
    if not text or text.endswith('\n\n'):
      separator = ''
    else:
      separator = '\n' if text.endswith('\n') else '\n\n'
    write_atomically(path, f'{text}{separator}{buffer.getvalue()}')
    return new_rolls
  if existing_entries is None:
    existing_entries = CommentedSeq()
    document[existing_key] = existing_entries
  existing_entries.extend(_roll_list(new_rolls))
  buffer = io.StringIO()
  round_trip.dump(document, buffer)
  write_atomically(path, buffer.getvalue())
  return new_rolls


def column_labels(
  perks: Sequence[str], categories_by_perk: Mapping[str, frozenset[str]]
) -> tuple[str, ...]:
  '''The column each perk goes under, by the plug categories the game gives it
  (see perk_categories): perk_1, perk_2, and so on for the traits in order,
  then origin_trait, barrel, and the like. "" for a perk the game doesn't
  place.'''
  labels = []
  trait_count = 0
  for perk in perks:
    categories = frozenset().union(
      *(categories_by_perk.get(choice, frozenset()) for choice in alternatives(perk))
    )
    if TRAIT_PLUG_CATEGORY in categories:
      trait_count += 1
      labels.append(f'{TRAIT_COLUMN_PREFIX}{trait_count}')
      continue
    column = next(
      (
        column
        for column, column_categories in COLUMN_PLUG_CATEGORIES.items()
        if categories & frozenset(column_categories)
      ),
      '',
    )
    labels.append(column)
  return tuple(labels)


def column_plug_categories(column: str) -> tuple[str, ...] | None:
  '''The plug categories a column covers, or None for a name that is not a
  column. The masterwork is not a perk column.'''
  if TRAIT_COLUMN_PATTERN.fullmatch(column):
    return (TRAIT_PLUG_CATEGORY,)
  return COLUMN_PLUG_CATEGORIES.get(column)


def _read_perks(
  raw_perks: object, roll: str
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
  '''A roll's perks, the column each is listed under, and its masterwork.

  Each line is "column: name" or "column: [choice, choice]". A bare name, or
  "A | B" for choices, is read too: the older layout, with no column.
  '''
  if not raw_perks or not isinstance(raw_perks, list):
    raise WishlistError(f'{roll} needs a list of perks.')
  perks: list[str] = []
  columns: list[str] = []
  masterwork: tuple[str, ...] = ()
  for line in raw_perks:
    if isinstance(line, dict) and len(line) == 1:
      ((column, value),) = line.items()
      column = str(column)
    elif isinstance(line, str):
      column, value = '', line
    else:
      raise WishlistError(
        f'{roll} has {line!r} in its perks: write one column and its perk to a '
        'line, such as "- perk_1: Firefly".'
      )
    choices = _choices(value, column, roll)
    if column == MASTERWORK_COLUMN:
      masterwork = choices
    else:
      perks.append(
        ALTERNATIVES_SEPARATOR.join(normalize_perk_name(choice) for choice in choices)
      )
      columns.append(column)
  if not perks:
    raise WishlistError(f'{roll} needs a list of perks.')
  return tuple(perks), tuple(columns), masterwork


def _choices(value: object, column: str, roll: str) -> tuple[str, ...]:
  '''What one line accepts: a name, "A | B", or a list of names.'''
  values = value if isinstance(value, list) else [value]
  choices = tuple(
    choice for text in values if text is not None for choice in alternatives(str(text))
  )
  if not choices:
    raise WishlistError(f'{roll} has nothing under {column or "a perk"}.')
  return choices


def _mode(value: object, roll: str) -> str:
  '''pve, pvp, or "" when the roll does not say.'''
  if value is None or value == '':
    return ''
  mode = str(value).lower()
  if mode not in MODE_NAMES:
    raise WishlistError(f"{roll} has mode '{value}': use pve or pvp.")
  return mode


def _read_document(text: str) -> object:
  '''Parse wishlist.yaml, turning YAML mistakes into a WishlistError.

  One parser reads the file for both planning and the tie step, so they never
  disagree about what it says.
  '''
  if not text.strip():
    return None
  try:
    return _round_trip_yaml().load(text)
  except DuplicateKeyError as error:
    match = DUPLICATE_KEY_PATTERN.search(str(error.problem))
    key = match.group('key') if match else 'a weapon'
    lines = [
      str(mark.line + 1)
      for mark in (error.context_mark, error.problem_mark)
      if mark is not None
    ]
    raise WishlistError(
      f'wishlist.yaml lists {key} more than once (lines {" and ".join(lines)}). '
      'Merge its rolls under one entry.'
    ) from error
  except YAMLError as error:
    raise WishlistError(f'wishlist.yaml is not valid YAML: {error}') from error


def _round_trip_yaml() -> YAML:
  '''A YAML reader and writer that keeps comments, in the layout of the example.'''
  round_trip = YAML()
  round_trip.indent(mapping=2, sequence=4, offset=2)
  round_trip.preserve_quotes = True
  round_trip.width = 88
  return round_trip


def _roll_list(rolls: list[WishlistEntry]) -> CommentedSeq:
  entries = CommentedSeq()
  for roll in rolls:
    entry = CommentedMap()
    if roll.label != MODE_NAMES.get(roll.mode):
      entry['label'] = roll.label
    if roll.mode:
      entry['mode'] = roll.mode
    entry['perks'] = _perk_lines(roll)
    if roll.added:
      entry['added'] = date.fromisoformat(roll.added)  # written unquoted
    if roll.game_version:
      entry['game_version'] = roll.game_version
    entries.append(entry)
  return entries


def _perk_lines(roll: WishlistEntry) -> CommentedSeq:
  '''One line per column, as in "- perk_2: [Chaos Reshaped, Firefly]", then the
  masterwork. A perk with no column is written as its name alone.'''
  lines = CommentedSeq()
  columns = roll.columns or ('',) * len(roll.perks)
  for perk, column in zip(roll.perks, columns, strict=True):
    lines.append(
      CommentedMap({column: _line_value(alternatives(perk))}) if column else perk
    )
  if roll.masterwork:
    lines.append(CommentedMap({MASTERWORK_COLUMN: _line_value(roll.masterwork)}))
  return lines


def _line_value(choices: tuple[str, ...]) -> str | CommentedSeq:
  '''A name, or several choices as a list on the same line.'''
  if len(choices) == 1:
    return choices[0]
  value = CommentedSeq(choices)
  value.fa.set_flow_style()
  return value
