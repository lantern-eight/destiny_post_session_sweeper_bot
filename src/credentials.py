'''Bungie application credentials from the gitignored .env file.

Real environment variables take precedence over values in .env.
'''

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

from src.paths import ENV_PATH

REQUIRED_VARIABLES = (
  'BUNGIE_API_KEY',
  'BUNGIE_CLIENT_ID',
  'BUNGIE_CLIENT_SECRET',
  'BUNGIE_REDIRECT_URI',
)
# Template values in .env.example all end with this, e.g. your_api_key_here.
PLACEHOLDER_SUFFIX = '_here'


class MissingCredentialsError(RuntimeError):
  '''A required value is missing from .env or still holds a template placeholder.'''


@dataclass(frozen=True)
class BungieCredentials:
  api_key: str
  client_id: str
  client_secret: str
  redirect_uri: str

  def __repr__(self) -> str:
    # Keep the API key and client secret out of logs and tracebacks.
    return (
      f'BungieCredentials(client_id={self.client_id!r}, '
      f'redirect_uri={self.redirect_uri!r})'
    )


def load_credentials(env_path: Path = ENV_PATH) -> BungieCredentials:
  '''Read Bungie credentials, reporting every missing value at once.'''
  file_values = dotenv_values(env_path) if env_path.exists() else {}
  values = {}
  missing_variables = []
  for variable in REQUIRED_VARIABLES:
    value = (os.environ.get(variable) or file_values.get(variable) or '').strip()
    if not value or value.endswith(PLACEHOLDER_SUFFIX):
      missing_variables.append(variable)
    values[variable] = value
  if missing_variables:
    raise MissingCredentialsError(
      f'Set {", ".join(missing_variables)} in .env (see .env.example).'
    )
  return BungieCredentials(
    api_key=values['BUNGIE_API_KEY'],
    client_id=values['BUNGIE_CLIENT_ID'],
    client_secret=values['BUNGIE_CLIENT_SECRET'],
    redirect_uri=values['BUNGIE_REDIRECT_URI'],
  )
