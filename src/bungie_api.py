'''Minimal Bungie.net platform API client.'''

from __future__ import annotations

import time
from collections.abc import Callable

import requests

BASE_URL = 'https://www.bungie.net/Platform'
SUCCESS_ERROR_CODE = 1
STEAM_MEMBERSHIP_TYPE = 3

# Bungie documents ThrottleSecondsBetweenActionPerUser = 0.1 for the item
# actions (transfer, Postmaster pull, lock); DIM spaces them the same way.
ACTION_INTERVAL_SECONDS = 0.1
# When throttled anyway, wait 1, 2, 4, 8, then 16 seconds before giving up.
MAXIMUM_THROTTLE_RETRIES = 5
THROTTLE_ERROR_STATUSES = frozenset(
  {
    'ThrottleLimitExceeded',
    'ThrottleLimitExceededMinutes',
    'ThrottleLimitExceededMomentarily',
    'ThrottleLimitExceededSeconds',
    'PerEndpointRequestThrottleExceeded',
    'PerApplicationThrottleExceeded',
    'PerApplicationAuthenticatedThrottleExceeded',
    'PerUserThrottleExceeded',
    'DestinyThrottledByGameServer',
  }
)

# DestinyComponentType values. See "Data formats we depend on" in README.md for
# the fields read from each. apply, next, and done only need where things are.
ACCOUNT_COMPONENTS = (
  102,  # ProfileInventories: the vault
  200,  # Characters: class and last played
  201,  # CharacterInventories: carried items and the Postmaster
  205,  # CharacterEquipment
)
# plan and ties also read rolls, tiers, and loadouts.
PLAN_COMPONENTS = (
  100,  # Profiles: account display name
  *ACCOUNT_COMPONENTS,
  206,  # CharacterLoadouts: in-game loadouts, their gear and mods
  300,  # ItemInstances: gear tier
  305,  # ItemSockets: plugged perks, stat plugs, archetype, tuning
  310,  # ItemReusablePlugs: perk options per column, tuning options
)


class BungieApiError(RuntimeError):
  '''Bungie returned a non-success ErrorCode or an unreadable response.'''

  def __init__(
    self, message: str, error_status: str = '', throttle_seconds: float = 0
  ) -> None:
    super().__init__(message)
    self.error_status = error_status
    self.throttle_seconds = throttle_seconds


class BungieClient:
  def __init__(
    self,
    api_key: str,
    access_token: str | None = None,
    session: requests.Session | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
  ) -> None:
    self.api_key = api_key
    self.access_token = access_token
    self.session = session or requests.Session()
    self.sleep = sleep
    self.clock = clock
    self._last_action_at: float | None = None

  def get(self, path: str, params: dict | None = None) -> dict:
    return self._request('GET', path, params=params)

  def post(self, path: str, body: dict) -> dict:
    return self._request('POST', path, json=body)

  # Item actions. Instance and character IDs are int64s, sent as strings the way
  # DIM sends them. A vault item is acted on through any of your characters.

  def set_lock_state(
    self, membership_type: int, character_id: str, item_id: str, locked: bool
  ) -> None:
    self._action(
      '/Destiny2/Actions/Items/SetLockState/',
      {
        'state': locked,
        'itemId': item_id,
        'characterId': character_id,
        'membershipType': membership_type,
      },
    )

  def transfer_item(
    self,
    membership_type: int,
    character_id: str,
    item_id: str,
    item_hash: int,
    to_vault: bool,
  ) -> None:
    '''Move between the vault and *character_id* (the holder, or the target).'''
    self._action(
      '/Destiny2/Actions/Items/TransferItem/',
      {
        'itemReferenceHash': item_hash,
        'stackSize': 1,
        'transferToVault': to_vault,
        'itemId': item_id,
        'characterId': character_id,
        'membershipType': membership_type,
      },
    )

  def pull_from_postmaster(
    self, membership_type: int, character_id: str, item_id: str, item_hash: int
  ) -> None:
    self._action(
      '/Destiny2/Actions/Items/PullFromPostmaster/',
      {
        'itemReferenceHash': item_hash,
        'stackSize': 1,
        'itemId': item_id,
        'characterId': character_id,
        'membershipType': membership_type,
      },
    )

  def _action(self, path: str, body: dict) -> dict:
    '''POST an item action, spaced out and retried while Bungie throttles it.

    The spacing counts from when each request was sent, as DIM does, so a slow
    reply does not add a pause on top of itself.
    '''
    attempt = 0
    while True:
      if self._last_action_at is not None:
        elapsed = self.clock() - self._last_action_at
        if elapsed < ACTION_INTERVAL_SECONDS:
          self.sleep(ACTION_INTERVAL_SECONDS - elapsed)
      self._last_action_at = self.clock()
      try:
        return self.post(path, body)
      except BungieApiError as error:
        is_throttled = error.error_status in THROTTLE_ERROR_STATUSES
        if not is_throttled or attempt == MAXIMUM_THROTTLE_RETRIES:
          raise
        self.sleep(max(error.throttle_seconds, 2**attempt))
        attempt += 1

  def get_memberships_for_current_user(self) -> dict:
    return self.get('/User/GetMembershipsForCurrentUser/')

  def get_profile(
    self,
    membership_type: int,
    membership_id: str,
    components: tuple[int, ...] = PLAN_COMPONENTS,
  ) -> dict:
    component_list = ','.join(str(component) for component in components)
    return self.get(
      f'/Destiny2/{membership_type}/Profile/{membership_id}/',
      params={'components': component_list},
    )

  def _request(self, method: str, path: str, **request_options: object) -> dict:
    headers = {'X-API-Key': self.api_key}
    if self.access_token:
      headers['Authorization'] = f'Bearer {self.access_token}'
    response = self.session.request(
      method, f'{BASE_URL}{path}', headers=headers, timeout=60, **request_options
    )
    try:
      payload = response.json()
    except ValueError as error:
      raise BungieApiError(
        f'{method} {path} returned HTTP {response.status_code} without JSON.'
      ) from error
    throttle_seconds = payload.get('ThrottleSeconds') or 0
    if payload.get('ErrorCode') != SUCCESS_ERROR_CODE:
      error_status = payload.get('ErrorStatus') or ''
      message = payload.get('Message')
      raise BungieApiError(
        f'{method} {path} failed: {error_status} ({message})',
        error_status,
        throttle_seconds,
      )
    # Bungie asks clients to wait this long before the next action call.
    if throttle_seconds > 0:
      self.sleep(throttle_seconds)
    return payload.get('Response', {})


def choose_destiny_membership(memberships_response: dict) -> dict:
  '''Pick the Destiny membership to act on, honoring Cross Save's primary.'''
  destiny_memberships = memberships_response.get('destinyMemberships', [])
  if not destiny_memberships:
    raise BungieApiError('This Bungie account has no Destiny memberships.')
  primary_membership_id = memberships_response.get('primaryMembershipId')
  for membership in destiny_memberships:
    if primary_membership_id and membership['membershipId'] == primary_membership_id:
      return membership
  if len(destiny_memberships) == 1:
    return destiny_memberships[0]
  for membership in destiny_memberships:
    if membership['membershipType'] == STEAM_MEMBERSHIP_TYPE:
      return membership
  return destiny_memberships[0]
