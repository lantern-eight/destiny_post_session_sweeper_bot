'''Turn a raw Bungie profile plus manifest definitions into armor pieces.'''

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from src.definitions import definition_name, perk_name, plug_category, rarity_of
from src.game_data import (
  ARCHETYPE_PLUG_CATEGORY,
  ARCHETYPE_STATS,
  ARMOR_SLOTS_BY_BUCKET_HASH,
  BASE_STAT_PLUG_CATEGORIES,
  CLASS_NAMES,
  EXOTIC_CLASS_ITEM_PERK_PREFIX,
  INTRINSIC_PLUG_CATEGORY,
  ITEM_STATE_LOCKED,
  ITEM_STATE_MASTERWORKED,
  ITEM_TYPE_ARMOR,
  LAST_WISH_MOD_PLUG_CATEGORY,
  LOW_RARITIES,
  POSTMASTER_BUCKET_HASH,
  RAID_MOD_PLUG_CATEGORY_PREFIX,
  STAT_DISPLAY_ORDER,
  STAT_NAMES,
  STAT_NAMES_BY_HASH,
  TUNING_BONUS,
  TUNING_PLUG_CATEGORY,
  VAULT_BUCKET_HASH,
)

# "Vault of Glass Armor Mod", "Last Wish Raid Mod", "King's Fall Mod"
RAID_MOD_TYPE_SUFFIX = re.compile(r'\s+(?:Raid |Armor )?Mod$')


@dataclass(frozen=True)
class OwnedItem:
  item: dict
  location: str  # vault | inventory | equipped | postmaster | profile
  character_id: str | None


@dataclass(frozen=True)
class OwnedGear:
  '''A weapon or armor piece you own, with its definition and instance data.'''

  instance_id: str
  item: dict  # the profile's entry: hash, state, bucket
  definition: dict
  location: str
  sockets: list[dict]
  reusable_plugs: dict[str, list[dict]]  # socket index -> the options it offers
  gear_tier: int


@dataclass(frozen=True)
class ArmorPiece:
  instance_id: str
  name: str
  class_name: str
  slot: str
  rarity: str  # white | green | blue | legendary | exotic
  tier: int  # 0 means legacy (pre-Edge of Fate) armor
  archetype: str | None
  base_stats: dict[str, int]
  tuned_stat: str | None
  set_name: str | None
  raid: str | None  # the raid whose mod socket it has, such as Vault of Glass
  perks: tuple[str, ...]  # named armor perks, such as Iron Lord's Pride
  exotic_perks: tuple[str, ...]  # the two "Spirit of" perks on exotic class items
  is_locked: bool
  is_masterworked: bool
  location: str

  @property
  def is_exotic(self) -> bool:
    return self.rarity == 'exotic'

  @property
  def is_low_rarity(self) -> bool:
    '''White, green, or blue: never competes with legendary armor.'''
    return self.rarity in LOW_RARITIES

  @property
  def base_total(self) -> int:
    return sum(self.base_stats.values())

  @property
  def is_legacy(self) -> bool:
    return self.tier == 0

  @property
  def ordered_stats(self) -> tuple[str, ...]:
    '''Primary, secondary, and tertiary stat.

    Legacy pieces have no archetype, so their three highest stats stand in.
    '''
    if self.archetype in ARCHETYPE_STATS:
      primary, secondary = ARCHETYPE_STATS[self.archetype]
      tertiary = [
        stat
        for stat in STAT_NAMES
        if self.base_stats.get(stat, 0) > 0 and stat not in (primary, secondary)
      ]
      return (primary, secondary, *tertiary[:1])
    by_value = sorted(STAT_NAMES, key=lambda stat: -self.base_stats.get(stat, 0))
    return tuple(by_value[:3])

  @property
  def display_stats(self) -> dict[str, int]:
    '''All six base stats in the order the game lists them, 0 where nothing
    rolled. Masterwork, mods, and tuning are not included.'''
    return {stat: self.base_stats.get(stat, 0) for stat in STAT_DISPLAY_ORDER}

  def reachable_value(self, stat: str) -> int:
    '''Highest value this piece can show in *stat*: T5 tuning adds 5.'''
    return self.base_stats.get(stat, 0) + self.tuning_bonus(stat)

  def tuning_bonus(self, stat: str) -> int:
    return TUNING_BONUS if self.tier == 5 and self.tuned_stat == stat else 0


