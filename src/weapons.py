'''Turn a raw Bungie profile plus manifest definitions into weapons.'''

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from src.definitions import (
  definition_name,
  is_masterwork,
  masterwork_stat,
  perk_name,
  plug_category,
  rarity_of,
)
from src.game_data import (
  ENHANCER_PLUG_CATEGORY,
  FRAME_PLUG_CATEGORY,
  INTRINSIC_TRAITS_SOCKET_CATEGORY_HASH,
  ITEM_STATE_CRAFTED,
  ITEM_STATE_LOCKED,
  ITEM_STATE_MASTERWORKED,
  ITEM_TYPE_WEAPON,
  NON_PERK_PLUG_CATEGORY_FRAGMENTS,
  PAIRED_EXOTIC_WEAPONS,
  WEAPON_PERKS_SOCKET_CATEGORY_HASH,
  WEAPON_SLOTS_BY_BUCKET_HASH,
  WEAPON_VARIANT_SUFFIXES,
)
from src.inventory import iter_owned_gear

VARIANT_SUFFIX_PATTERN = re.compile(rf'\s*\(({"|".join(WEAPON_VARIANT_SUFFIXES)})\)$')
# Choices for one spot in a roll, as in "Chaos Reshaped | Firefly".
ALTERNATIVES_SEPARATOR = ' | '


def normalize_weapon_name(name: str) -> str:
  '''Fold Adept, Timelost, Harrowed, and Baroque versions into one name.'''
  return VARIANT_SUFFIX_PATTERN.sub('', name).strip()


def alternatives(perk: str) -> tuple[str, ...]:
  '''The perks one spot of a roll accepts: "Chaos Reshaped | Firefly" is
  either, and a plain name is just that perk.'''
  return tuple(choice.strip() for choice in perk.split('|') if choice.strip())


@dataclass(frozen=True)
class Weapon:
  instance_id: str
  name: str
  group_name: str
  weapon_type: str
  slot: str
  rarity: str  # white | green | blue | legendary | exotic
  tier: int
  is_shiny: bool
  is_adept: bool
  is_crafted: bool
  is_masterworked: bool
  is_locked: bool
  # Perks available in each column: the roll's options, or for crafted copies
  # every perk they could be reshaped into.
  perk_columns: tuple[tuple[str, ...], ...]
  selected_perks: tuple[str, ...]
  location: str
  # Plug category of each perk column, such as barrels, magazines, or frames.
  column_categories: tuple[str, ...] = ()
  # This version of the weapon has a crafting pattern. Other versions of a
  # craftable weapon (a reissue, most Adept ones) may not.
  is_craftable: bool = False
  # Ergo Sum and the like: the random frame and exotic trait this copy rolled.
  exotic_pair: tuple[str, ...] = ()
  # Enhanced at the Enclave: Bungie flags it crafted, but it keeps its own
  # perk options like a random roll.
  is_enhanced: bool = False
  # The masterwork stat the copy has ("Stability"), and every stat it can
  # have: a random roll keeps the one it rolled, while a crafted or enhanced
  # copy can take any.
  masterwork: str = ''
  masterwork_options: tuple[str, ...] = ()

  @property
  def group_key(self) -> tuple[str, bool]:
    '''Copies of the same weapon compete; shiny versions compete separately.'''
    return (self.group_name, self.is_shiny)

  @property
  def option_count(self) -> int:
    return sum(len(column) for column in self.perk_columns)

  def can_roll(self, perks: Sequence[str]) -> bool:
    '''Whether every perk can be active at once, each from its own column.

    A perk written "A | B" is either one.
    '''
    return self.matchable_perk_count(perks) == len(perks)

  def can_have_masterwork(self, stats: Sequence[str]) -> bool:
    '''Whether the copy has, or can take, one of these masterwork stats.'''
    options = {option.casefold() for option in self.masterwork_options}
    return any(stat.casefold() in options for stat in stats)

  def matchable_perk_count(self, perks: Sequence[str]) -> int:
    '''Most of *perks* that can be active at once, one perk per column.'''
    return _max_assignment(
      [frozenset(alternatives(perk)) for perk in perks],
      [set(column) for column in self.perk_columns],
    )


