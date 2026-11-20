'''A fake Bungie account for testing the end-of-session flow (no real data).

It enforces the game rules the flow relies on: 9 unequipped items per
character bucket, class armor only on a character of that class, equipped items
never move, Postmaster pulls land in the owner's inventory, and a vault item is
locked through one of your characters. Like the real profile, it reports
Postmaster items as not transferable and not lockable until they are pulled.
'''

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import requests

from src.account import Account
from src.bungie_api import BungieApiError
from src.game_data import (
  CHARACTER_BUCKET_CAPACITY,
  POSTMASTER_BUCKET_HASH,
  VAULT_BUCKET_HASH,
)
from src.session import PlanView

WARLOCK = '2305843009000000001'
HUNTER = '2305843009000000002'
CLASS_TYPES = {WARLOCK: 2, HUNTER: 1}
KINETIC = 1498876634
ENERGY = 2465295065
HELMET = 3448274439
NOW = datetime(2026, 9, 27, 20, 0, tzinfo=UTC)


def _definition(
  name: str, item_type: int, bucket_hash: int, class_type: int = 3
) -> dict:
  return {
    'displayProperties': {'name': name},
    'itemType': item_type,
    'classType': class_type,
    'inventory': {'bucketTypeHash': bucket_hash, 'tierType': 5},
  }


DEFINITIONS = {
  '100': _definition('Kinetic Gun', 3, KINETIC),
  '101': _definition('Energy Gun', 3, ENERGY),
  '200': _definition('Warlock Hood', 2, HELMET, class_type=2),
  '201': _definition('Hunter Mask', 2, HELMET, class_type=1),
}
KINETIC_GUN, ENERGY_GUN, WARLOCK_HOOD, HUNTER_MASK = 100, 101, 200, 201


@dataclass
class FakeItem:
  instance_id: str
  item_hash: int
  location: str = 'vault'  # vault | inventory | equipped | postmaster
  character_id: str | None = None
  locked: bool = False
  # Some items can never be moved (or locked), wherever they are.
  movable: bool = True
  lockable: bool = True

  @property
  def bucket_hash(self) -> int:
    return DEFINITIONS[str(self.item_hash)]['inventory']['bucketTypeHash']