def iter_owned_items(profile: dict) -> Iterator[OwnedItem]:
  '''Every item on the account with where it currently sits.'''
  profile_items = profile.get('profileInventory', {}).get('data', {}).get('items', [])
  for item in profile_items:
    location = 'vault' if item.get('bucketHash') == VAULT_BUCKET_HASH else 'profile'
    yield OwnedItem(item, location, None)
  inventories = profile.get('characterInventories', {}).get('data', {})
  for character_id, inventory in inventories.items():
    for item in inventory.get('items', []):
      in_postmaster = item.get('bucketHash') == POSTMASTER_BUCKET_HASH
      location = 'postmaster' if in_postmaster else 'inventory'
      yield OwnedItem(item, location, character_id)
  equipment = profile.get('characterEquipment', {}).get('data', {})
  for character_id, equipped in equipment.items():
    for item in equipped.get('items', []):
      yield OwnedItem(item, 'equipped', character_id)


def locked_instance_ids(profile: dict) -> set[str]:
  '''Every item instance the profile shows as locked.'''
  return {
    owned.item['itemInstanceId']
    for owned in iter_owned_items(profile)
    if owned.item.get('itemInstanceId')
    and owned.item.get('state', 0) & ITEM_STATE_LOCKED
  }


def iter_owned_gear(
  profile: dict, item_definitions: dict, item_type: int
) -> Iterator[OwnedGear]:
  '''Every weapon or armor piece (by *item_type*) you own, with its definition.

  Items missing from the cached manifest are skipped.
  '''
  item_components = profile.get('itemComponents', {})
  instances = item_components.get('instances', {}).get('data', {})
  sockets = item_components.get('sockets', {}).get('data', {})
  reusable_plugs = item_components.get('reusablePlugs', {}).get('data', {})
  for owned in iter_owned_items(profile):
    instance_id = owned.item.get('itemInstanceId')
    definition = item_definitions.get(str(owned.item.get('itemHash')))
    if not instance_id or not definition or definition.get('itemType') != item_type:
      continue
    yield OwnedGear(
      instance_id=instance_id,
      item=owned.item,
      definition=definition,
      location=owned.location,
      sockets=sockets.get(instance_id, {}).get('sockets', []),
      reusable_plugs=reusable_plugs.get(instance_id, {}).get('plugs', {}),
      gear_tier=instances.get(instance_id, {}).get('gearTier') or 0,
    )


def build_armor_pieces(
  profile: dict, item_definitions: dict, set_definitions: dict
) -> list[ArmorPiece]:
  pieces = []
  for gear in iter_owned_gear(profile, item_definitions, ITEM_TYPE_ARMOR):
    definition = gear.definition
    slot = ARMOR_SLOTS_BY_BUCKET_HASH.get(
      definition.get('inventory', {}).get('bucketTypeHash')
    )
    class_name = CLASS_NAMES.get(definition.get('classType'))
    if not slot or not class_name:
      continue
    plugs = _plugged_sockets(gear.sockets, item_definitions)
    state = gear.item.get('state', 0)
    pieces.append(
      ArmorPiece(
        instance_id=gear.instance_id,
        name=definition_name(definition) or '?',
        class_name=class_name,
        slot=slot,
        # An unknown rarity competes like legendary armor.
        rarity=rarity_of(definition) or 'legendary',
        tier=gear.gear_tier,
        archetype=_archetype(plugs),
        base_stats=_base_stats(definition, plugs),
        tuned_stat=_tuned_stat(plugs, gear.reusable_plugs, item_definitions),
        set_name=_set_name(definition, set_definitions),
        raid=_raid(plugs),
        perks=_perks(plugs),
        exotic_perks=_exotic_perks(plugs),
        is_locked=bool(state & ITEM_STATE_LOCKED),
        is_masterworked=bool(state & ITEM_STATE_MASTERWORKED),
        location=gear.location,
      )
    )
  return pieces


