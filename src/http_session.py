'''HTTP sessions that ride out Bungie's occasional transient server errors.'''

from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Only GETs are retried: token exchanges and item actions are not idempotent.
RETRY_POLICY = Retry(
  total=3,
  backoff_factor=1,
  status_forcelist=(500, 502, 503, 504),
  allowed_methods=frozenset({'GET'}),
  raise_on_status=False,
)


def make_session() -> requests.Session:
  '''Return a session that retries GETs on transient 5xx responses.'''
  session = requests.Session()
  session.mount('https://', HTTPAdapter(max_retries=RETRY_POLICY))
  return session
