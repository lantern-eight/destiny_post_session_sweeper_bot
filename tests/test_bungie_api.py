'''Tests for the Bungie API client and membership selection.'''

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.bungie_api import (
  ACCOUNT_COMPONENTS,
  BungieApiError,
  BungieClient,
  choose_destiny_membership,
)

STEAM = {'membershipType': 3, 'membershipId': '111'}
PLAYSTATION = {'membershipType': 2, 'membershipId': '222'}


def make_session(payload: dict) -> MagicMock:
  session = MagicMock()
  session.request.return_value.json.return_value = payload
  return session


def test_client_sends_api_key_and_bearer_token():
  session = make_session({'ErrorCode': 1, 'Response': {'ok': True}})
  client = BungieClient('api-key-value', 'access-token-value', session)
  assert client.get('/Destiny2/Manifest/') == {'ok': True}
  assert session.request.call_args.kwargs['headers'] == {
    'X-API-Key': 'api-key-value',
    'Authorization': 'Bearer access-token-value',
  }


def test_client_raises_on_bungie_error_code():
  session = make_session(
    {'ErrorCode': 5, 'ErrorStatus': 'SystemDisabled', 'Message': 'Maintenance'}
  )
  client = BungieClient('api-key-value', session=session)
  with pytest.raises(BungieApiError, match='SystemDisabled'):
    client.get('/Destiny2/Manifest/')


def test_cross_save_primary_membership_wins():
  memberships = {
    'destinyMemberships': [STEAM, PLAYSTATION],
    'primaryMembershipId': '222',
  }
  assert choose_destiny_membership(memberships) == PLAYSTATION


def test_single_membership_is_used():
  assert choose_destiny_membership({'destinyMemberships': [PLAYSTATION]}) == PLAYSTATION


def test_steam_is_preferred_without_cross_save():
  memberships = {'destinyMemberships': [PLAYSTATION, STEAM]}
  assert choose_destiny_membership(memberships) == STEAM


def test_account_without_destiny_membership_raises():
  with pytest.raises(BungieApiError, match='no Destiny memberships'):
    choose_destiny_membership({'destinyMemberships': []})


class FakeClock:
  def __init__(self) -> None:
    self.now = 100.0
    self.sleeps: list[float] = []

  def sleep(self, seconds: float) -> None:
    self.sleeps.append(round(seconds, 3))
    self.now += seconds

  def __call__(self) -> float:
    return self.now


def make_action_client(*payloads: dict) -> tuple[BungieClient, MagicMock, FakeClock]:
  session = MagicMock()
  session.request.return_value.json.side_effect = list(payloads)
  clock = FakeClock()
  client = BungieClient(
    'api-key-value', 'token', session, sleep=clock.sleep, clock=clock
  )
  return client, session, clock


def test_actions_send_the_documented_bodies():
  client, session, _ = make_action_client(*[{'ErrorCode': 1, 'Response': 0}] * 3)
  client.set_lock_state(3, 'character-1', 'item-1', True)
  client.transfer_item(3, 'character-1', 'item-1', 1234, to_vault=True)
  client.pull_from_postmaster(3, 'character-1', 'item-1', 1234)
  paths = [call.args[1] for call in session.request.call_args_list]
  bodies = [call.kwargs['json'] for call in session.request.call_args_list]
  assert paths == [
    'https://www.bungie.net/Platform/Destiny2/Actions/Items/SetLockState/',
    'https://www.bungie.net/Platform/Destiny2/Actions/Items/TransferItem/',
    'https://www.bungie.net/Platform/Destiny2/Actions/Items/PullFromPostmaster/',
  ]
  assert bodies[0] == {
    'state': True,
    'itemId': 'item-1',
    'characterId': 'character-1',
    'membershipType': 3,
  }
  assert bodies[1] == {
    'itemReferenceHash': 1234,
    'stackSize': 1,
    'transferToVault': True,
    'itemId': 'item-1',
    'characterId': 'character-1',
    'membershipType': 3,
  }
  assert bodies[2] == {
    'itemReferenceHash': 1234,
    'stackSize': 1,
    'itemId': 'item-1',
    'characterId': 'character-1',
    'membershipType': 3,
  }


def test_actions_are_spaced_a_tenth_of_a_second_apart():
  client, _, clock = make_action_client(*[{'ErrorCode': 1, 'Response': 0}] * 2)
  client.set_lock_state(3, 'character-1', 'item-1', True)
  clock.now += 0.03
  client.set_lock_state(3, 'character-1', 'item-2', True)
  assert clock.sleeps == [0.07]


def test_throttled_actions_wait_and_retry():
  throttled = {'ErrorCode': 1672, 'ErrorStatus': 'DestinyThrottledByGameServer'}
  client, session, clock = make_action_client(
    throttled, throttled, {'ErrorCode': 1, 'Response': 0}
  )
  client.transfer_item(3, 'character-1', 'item-1', 1234, to_vault=False)
  assert session.request.call_count == 3
  assert [seconds for seconds in clock.sleeps if seconds >= 1] == [1, 2]


def test_other_action_errors_are_not_retried():
  client, session, _ = make_action_client(
    {'ErrorCode': 1642, 'ErrorStatus': 'DestinyNoRoomInDestination', 'Message': 'Full'}
  )
  with pytest.raises(BungieApiError) as raised:
    client.transfer_item(3, 'character-1', 'item-1', 1234, to_vault=True)
  assert raised.value.error_status == 'DestinyNoRoomInDestination'
  assert session.request.call_count == 1


def test_a_slow_reply_counts_toward_the_spacing():
  client, session, clock = make_action_client(*[{'ErrorCode': 1, 'Response': 0}] * 2)

  def slow_request(*arguments, **options):
    clock.now += 0.2  # Bungie takes 200 ms to answer
    return session.request.return_value

  session.request.side_effect = slow_request
  client.set_lock_state(3, 'character-1', 'item-1', True)
  client.set_lock_state(3, 'character-1', 'item-2', True)
  assert clock.sleeps == []  # the next request may go at once


def test_plan_and_account_reads_ask_for_the_components_they_need():
  session = make_session({'ErrorCode': 1, 'Response': {}})
  client = BungieClient('api-key-value', 'token', session)
  client.get_profile(3, '111', ACCOUNT_COMPONENTS)
  assert session.request.call_args.kwargs['params'] == {'components': '102,200,201,205'}
  client.get_profile(3, '111')
  components = session.request.call_args.kwargs['params']['components'].split(',')
  assert '304' not in components
  assert {'206', '300', '305', '310'} <= set(components)