def _plugged_sockets(
  item_sockets: list[dict], item_definitions: dict
) -> list[tuple[int, dict]]:
  '''(socket index, plug definition) for every socket with something plugged.'''
  plugged = []
  for socket_index, socket in enumerate(item_sockets):
    plug_hash = socket.get('plugHash')
    if plug_hash:
      plugged.append((socket_index, item_definitions.get(str(plug_hash), {})))
  return plugged


def _archetype(plugs: list[tuple[int, dict]]) -> str | None:
  for _, plug_definition in plugs:
    if plug_category(plug_definition) == ARCHETYPE_PLUG_CATEGORY:
      return definition_name(plug_definition) or None
  return None


def _base_stats(definition: dict, plugs: list[tuple[int, dict]]) -> dict[str, int]:
  '''Rolled stats, without masterwork, mods, or tuning.'''
  totals = dict.fromkeys(STAT_NAMES, 0)
  investment_lists = [definition.get('investmentStats', [])]
  investment_lists += [
    plug_definition.get('investmentStats', [])
    for _, plug_definition in plugs
    if plug_category(plug_definition) in BASE_STAT_PLUG_CATEGORIES
  ]
  for investment_stats in investment_lists:
    for investment in investment_stats:
      stat = STAT_NAMES_BY_HASH.get(investment.get('statTypeHash'))
      if stat and not investment.get('isConditionallyActive'):
        totals[stat] += investment.get('value', 0)
  return totals


def _tuned_stat(
  plugs: list[tuple[int, dict]],
  reusable_plugs_by_socket: dict[str, list[dict]],
  item_definitions: dict,
) -> str | None:
  '''The one stat this piece's tuning mods can raise.

  Only T5 legendary armor has one. Lower tiers offer just Balanced Tuning, and
  exotics offer every direction, so both return None.
  '''
  for socket_index, plug_definition in plugs:
    if plug_category(plug_definition) != TUNING_PLUG_CATEGORY:
      continue
    raised_stats = set()
    for option in reusable_plugs_by_socket.get(str(socket_index), []):
      option_definition = item_definitions.get(str(option.get('plugItemHash')), {})
      for investment in option_definition.get('investmentStats', []):
        stat = STAT_NAMES_BY_HASH.get(investment.get('statTypeHash'))
        is_raise = investment.get('value', 0) > 0
        if stat and is_raise and not investment.get('isConditionallyActive'):
          raised_stats.add(stat)
    return raised_stats.pop() if len(raised_stats) == 1 else None
  return None


def _set_name(definition: dict, set_definitions: dict) -> str | None:
  set_hash = definition.get('equippingBlock', {}).get('equipableItemSetHash')
  if not set_hash:
    return None
  set_definition = set_definitions.get(str(set_hash), {})
  return definition_name(set_definition) or str(set_hash)


def _raid(plugs: list[tuple[int, dict]]) -> str | None:
  '''The raid whose mod socket this piece has, named after the socket's mods.'''
  for _, plug_definition in plugs:
    category = plug_category(plug_definition)
    if category.startswith(RAID_MOD_PLUG_CATEGORY_PREFIX) or (
      category == LAST_WISH_MOD_PLUG_CATEGORY
    ):
      mod_type = plug_definition.get('itemTypeDisplayName', '')
      return RAID_MOD_TYPE_SUFFIX.sub('', mod_type) or category
  return None


def _perks(plugs: list[tuple[int, dict]]) -> tuple[str, ...]:
  '''Named perks in the piece's intrinsic sockets.'''
  return tuple(
    perk_name(plug_definition)
    for _, plug_definition in plugs
    if plug_category(plug_definition) == INTRINSIC_PLUG_CATEGORY
    and perk_name(plug_definition)
  )


def _exotic_perks(plugs: list[tuple[int, dict]]) -> tuple[str, ...]:
  return tuple(
    definition_name(plug_definition)
    for _, plug_definition in plugs
    if definition_name(plug_definition).startswith(EXOTIC_CLASS_ITEM_PERK_PREFIX)
  )
