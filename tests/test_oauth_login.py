'''Tests for the local sign-in listener: stalled connections and stray requests.'''

from __future__ import annotations

import http.client
import shutil
import socket
import ssl
import threading
import time
from unittest.mock import MagicMock

import pytest

from src import oauth
from src.credentials import BungieCredentials

pytestmark = pytest.mark.skipif(
  shutil.which('openssl') is None, reason='needs openssl for a test certificate'
)
STATE = 'known-state'
TOKEN_PAYLOAD = {
  'access_token': 'access',
  'expires_in': 3600,
  'refresh_token': 'refresh',
  'refresh_expires_in': 7_776_000,
  'membership_id': '42',
}


def free_port() -> int:
  with socket.socket() as probe:
    probe.bind(('127.0.0.1', 0))
    return probe.getsockname()[1]


@pytest.fixture
def sign_in(tmp_path, monkeypatch):
  '''Start run_login in the background; returns (port, results, session).'''
  make_context = oauth._localhost_tls_context
  monkeypatch.setattr(
    oauth, '_localhost_tls_context', lambda: make_context(tmp_path / 'tls')
  )
  monkeypatch.setattr(oauth.secrets, 'token_urlsafe', lambda length: STATE)
  port = free_port()
  credentials = BungieCredentials(
    'api-key-value', '12345', 'secret', f'https://localhost:{port}/callback'
  )
  session = MagicMock()
  session.post.return_value.status_code = 200
  session.post.return_value.json.return_value = TOKEN_PAYLOAD
  results: dict = {}
  threads: list[threading.Thread] = []

  def start(timeout_seconds: float) -> None:
    def run() -> None:
      try:
        results['tokens'] = oauth.run_login(
          credentials,
          session,
          open_browser=False,
          timeout_seconds=timeout_seconds,
          token_cache_path=tmp_path / 'token-cache',
        )
      except oauth.OAuthError as error:
        results['error'] = error

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    threads.append(thread)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
      try:
        socket.create_connection(('127.0.0.1', port), timeout=1).close()
        return
      except OSError:
        time.sleep(0.05)
    raise AssertionError('the sign-in listener never started')

  yield start, port, results, session, threads
  for thread in threads:
    thread.join(timeout=15)


def browser_get(port: int, path: str) -> int:
  context = ssl.create_default_context()
  context.check_hostname = False
  context.verify_mode = ssl.CERT_NONE
  connection = http.client.HTTPSConnection(
    '127.0.0.1', port, context=context, timeout=5
  )
  try:
    connection.request('GET', path)
    return connection.getresponse().status
  finally:
    connection.close()


def test_a_stalled_connection_and_an_old_tab_do_not_stop_the_sign_in(sign_in):
  start, port, results, session, threads = sign_in
  start(timeout_seconds=20)
  stalled = socket.create_connection(('127.0.0.1', port))  # connects, says nothing
  try:
    assert browser_get(port, '/callback?state=from-an-old-tab&code=stale') == 400
    assert browser_get(port, f'/callback?state={STATE}&code=fresh') == 200
    threads[0].join(timeout=10)
  finally:
    stalled.close()
  assert results['tokens'].access_token == 'access'
  assert session.post.call_args.kwargs['data']['code'] == 'fresh'


def test_the_time_limit_holds_even_with_a_stalled_connection(sign_in):
  start, port, results, _, threads = sign_in
  started = time.monotonic()
  start(timeout_seconds=1)
  stalled = socket.create_connection(('127.0.0.1', port))
  try:
    threads[0].join(timeout=10)
  finally:
    stalled.close()
  assert 'Timed out' in str(results['error'])
  assert time.monotonic() - started < 8