def _max_assignment(perks: list[frozenset[str]], columns: list[set[str]]) -> int:
  '''Largest number of perks placed in distinct columns (tiny search space).

  Each perk is the set of names it accepts.
  '''
  if not perks:
    return 0
  first, rest = perks[0], perks[1:]
  best = _max_assignment(rest, columns)
  for index, column in enumerate(columns):
    if first & column:
      remaining_columns = columns[:index] + columns[index + 1 :]
      best = max(best, 1 + _max_assignment(rest, remaining_columns))
      if best == len(perks):
        break
  return best


def build_weapons(
  profile: dict, item_definitions: dict, plug_set_definitions: dict
) -> list[Weapon]:
  weapons = []
  for gear in iter_owned_gear(profile, item_definitions, ITEM_TYPE_WEAPON):
    definition = gear.definition
    state = gear.item.get('state', 0)
    # Bungie flags enhanced copies as crafted too, but only a crafted (shaped)
    # copy can be reshaped into any perk of its pattern.
    is_enhanced = _has_enhancement(gear.sockets, item_definitions)
    is_crafted = bool(state & ITEM_STATE_CRAFTED) and not is_enhanced
    selected_perks, perk_columns, column_categories = _perk_columns(
      definition,
      gear.sockets,
      gear.reusable_plugs,
      item_definitions,
      plug_set_definitions,
      is_crafted,
    )
    masterwork, masterwork_options = _masterwork(
      gear.sockets, gear.reusable_plugs, item_definitions
    )
    name = definition_name(definition) or '?'
    group_name = normalize_weapon_name(name)
    weapons.append(
      Weapon(
        instance_id=gear.instance_id,
        name=name,
        group_name=group_name,
        weapon_type=definition.get('itemTypeDisplayName', ''),
        slot=WEAPON_SLOTS_BY_BUCKET_HASH.get(
          definition.get('inventory', {}).get('bucketTypeHash'), '?'
        ),
        # An unknown rarity counts as white: always kept.
        rarity=rarity_of(definition) or 'white',
        tier=gear.gear_tier,
        is_shiny=bool(definition.get('isHolofoil')),
        is_adept=bool(definition.get('isAdept')),
        is_crafted=is_crafted,
        is_masterworked=bool(state & ITEM_STATE_MASTERWORKED),
        is_locked=bool(state & ITEM_STATE_LOCKED),
        perk_columns=perk_columns,
        selected_perks=selected_perks,
        location=gear.location,
        column_categories=column_categories,
        is_craftable=bool(definition.get('inventory', {}).get('recipeItemHash')),
        exotic_pair=(
          _frame_and_trait(definition, gear.sockets, item_definitions)
          if group_name in PAIRED_EXOTIC_WEAPONS
          else ()
        ),
        is_enhanced=is_enhanced,
        masterwork=masterwork,
        masterwork_options=masterwork_options,
      )
    )
  return weapons


def _masterwork(
  item_sockets: list[dict],
  reusable_plugs_by_socket: dict[str, list[dict]],
  item_definitions: dict,
) -> tuple[str, tuple[str, ...]]:
  '''The masterwork stat a copy has, and every stat its masterwork socket
  offers. ("", ()) for a weapon without one, such as an exotic.'''
  for socket_index, socket in enumerate(item_sockets):
    plugged = item_definitions.get(str(socket.get('plugHash')), {})
    if not is_masterwork(plugged):
      continue
    current = masterwork_stat(plugged)
    options = [current] if current else []
    for option in reusable_plugs_by_socket.get(str(socket_index), []):
      stat = masterwork_stat(item_definitions.get(str(option.get('plugItemHash')), {}))
      if stat and stat not in options:
        options.append(stat)
    return current, tuple(options)
  return '', ()


