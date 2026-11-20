'''The live account: where each weapon and armor piece is, and the moves.

Built from a fresh profile at the start of each command and updated as each
action succeeds, so a run never re-reads the profile halfway through. Equipped
items are never moved.
'''

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from src.bungie_api import BungieApiError
from src.definitions import definition_name
from src.game_data import (
  CHARACTER_BUCKET_CAPACITY,
  CLASS_NAMES,
  ITEM_STATE_LOCKED,
  SLOTS_BY_BUCKET_HASH,
  TRANSFER_STATUS_NOT_TRANSFERRABLE,
  WEAPON_SLOTS_BY_BUCKET_HASH,
)
from src.inventory import iter_owned_items
from src.profile_snapshot import check_profile_is_complete

# Bungie errors about a single item. The run skips that item and carries on;
# any other error stops the run.
ITEM_ERROR_REASONS = {
  'DestinyItemNotFound': 'it is not where Bungie last saw it (moved or dismantled?)',
  'DestinyNoRoomInDestination': 'there is no room where it was going',
  'DestinyItemNotTransferrable': 'it cannot be moved',
  'DestinyItemNotTransferrableHasSideEffects': 'it cannot be moved',
  'DestinyCannotPerformActionOnEquippedItem': 'it is equipped',
  'DestinyItemActionForbidden': 'Bungie does not allow that for this item',
}


class ItemActions(Protocol):
  '''BungieClient's item actions, or a stand-in for dry runs and tests.'''

  def set_lock_state(
    self, membership_type: int, character_id: str, item_id: str, locked: bool
  ) -> None: ...

  def transfer_item(
    self,
    membership_type: int,
    character_id: str,
    item_id: str,
    item_hash: int,
    to_vault: bool,
  ) -> None: ...

  def pull_from_postmaster(
    self, membership_type: int, character_id: str, item_id: str, item_hash: int
  ) -> None: ...


class DryRunActions:
  '''Records the actions a real run would send, and sends nothing.'''

  def __init__(self) -> None:
    self.calls: list[tuple] = []

  def set_lock_state(self, *arguments: object) -> None:
    self.calls.append(('set_lock_state', *arguments))

  def transfer_item(self, *arguments: object) -> None:
    self.calls.append(('transfer_item', *arguments))

  def pull_from_postmaster(self, *arguments: object) -> None:
    self.calls.append(('pull_from_postmaster', *arguments))


class ItemActionError(Exception):
  '''One item could not be locked or moved; the run carries on without it.'''

  def __init__(self, item: AccountItem, action: str, reason: str) -> None:
    super().__init__(f'could not {action} {item.name} ({item.slot}): {reason}')
    self.reason = reason


@dataclass(frozen=True)
class Character:
  character_id: str
  class_name: str
  date_last_played: str


@dataclass
class AccountItem:
  instance_id: str
  item_hash: int
  name: str
  class_name: str | None  # armor only: it can only go to a character of that class
  bucket_hash: int  # the character bucket it takes a slot in
  location: str  # vault | inventory | equipped | postmaster
  character_id: str | None  # who holds it; None in the vault
  is_locked: bool
  lockable: bool
  transferable: bool

  @property
  def slot(self) -> str:
    return SLOTS_BY_BUCKET_HASH[self.bucket_hash]

  @property
  def is_movable(self) -> bool:
    '''It can go between the vault and your characters right now.'''
    return self.location in ('vault', 'inventory') and self.transferable

  def is_carried_by(self, character_id: str) -> bool:
    '''In that character's inventory: unequipped, and not in the Postmaster.'''
    return self.location == 'inventory' and self.character_id == character_id

  def fits_class(self, class_name: str) -> bool:
    '''Weapons go to anyone; armor only to a character of its class.'''
    return not self.class_name or self.class_name == class_name


