'''Fields read from Bungie manifest definitions, in one place.'''

from __future__ import annotations

from collections import defaultdict

from src.game_data import (
  EMPTY_PLUG_PREFIX,
  ENHANCED_PERK_SUFFIX,
  MASTERWORK_PLUG_CATEGORY_FRAGMENT,
  NON_PERK_PLUG_CATEGORY_FRAGMENTS,
  RARITIES_BY_TIER_TYPE,
)


def definition_name(definition: dict) -> str:
  return definition.get('displayProperties', {}).get('name', '')


def plug_category(definition: dict) -> str:
  '''A plug's category, such as "frames" for traits or "barrels".'''
  return definition.get('plug', {}).get('plugCategoryIdentifier', '')


def rarity_of(definition: dict) -> str | None:
  '''white, green, blue, legendary, or exotic.'''
  return RARITIES_BY_TIER_TYPE.get(definition.get('inventory', {}).get('tierType'))


def normalize_perk_name(name: str) -> str:
  '''Enhanced perks mostly share the base name; strip the rare explicit suffix.'''
  if name.endswith(ENHANCED_PERK_SUFFIX):
    return name[: -len(ENHANCED_PERK_SUFFIX)]
  return name


def perk_name(definition: dict) -> str:
  '''Normalized perk name, or "" for empty sockets and unknown plugs.'''
  name = definition_name(definition)
  if not name or name.startswith(EMPTY_PLUG_PREFIX):
    return ''
  return normalize_perk_name(name)


def perk_categories(item_definitions: dict) -> dict[str, frozenset[str]]:
  '''Every plug name, normalized, and the plug categories it comes in. Names
  repeat across categories: "Demolitionist" is an armor archetype and a weapon
  trait.'''
  categories: dict[str, set[str]] = defaultdict(set)
  for definition in item_definitions.values():
    if 'plug' in definition and definition_name(definition):
      name = normalize_perk_name(definition_name(definition))
      categories[name].add(plug_category(definition))
  return {name: frozenset(found) for name, found in categories.items()}


def is_masterwork(definition: dict) -> bool:
  '''Whether a plug sits in a weapon's masterwork socket. The kill tracker's
  category shares the prefix.'''
  category = plug_category(definition)
  return MASTERWORK_PLUG_CATEGORY_FRAGMENT in category and not any(
    part in category for part in NON_PERK_PLUG_CATEGORY_FRAGMENTS
  )


def masterwork_stat(definition: dict) -> str:
  '''The stat a masterwork plug gives: "Tier 3: Stability" is Stability. ""
  for an unnamed masterwork or any other plug.'''
  if not is_masterwork(definition):
    return ''
  _, separator, stat = definition_name(definition).partition(': ')
  return stat if separator else ''
