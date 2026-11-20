'''Filesystem locations. Everything under data/ and .cache/ is gitignored.'''

from __future__ import annotations

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPOSITORY_ROOT / '.env'
CONFIG_PATH = REPOSITORY_ROOT / 'config.yaml'
WISHLIST_PATH = REPOSITORY_ROOT / 'wishlist.yaml'
TOKEN_CACHE_PATH = REPOSITORY_ROOT / '.bungie_token_cache'

DATA_DIRECTORY = REPOSITORY_ROOT / 'data'
PROFILE_SNAPSHOT_DIRECTORY = DATA_DIRECTORY / 'profiles'
RUNS_DIRECTORY = DATA_DIRECTORY / 'runs'
STATE_PATH = DATA_DIRECTORY / 'state.json'
COMMAND_LOCK_PATH = DATA_DIRECTORY / '.command.lock'
TLS_DIRECTORY = DATA_DIRECTORY / 'tls'

CACHE_DIRECTORY = REPOSITORY_ROOT / '.cache'
MANIFEST_CACHE_DIRECTORY = CACHE_DIRECTORY / 'manifest'
VOLTRON_CACHE_PATH = CACHE_DIRECTORY / 'voltron.txt'
VOLTRON_PARSED_CACHE_PATH = CACHE_DIRECTORY / 'voltron-parsed.json'