class Account:
  def __init__(
    self,
    actions: ItemActions,
    membership_type: int,
    characters: dict[str, Character],
    items: dict[str, AccountItem],
    vault_item_count: int = 0,
    vault_capacity: int | None = None,
    instance_ids: frozenset[str] | None = None,
  ) -> None:
    self.actions = actions
    self.membership_type = membership_type
    self.characters = characters
    self.items = items
    self.vault_item_count = vault_item_count
    self.vault_capacity = vault_capacity
    # Every item instance in the profile, including ones the tool does not
    # track (ghosts, or gear missing from the cached manifest). An ID missing
    # from here was dismantled.
    self.instance_ids = frozenset(items) if instance_ids is None else instance_ids
    # Actions sent to Bungie so far, whether or not they worked.
    self.actions_attempted = 0

  @classmethod
  def from_profile(
    cls,
    profile: dict,
    item_definitions: dict,
    actions: ItemActions,
    membership_type: int,
    vault_capacity: int | None = None,
  ) -> Account:
    '''Every weapon and armor piece on the account, wherever it is.

    A profile with parts missing is refused (IncompleteProfileError).
    '''
    check_profile_is_complete(profile)
    characters = {}
    for character_id, character in profile['characters']['data'].items():
      class_name = CLASS_NAMES.get(character.get('classType'))
      if class_name:
        characters[character_id] = Character(
          character_id, class_name, character.get('dateLastPlayed', '')
        )
    items = {}
    instance_ids = set()
    vault_item_count = 0
    for owned in iter_owned_items(profile):
      if owned.location == 'vault':
        vault_item_count += 1
      instance_id = owned.item.get('itemInstanceId')
      if instance_id:
        instance_ids.add(instance_id)
      if not instance_id or owned.location == 'profile':
        continue
      definition = item_definitions.get(str(owned.item.get('itemHash')), {})
      bucket_hash = definition.get('inventory', {}).get('bucketTypeHash')
      if bucket_hash not in SLOTS_BY_BUCKET_HASH:
        # Missing from the cached manifest: it still takes a slot where it is.
        bucket_hash = owned.item.get('bucketHash')
        if owned.location != 'inventory' or bucket_hash not in SLOTS_BY_BUCKET_HASH:
          continue
      if owned.location == 'postmaster':
        # Bungie flags Postmaster items as not transferable (they come out by a
        # pull, not a transfer) and may flag them not lockable. Once pulled, a
        # weapon or armor piece is both, so judge it by its definition.
        transferable = not definition.get('nonTransferrable', False)
        lockable = True
      else:
        transfer_status = owned.item.get('transferStatus', 0)
        transferable = not transfer_status & TRANSFER_STATUS_NOT_TRANSFERRABLE
        lockable = bool(owned.item.get('lockable'))
      is_armor = bucket_hash not in WEAPON_SLOTS_BY_BUCKET_HASH
      items[instance_id] = AccountItem(
        instance_id=instance_id,
        item_hash=owned.item['itemHash'],
        name=definition_name(definition) or '?',
        class_name=CLASS_NAMES.get(definition.get('classType')) if is_armor else None,
        bucket_hash=bucket_hash,
        location=owned.location,
        character_id=owned.character_id,
        is_locked=bool(owned.item.get('state', 0) & ITEM_STATE_LOCKED),
        lockable=lockable,
        transferable=transferable,
      )
    return cls(
      actions,
      membership_type,
      characters,
      items,
      vault_item_count,
      vault_capacity,
      frozenset(instance_ids),
    )

  def latest_character_id(self) -> str:
    '''The character you played most recently: the one being cleaned up.'''
    latest = max(
      self.characters.values(), key=lambda character: character.date_last_played
    )
    return latest.character_id

  def inventory(
    self, character_id: str, bucket_hash: int | None = None
  ) -> list[AccountItem]:
    '''Unequipped weapons and armor a character is carrying.'''
    return [
      item
      for item in self.items.values()
      if item.is_carried_by(character_id)
      and (bucket_hash is None or item.bucket_hash == bucket_hash)
    ]

  def free_slots(self, character_id: str, bucket_hash: int) -> int:
    return CHARACTER_BUCKET_CAPACITY - len(self.inventory(character_id, bucket_hash))

  def describe_location(self, item: AccountItem) -> str:
    '''Where an item is, for messages: "in the vault", "on your Hunter"...'''
    if item.location == 'vault':
      return 'in the vault'
    holder = self.characters.get(item.character_id or '')
    holder_name = f'your {holder.class_name}' if holder else 'a deleted character'
    if item.location == 'equipped':
      return f'equipped on {holder_name}'
    if item.location == 'postmaster':
      return f"in {holder_name}'s Postmaster"
    return f'on {holder_name}'

  def set_locked(
    self, item: AccountItem, locked: bool, acting_character_id: str
  ) -> None:
    '''Lock or unlock. Vault items are acted on through *acting_character_id*.'''
    character_id = item.character_id or acting_character_id
    self._call(
      item,
      'lock' if locked else 'unlock',
      lambda: self.actions.set_lock_state(
        self.membership_type, character_id, item.instance_id, locked
      ),
    )
    item.is_locked = locked

  def move_to_vault(self, item: AccountItem) -> None:
    if item.location == 'vault':
      return
    self._check_movable(item)
    if self.vault_capacity is not None and self.vault_item_count >= self.vault_capacity:
      raise ItemActionError(item, 'move', 'the vault is full')
    self._call(
      item,
      'move',
      lambda: self.actions.transfer_item(
        self.membership_type,
        item.character_id,
        item.instance_id,
        item.item_hash,
        True,
      ),
    )
    item.location, item.character_id = 'vault', None
    self.vault_item_count += 1

  def move_to_character(self, item: AccountItem, character_id: str) -> None:
    '''Move into a character's inventory, through the vault if another has it.'''
    if item.is_carried_by(character_id):
      return
    self._check_movable(item)
    character = self.characters.get(character_id)
    if character is None:
      raise ItemActionError(item, 'move', 'that character no longer exists')
    if not item.fits_class(character.class_name):
      raise ItemActionError(item, 'move', f'{item.class_name} armor')
    if self.free_slots(character_id, item.bucket_hash) <= 0:
      raise ItemActionError(
        item, 'move', f'no room in your {character.class_name} {item.slot}'
      )
    if item.location != 'vault':
      self.move_to_vault(item)
    self._call(
      item,
      'move',
      lambda: self.actions.transfer_item(
        self.membership_type, character_id, item.instance_id, item.item_hash, False
      ),
    )
    item.location, item.character_id = 'inventory', character_id
    self.vault_item_count -= 1

  def pull_from_postmaster(self, item: AccountItem) -> None:
    '''Into the inventory of the character whose Postmaster holds it.'''
    if item.location != 'postmaster':
      raise ItemActionError(item, 'pull', 'it is not in the Postmaster')
    if self.free_slots(item.character_id, item.bucket_hash) <= 0:
      raise ItemActionError(item, 'pull', f'no room in {item.slot}')
    self._call(
      item,
      'pull',
      lambda: self.actions.pull_from_postmaster(
        self.membership_type, item.character_id, item.instance_id, item.item_hash
      ),
    )
    item.location = 'inventory'

  def _check_movable(self, item: AccountItem) -> None:
    if item.location == 'equipped':
      raise ItemActionError(
        item, 'move', 'it is equipped, and equipped items never move'
      )
    if item.location == 'postmaster':
      raise ItemActionError(item, 'move', 'it is in the Postmaster')
    if not item.transferable:
      raise ItemActionError(item, 'move', 'it cannot be moved')

  def _call(self, item: AccountItem, action: str, send: Callable[[], None]) -> None:
    self.actions_attempted += 1
    try:
      send()
    except BungieApiError as error:
      reason = ITEM_ERROR_REASONS.get(error.error_status)
      if reason is None:
        raise
      raise ItemActionError(item, action, reason) from error
