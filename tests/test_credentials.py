'''Tests for loading Bungie credentials from .env.'''

from __future__ import annotations

import pytest

from src.credentials import (
  REQUIRED_VARIABLES,
  MissingCredentialsError,
  load_credentials,
)

COMPLETE_VALUES = {
  'BUNGIE_API_KEY': 'api-key-value',
  'BUNGIE_CLIENT_ID': '12345',
  'BUNGIE_CLIENT_SECRET': 'client-secret-value',
  'BUNGIE_REDIRECT_URI': 'https://localhost:6821/callback',
}


@pytest.fixture(autouse=True)
def clear_bungie_environment(monkeypatch):
  for variable in REQUIRED_VARIABLES:
    monkeypatch.delenv(variable, raising=False)


def write_env_file(tmp_path, values):
  env_path = tmp_path / '.env'
  env_path.write_text(''.join(f'{name}={value}\n' for name, value in values.items()))
  return env_path


def test_loads_every_value_from_env_file(tmp_path):
  credentials = load_credentials(write_env_file(tmp_path, COMPLETE_VALUES))
  assert credentials.api_key == 'api-key-value'
  assert credentials.client_id == '12345'
  assert credentials.client_secret == 'client-secret-value'
  assert credentials.redirect_uri == 'https://localhost:6821/callback'


def test_environment_variables_override_env_file(tmp_path, monkeypatch):
  monkeypatch.setenv('BUNGIE_CLIENT_ID', '99999')
  credentials = load_credentials(write_env_file(tmp_path, COMPLETE_VALUES))
  assert credentials.client_id == '99999'


def test_template_placeholders_count_as_missing(tmp_path):
  values = {**COMPLETE_VALUES, 'BUNGIE_API_KEY': 'your_api_key_here'}
  with pytest.raises(MissingCredentialsError, match='BUNGIE_API_KEY'):
    load_credentials(write_env_file(tmp_path, values))


def test_reports_every_missing_value_at_once(tmp_path):
  values = {'BUNGIE_API_KEY': 'api-key-value'}
  with pytest.raises(
    MissingCredentialsError,
    match='BUNGIE_CLIENT_ID, BUNGIE_CLIENT_SECRET, BUNGIE_REDIRECT_URI',
  ):
    load_credentials(write_env_file(tmp_path, values))


def test_repr_hides_api_key_and_secret(tmp_path):
  credentials = load_credentials(write_env_file(tmp_path, COMPLETE_VALUES))
  assert 'api-key-value' not in repr(credentials)
  assert 'client-secret-value' not in repr(credentials)
