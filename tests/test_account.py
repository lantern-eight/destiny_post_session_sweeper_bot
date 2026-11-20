'''Tests for the live account model and its moves.'''

from __future__ import annotations

import pytest

from src.account import Account, DryRunActions, ItemActionError
from src.bungie_api import BungieApiError
from src.profile_snapshot import IncompleteProfileError
from tests.account_fixtures import (
  DEFINITIONS,
  ENERGY_GUN,
  HUNTER,
  HUNTER_MASK,
  KINETIC,
  KINETIC_GUN,
  WARLOCK,
  FakeBungie,
  FakeItem,
  open_account,
)


def test_the_account_knows_where_everything_is():
  fake = FakeBungie(
    [
      FakeItem('1', KINETIC_GUN, 'inventory', WARLOCK, locked=True),
      FakeItem('2', ENERGY_GUN, 'equipped', HUNTER),
      FakeItem('3', HUNTER_MASK),
    ]
  )
  account = open_account(fake)
  assert account.latest_character_id() == WARLOCK
  carried, equipped, vaulted = (account.items[key] for key in ('1', '2', '3'))
  assert (carried.location, carried.character_id, carried.is_locked) == (
    'inventory',
    WARLOCK,
    True,
  )
  assert (equipped.location, equipped.slot) == ('equipped', 'Energy')
  assert (vaulted.class_name, vaulted.slot) == ('Hunter', 'Helmet')
  assert carried.is_carried_by(WARLOCK)
  assert not vaulted.fits_class('Warlock')
  assert account.free_slots(WARLOCK, KINETIC) == 8
  assert account.vault_item_count == 1


def test_items_missing_from_the_manifest_still_take_a_slot():
  fake = FakeBungie([])
  profile = fake.profile()
  profile['characterInventories']['data'][WARLOCK]['items'].append(
    {'itemHash': 999, 'itemInstanceId': '9', 'bucketHash': KINETIC, 'state': 0}
  )
  account = Account.from_profile(profile, DEFINITIONS, DryRunActions(), 3)
  assert account.free_slots(WARLOCK, KINETIC) == 8


def test_moving_between_characters_goes_through_the_vault():
  fake = FakeBungie([FakeItem('1', KINETIC_GUN, 'inventory', HUNTER)])
  account = open_account(fake)
  account.move_to_character(account.items['1'], WARLOCK)
  assert fake.calls == [('to_vault', '1'), ('to_character', '1')]
  assert fake.where('1') == ('inventory', WARLOCK)


@pytest.mark.parametrize(
  ('item', 'reason'),
  [
    (FakeItem('1', KINETIC_GUN, 'equipped', WARLOCK), 'equipped items never move'),
    (FakeItem('1', KINETIC_GUN, 'postmaster', WARLOCK), 'in the Postmaster'),
    (FakeItem('1', HUNTER_MASK), 'Hunter armor'),
  ],
)
def test_moves_the_game_forbids_are_refused_before_asking_bungie(item, reason):
  fake = FakeBungie([item])
  account = open_account(fake)
  with pytest.raises(ItemActionError, match=reason):
    account.move_to_character(account.items['1'], WARLOCK)
  assert fake.calls == []


def test_a_full_slot_or_vault_is_refused():
  fake = FakeBungie(
    [
      FakeItem(str(10 + number), KINETIC_GUN, 'inventory', WARLOCK)
      for number in range(9)
    ]
    + [FakeItem('1', KINETIC_GUN)],
    vault_capacity=1,
  )
  account = open_account(fake)
  with pytest.raises(ItemActionError, match='no room in your Warlock Kinetic'):
    account.move_to_character(account.items['1'], WARLOCK)
  with pytest.raises(ItemActionError, match='vault is full'):
    account.move_to_vault(account.items['10'])


def test_vault_items_are_locked_through_your_character():
  fake = FakeBungie([FakeItem('1', KINETIC_GUN)])
  account = open_account(fake)
  account.set_locked(account.items['1'], True, WARLOCK)
  assert fake.items['1'].locked
  assert account.items['1'].is_locked


def test_item_errors_are_softened_but_others_stop_the_run():
  fake = FakeBungie([FakeItem('1', KINETIC_GUN), FakeItem('2', KINETIC_GUN)])
  account = open_account(fake)
  fake.failures['1'] = 'DestinyItemNotFound'
  with pytest.raises(ItemActionError, match='not where Bungie last saw it'):
    account.set_locked(account.items['1'], True, WARLOCK)
  fake.failures['2'] = 'SystemDisabled'
  with pytest.raises(BungieApiError):
    account.set_locked(account.items['2'], True, WARLOCK)


def test_postmaster_items_can_move_and_lock_once_pulled():
  # Bungie reports them as neither while they sit in the Postmaster.
  fake = FakeBungie([FakeItem('1', KINETIC_GUN, 'postmaster', WARLOCK)])
  profile = fake.profile()
  raw_item = profile['characterInventories']['data'][WARLOCK]['items'][0]
  assert (raw_item['transferStatus'], raw_item['lockable']) == (2, False)
  account = Account.from_profile(profile, DEFINITIONS, fake, 3)
  item = account.items['1']
  account.pull_from_postmaster(item)
  account.move_to_vault(item)
  account.set_locked(item, True, WARLOCK)
  assert fake.where('1') == ('vault', None)
  assert fake.items['1'].locked


@pytest.mark.parametrize(
  'missing', ['profileInventory', 'characterInventories', 'characterEquipment']
)
def test_an_incomplete_profile_is_refused(missing):
  profile = FakeBungie([FakeItem('1', KINETIC_GUN)]).profile()
  profile[missing] = {'privacy': 2}  # Bungie left this part out
  with pytest.raises(IncompleteProfileError, match='try again in a minute'):
    Account.from_profile(profile, DEFINITIONS, DryRunActions(), 3)


def test_a_profile_missing_one_character_inventory_is_refused():
  profile = FakeBungie([]).profile()
  del profile['characterInventories']['data'][HUNTER]
  with pytest.raises(IncompleteProfileError, match="characters' inventories"):
    Account.from_profile(profile, DEFINITIONS, DryRunActions(), 3)


def test_actions_are_counted_even_when_they_fail():
  fake = FakeBungie([FakeItem('1', KINETIC_GUN)])
  account = open_account(fake)
  fake.failures['1'] = 'DestinyItemNotFound'
  with pytest.raises(ItemActionError):
    account.set_locked(account.items['1'], True, WARLOCK)
  assert account.actions_attempted == 1
