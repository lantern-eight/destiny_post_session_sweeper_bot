'''In-game loadouts: the gear each one uses and the mods applied to it.

A loadout keeps pointing at an item after it is dismantled, so sharding gear a
loadout uses breaks that loadout. The plan still shards it but calls it out,
listing the loadout's gear and mods so it can be rebuilt.
'''

from __future__ import annotations

from dataclasses import dataclass

from src.definitions import definition_name, plug_category
from src.game_data import (
  CLASS_NAMES,
  EMPTY_PLUG_PREFIX,
  MOD_PLUG_CATEGORY_PREFIXES,
  UNSET_HASH,
)


@dataclass(frozen=True)
class LoadoutGear:
  instance_id: str
  mods: tuple[str, ...]  # in socket order; a mod slotted twice is listed twice


@dataclass(frozen=True)
class Loadout:
  class_name: str
  number: int  # its slot in the game's loadout screen, from 1
  name: str
  gear: tuple[LoadoutGear, ...]  # includes the subclass

  @property
  def label(self) -> str:
    '''Names repeat (a Titan can have three "PvE" loadouts), so add the slot.'''
    return f'{self.class_name} loadout {self.number} ({self.name})'


def read_loadouts(
  profile: dict, item_definitions: dict, name_definitions: dict
) -> list[Loadout]:
  '''Every saved loadout on the account. Empty loadout slots are skipped.'''
  characters = profile.get('characters', {}).get('data', {})
  loadouts = []
  for character_id, block in (
    profile.get('characterLoadouts', {}).get('data', {}).items()
  ):
    class_name = CLASS_NAMES.get(characters.get(character_id, {}).get('classType'))
    if not class_name:
      continue
    for number, raw_loadout in enumerate(block.get('loadouts', []), start=1):
      gear = tuple(
        LoadoutGear(
          raw_item['itemInstanceId'],
          tuple(
            name
            for plug_hash in raw_item.get('plugItemHashes', [])
            if (name := _mod_name(plug_hash, item_definitions))
          ),
        )
        for raw_item in raw_loadout.get('items', [])
        if raw_item.get('itemInstanceId') not in (None, '', '0')
      )
      if gear:
        name_hash = raw_loadout.get('nameHash')
        name = name_definitions.get(str(name_hash), {}).get('name') or f'#{number}'
        loadouts.append(Loadout(class_name, number, name, gear))
  return loadouts


def _mod_name(plug_hash: int, item_definitions: dict) -> str:
  if plug_hash == UNSET_HASH:
    return ''
  definition = item_definitions.get(str(plug_hash), {})
  category = plug_category(definition)
  name = definition_name(definition)
  if not category.startswith(MOD_PLUG_CATEGORY_PREFIXES):
    return ''
  if not name or name.startswith(EMPTY_PLUG_PREFIX):
    return ''
  return name
