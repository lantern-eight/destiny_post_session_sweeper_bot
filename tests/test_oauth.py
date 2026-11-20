'''Tests for token caching, refresh at run start, and expiry handling.'''

from __future__ import annotations

import stat
from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from src.credentials import BungieCredentials
from src.oauth import (
  NotSignedInError,
  OAuthError,
  TokenSet,
  get_valid_tokens,
  load_token_cache,
  save_token_cache,
)

NOW = 1_800_000_000.0
NINETY_DAYS = 7_776_000
CREDENTIALS = BungieCredentials(
  api_key='api-key-value',
  client_id='12345',
  client_secret='client-secret-value',
  redirect_uri='https://localhost:6821/callback',
)


def make_tokens(access_expires_in: float, refresh_expires_in: float) -> TokenSet:
  return TokenSet(
    access_token='access-token-value',
    access_expires_at=NOW + access_expires_in,
    refresh_token='refresh-token-value',
    refresh_expires_at=NOW + refresh_expires_in,
    membership_id='42',
  )


def make_response(status_code: int, payload: dict) -> MagicMock:
  response = MagicMock()
  response.status_code = status_code
  response.json.return_value = payload
  return response


@pytest.fixture
def cache_path(tmp_path):
  return tmp_path / '.bungie_token_cache'


def test_token_response_expiries_become_absolute_times():
  tokens = TokenSet.from_token_response(
    {
      'access_token': 'access',
      'expires_in': 3600,
      'refresh_token': 'refresh',
      'refresh_expires_in': NINETY_DAYS,
      'membership_id': 42,
    },
    NOW,
  )
  assert tokens.access_expires_at == NOW + 3600
  assert tokens.refresh_expires_at == NOW + NINETY_DAYS
  assert tokens.membership_id == '42'


def test_refresh_response_without_refresh_token_keeps_the_old_one():
  previous_tokens = make_tokens(-10, 1000)
  tokens = TokenSet.from_token_response(
    {'access_token': 'new-access', 'expires_in': 3600}, NOW, previous_tokens
  )
  assert tokens.refresh_token == 'refresh-token-value'
  assert tokens.refresh_expires_at == previous_tokens.refresh_expires_at
  assert tokens.membership_id == '42'


def test_cache_round_trips_with_owner_only_permissions(cache_path):
  tokens = make_tokens(3600, NINETY_DAYS)
  save_token_cache(tokens, cache_path)
  assert load_token_cache(cache_path) == tokens
  assert stat.S_IMODE(cache_path.stat().st_mode) == 0o600


def test_repr_hides_tokens_and_membership_id():
  tokens = replace(make_tokens(3600, NINETY_DAYS), membership_id='membership-value')
  representation = repr(tokens)
  assert 'access-token-value' not in representation
  assert 'refresh-token-value' not in representation
  assert 'membership-value' not in representation


def test_fresh_access_token_is_used_without_calling_bungie(cache_path):
  save_token_cache(make_tokens(3600, NINETY_DAYS), cache_path)
  session = MagicMock()
  tokens = get_valid_tokens(CREDENTIALS, session, cache_path, now=NOW)
  assert tokens.access_token == 'access-token-value'
  session.post.assert_not_called()


def test_expired_access_token_is_refreshed_and_recached(cache_path):
  save_token_cache(make_tokens(-10, NINETY_DAYS), cache_path)
  session = MagicMock()
  session.post.return_value = make_response(
    200,
    {
      'access_token': 'new-access',
      'expires_in': 3600,
      'refresh_token': 'new-refresh',
      'refresh_expires_in': NINETY_DAYS,
      'membership_id': '42',
    },
  )
  tokens = get_valid_tokens(CREDENTIALS, session, cache_path, now=NOW)
  assert tokens.access_token == 'new-access'
  assert tokens.refresh_token == 'new-refresh'
  assert tokens.refresh_expires_at == NOW + NINETY_DAYS
  assert load_token_cache(cache_path) == tokens
  request_options = session.post.call_args.kwargs
  assert request_options['data'] == {
    'grant_type': 'refresh_token',
    'refresh_token': 'refresh-token-value',
  }
  assert request_options['auth'] == ('12345', 'client-secret-value')


def test_expired_refresh_token_asks_for_login(cache_path):
  save_token_cache(make_tokens(-10, -10), cache_path)
  with pytest.raises(NotSignedInError, match='sweeper login'):
    get_valid_tokens(CREDENTIALS, MagicMock(), cache_path, now=NOW)


def test_missing_cache_asks_for_login(cache_path):
  with pytest.raises(NotSignedInError, match='sweeper login'):
    get_valid_tokens(CREDENTIALS, MagicMock(), cache_path, now=NOW)


def test_rejected_refresh_raises_with_bungie_error(cache_path):
  save_token_cache(make_tokens(-10, NINETY_DAYS), cache_path)
  session = MagicMock()
  session.post.return_value = make_response(
    400, {'error': 'invalid_grant', 'error_description': 'Refresh token expired'}
  )
  with pytest.raises(OAuthError, match='invalid_grant'):
    get_valid_tokens(CREDENTIALS, session, cache_path, now=NOW)


def test_a_damaged_cache_asks_for_login(cache_path):
  cache_path.write_text('{"access_token": "abc"')
  with pytest.raises(NotSignedInError, match='damaged'):
    get_valid_tokens(CREDENTIALS, MagicMock(), cache_path, now=NOW)