def _has_enhancement(item_sockets: list[dict], item_definitions: dict) -> bool:
  '''Whether the copy holds an enhancement such as "Enhancement 1", not the
  empty socket of a weapon that could be enhanced.'''
  plugs = (
    item_definitions.get(str(socket.get('plugHash')), {}) for socket in item_sockets
  )
  return any(
    plug_category(plug) == ENHANCER_PLUG_CATEGORY and perk_name(plug) for plug in plugs
  )


def _frame_and_trait(
  definition: dict, item_sockets: list[dict], item_definitions: dict
) -> tuple[str, ...]:
  '''The random frame and exotic trait a copy rolled, or () if unreadable.

  The frame is the randomly rolled frame perk (Caster Frame); the exotic trait
  sits in the intrinsic socket (Arc Conductor). Blades and guards don't count.
  '''
  sockets_block = definition.get('sockets', {})
  socket_entries = sockets_block.get('socketEntries', [])
  socket_indexes = {
    category.get('socketCategoryHash'): category.get('socketIndexes', [])
    for category in sockets_block.get('socketCategories', [])
  }

  def plugged(socket_index: int) -> dict:
    if socket_index >= len(item_sockets):
      return {}
    plug_hash = item_sockets[socket_index].get('plugHash')
    return item_definitions.get(str(plug_hash), {})

  frame = next(
    (
      perk_name(plugged(socket_index))
      for socket_index in socket_indexes.get(WEAPON_PERKS_SOCKET_CATEGORY_HASH, [])
      if socket_index < len(socket_entries)
      and 'randomizedPlugSetHash' in socket_entries[socket_index]
      and plug_category(plugged(socket_index)) == FRAME_PLUG_CATEGORY
    ),
    '',
  )
  trait = next(
    (
      perk_name(plugged(socket_index))
      for socket_index in socket_indexes.get(INTRINSIC_TRAITS_SOCKET_CATEGORY_HASH, [])
      if perk_name(plugged(socket_index))
    ),
    '',
  )
  return (frame, trait) if frame and trait else ()


def _perk_columns(
  definition: dict,
  item_sockets: list[dict],
  reusable_plugs_by_socket: dict[str, list[dict]],
  item_definitions: dict,
  plug_set_definitions: dict,
  is_crafted: bool,
) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...], tuple[str, ...]]:
  '''Selected perk, every option, and plug category for each perk column.'''
  sockets_block = definition.get('sockets', {})
  perk_socket_indexes = next(
    (
      category.get('socketIndexes', [])
      for category in sockets_block.get('socketCategories', [])
      if category.get('socketCategoryHash') == WEAPON_PERKS_SOCKET_CATEGORY_HASH
    ),
    [],
  )
  socket_entries = sockets_block.get('socketEntries', [])
  selected_perks = []
  perk_columns = []
  column_categories = []
  for socket_index in perk_socket_indexes:
    if socket_index >= len(item_sockets):
      continue
    plugged = item_definitions.get(str(item_sockets[socket_index].get('plugHash')), {})
    category = plug_category(plugged)
    if not plugged or any(
      part in category for part in NON_PERK_PLUG_CATEGORY_FRAGMENTS
    ):
      continue
    selected = perk_name(plugged)
    if is_crafted:
      entry = socket_entries[socket_index] if socket_index < len(socket_entries) else {}
      pool_hash = entry.get('randomizedPlugSetHash') or entry.get('reusablePlugSetHash')
      pool = plug_set_definitions.get(str(pool_hash), {}).get('reusablePlugItems', [])
      option_hashes = [plug_item.get('plugItemHash') for plug_item in pool]
    else:
      options = reusable_plugs_by_socket.get(str(socket_index), [])
      option_hashes = [option.get('plugItemHash') for option in options]
    column = [selected] if selected else []
    for option_hash in option_hashes:
      option_name = perk_name(item_definitions.get(str(option_hash), {}))
      if option_name and option_name not in column:
        column.append(option_name)
    if column:
      selected_perks.append(selected or column[0])
      perk_columns.append(tuple(column))
      column_categories.append(category)
  return tuple(selected_perks), tuple(perk_columns), tuple(column_categories)