class FakeBungie:
  '''Stands in for BungieClient's item actions and returns the live profile.'''

  def __init__(self, items: list[FakeItem], vault_capacity: int = 1300) -> None:
    self.items = {item.instance_id: item for item in items}
    self.vault_capacity = vault_capacity
    self.latest_character = WARLOCK
    self.calls: list[tuple] = []
    # instance ID -> ErrorStatus to fail that item's next action with.
    self.failures: dict[str, str] = {}
    # Items whose next action goes through but whose reply never arrives.
    self.lost_replies: set[str] = set()

  def set_lock_state(
    self, membership_type: int, character_id: str, item_id: str, locked: bool
  ) -> None:
    self.calls.append(('lock' if locked else 'unlock', item_id))
    item = self._item(item_id)
    holder = item.character_id or character_id
    if character_id not in CLASS_TYPES or holder != character_id:
      raise BungieApiError('lock', 'DestinyItemNotFound')
    if not item.lockable:
      raise BungieApiError('lock', 'DestinyItemActionForbidden')
    item.locked = locked
    self._maybe_lose_reply(item_id)

  def transfer_item(
    self,
    membership_type: int,
    character_id: str,
    item_id: str,
    item_hash: int,
    to_vault: bool,
  ) -> None:
    self.calls.append(('to_vault' if to_vault else 'to_character', item_id))
    item = self._item(item_id)
    if item.location == 'equipped':
      raise BungieApiError('transfer', 'DestinyCannotPerformActionOnEquippedItem')
    if not item.movable or item.location == 'postmaster':
      raise BungieApiError('transfer', 'DestinyItemNotTransferrable')
    if to_vault:
      if item.location != 'inventory' or item.character_id != character_id:
        raise BungieApiError('transfer', 'DestinyItemNotFound')
      if self._vault_count() >= self.vault_capacity:
        raise BungieApiError('transfer', 'DestinyNoRoomInDestination')
      item.location, item.character_id = 'vault', None
      self._maybe_lose_reply(item_id)
      return
    if item.location != 'vault':
      raise BungieApiError('transfer', 'DestinyItemNotFound')
    class_type = DEFINITIONS[str(item.item_hash)]['classType']
    if class_type != 3 and class_type != CLASS_TYPES[character_id]:
      raise BungieApiError('transfer', 'DestinyItemActionForbidden')
    if self._carried(character_id, item.bucket_hash) >= CHARACTER_BUCKET_CAPACITY:
      raise BungieApiError('transfer', 'DestinyNoRoomInDestination')
    item.location, item.character_id = 'inventory', character_id
    self._maybe_lose_reply(item_id)

  def pull_from_postmaster(
    self, membership_type: int, character_id: str, item_id: str, item_hash: int
  ) -> None:
    self.calls.append(('pull', item_id))
    item = self._item(item_id)
    if item.location != 'postmaster' or item.character_id != character_id:
      raise BungieApiError('pull', 'DestinyItemNotFound')
    if self._carried(character_id, item.bucket_hash) >= CHARACTER_BUCKET_CAPACITY:
      raise BungieApiError('pull', 'DestinyNoRoomInDestination')
    item.location = 'inventory'

  def dismantle(self, *instance_ids: str) -> None:
    '''What you do in-game: only unlocked, unequipped items you are carrying.'''
    for instance_id in instance_ids:
      item = self.items[instance_id]
      assert item.location == 'inventory', f'{instance_id} is not in an inventory'
      assert not item.locked, f'{instance_id} is locked'
      del self.items[instance_id]

  def profile(self) -> dict:
    '''The profile Bungie would return right now.'''
    vault_items = []
    inventories: dict[str, list[dict]] = {character: [] for character in CLASS_TYPES}
    equipment: dict[str, list[dict]] = {character: [] for character in CLASS_TYPES}
    for item in self.items.values():
      in_postmaster = item.location == 'postmaster'
      transfer_status = 0
      if item.location == 'equipped':
        transfer_status = 1
      elif in_postmaster or not item.movable:
        transfer_status = 2
      raw_item = {
        'itemHash': item.item_hash,
        'itemInstanceId': item.instance_id,
        'state': 1 if item.locked else 0,
        'lockable': item.lockable and not in_postmaster,
        'transferStatus': transfer_status,
      }
      if item.location == 'vault':
        vault_items.append({**raw_item, 'bucketHash': VAULT_BUCKET_HASH})
      elif item.location == 'postmaster':
        inventories[item.character_id].append(
          {**raw_item, 'bucketHash': POSTMASTER_BUCKET_HASH}
        )
      elif item.location == 'inventory':
        inventories[item.character_id].append(
          {**raw_item, 'bucketHash': item.bucket_hash}
        )
      else:
        equipment[item.character_id].append(
          {**raw_item, 'bucketHash': item.bucket_hash}
        )
    return {
      'characters': {
        'data': {
          character_id: {
            'classType': class_type,
            'dateLastPlayed': '2026-09-27T19:00:00Z'
            if character_id == self.latest_character
            else '2026-09-20T19:00:00Z',
          }
          for character_id, class_type in CLASS_TYPES.items()
        }
      },
      'profileInventory': {'data': {'items': vault_items}},
      'characterInventories': {
        'data': {
          character: {'items': items} for character, items in inventories.items()
        }
      },
      'characterEquipment': {
        'data': {character: {'items': items} for character, items in equipment.items()}
      },
    }

  def where(self, instance_id: str) -> tuple[str, str | None] | None:
    item = self.items.get(instance_id)
    return (item.location, item.character_id) if item else None

  def moved_ids(self) -> set[str]:
    return {call[1] for call in self.calls if call[0] in ('to_vault', 'to_character')}

  def _maybe_lose_reply(self, item_id: str) -> None:
    if item_id in self.lost_replies:
      self.lost_replies.discard(item_id)
      raise requests.ConnectionError('the reply never arrived')

  def _item(self, item_id: str) -> FakeItem:
    if item_id in self.failures:
      raise BungieApiError('action', self.failures.pop(item_id))
    if item_id not in self.items:
      raise BungieApiError('action', 'DestinyItemNotFound')
    return self.items[item_id]

  def _vault_count(self) -> int:
    return sum(1 for item in self.items.values() if item.location == 'vault')

  def _carried(self, character_id: str, bucket_hash: int) -> int:
    return sum(
      1
      for item in self.items.values()
      if item.location == 'inventory'
      and item.character_id == character_id
      and item.bucket_hash == bucket_hash
    )


def open_account(fake: FakeBungie) -> Account:
  '''What a command sees at its start: a fresh profile from Bungie.'''
  return Account.from_profile(
    fake.profile(),
    DEFINITIONS,
    fake,
    membership_type=3,
    vault_capacity=fake.vault_capacity,
  )


def make_plan(
  actions: dict[str, str],
  *,
  generated: datetime = NOW,
  kept_for_your_lock: tuple[str, ...] = (),
  ties: tuple[dict, ...] = (),
  name: str = '20260927T200000Z-plan.yaml',
  compare_with: dict[str, tuple[str, ...]] | None = None,
) -> PlanView:
  return PlanView(
    path=Path(name),
    generated=generated,
    actions=actions,
    kept_for_your_lock=frozenset(kept_for_your_lock),
    ties=list(ties),
    compare_with=compare_with or {},
  )
