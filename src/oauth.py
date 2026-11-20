'''Bungie OAuth: one-time browser sign-in, then token refresh at run start.

Tokens live in the gitignored .bungie_token_cache file with owner-only
permissions. Nothing runs in the background: each command refreshes an expired
access token before it talks to Bungie. Every refresh returns a new 90-day
refresh token, so sign-in is only needed again after 90+ days without a run.
'''

from __future__ import annotations

import errno
import html
import http.server
import json
import os
import secrets
import socket
import ssl
import subprocess
import threading
import time
import webbrowser
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from src.credentials import BungieCredentials
from src.paths import TLS_DIRECTORY, TOKEN_CACHE_PATH

AUTHORIZE_URL = 'https://www.bungie.net/en/OAuth/Authorize'
TOKEN_URL = 'https://www.bungie.net/platform/app/oauth/token/'
EXPIRY_MARGIN_SECONDS = 60
LOGIN_TIMEOUT_SECONDS = 300
LOCAL_HOSTNAMES = ('localhost', '127.0.0.1')
# A connection that sends nothing (a browser's spare connection, say) is
# dropped after this long instead of holding up the sign-in.
CONNECTION_TIMEOUT_SECONDS = 10
# The redirect says localhost, which a browser may reach over IPv4 or IPv6, so
# the listener takes both: no other program can answer on the other one.
LISTEN_ADDRESSES = (('127.0.0.1', socket.AF_INET), ('::1', socket.AF_INET6))


class NotSignedInError(RuntimeError):
  '''No usable sign-in exists; the user needs to run `sweeper login`.'''


class OAuthError(RuntimeError):
  '''Bungie rejected a sign-in or refresh, or the sign-in flow failed.'''


@dataclass(frozen=True)
class TokenSet:
  access_token: str
  access_expires_at: float
  refresh_token: str
  refresh_expires_at: float
  membership_id: str

  @classmethod
  def from_token_response(
    cls, payload: dict, now: float, previous: TokenSet | None = None
  ) -> TokenSet:
    '''Build tokens from Bungie's token response.

    Expiry times are stored as absolute timestamps. If a refresh response ever
    omits the refresh token, the previous one is kept.
    '''
    if 'refresh_token' in payload:
      refresh_token = payload['refresh_token']
      refresh_expires_at = now + payload['refresh_expires_in']
    elif previous is not None:
      refresh_token = previous.refresh_token
      refresh_expires_at = previous.refresh_expires_at
    else:
      raise OAuthError(
        'Bungie returned no refresh token. Is the app set to Confidential?'
      )
    previous_membership_id = previous.membership_id if previous else ''
    return cls(
      access_token=payload['access_token'],
      access_expires_at=now + payload['expires_in'],
      refresh_token=refresh_token,
      refresh_expires_at=refresh_expires_at,
      membership_id=str(payload.get('membership_id') or previous_membership_id),
    )

  def access_token_is_fresh(self, now: float) -> bool:
    return now < self.access_expires_at - EXPIRY_MARGIN_SECONDS

  def refresh_token_is_fresh(self, now: float) -> bool:
    return now < self.refresh_expires_at - EXPIRY_MARGIN_SECONDS

  def __repr__(self) -> str:
    # Keep tokens and the account's membership ID out of logs and tracebacks.
    return 'TokenSet(<hidden>)'


def load_token_cache(path: Path = TOKEN_CACHE_PATH) -> TokenSet | None:
  '''Return cached tokens, or None when the user has never signed in.'''
  if not path.exists():
    return None
  try:
    return TokenSet(**json.loads(path.read_text()))
  except (OSError, ValueError, TypeError) as error:
    raise NotSignedInError(
      f'The sign-in cache ({path.name}) is damaged. Run `uv run sweeper login` '
      'to sign in again.'
    ) from error


def save_token_cache(tokens: TokenSet, path: Path = TOKEN_CACHE_PATH) -> None:
  '''Write tokens with owner-only permissions, replacing any previous cache.'''
  temporary_path = path.with_name(f'{path.name}.tmp')
  temporary_path.unlink(missing_ok=True)
  file_descriptor = os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
  with os.fdopen(file_descriptor, 'w') as token_file:
    json.dump(asdict(tokens), token_file)
  os.replace(temporary_path, path)


def exchange_authorization_code(
  credentials: BungieCredentials,
  code: str,
  session: requests.Session,
  now: float | None = None,
) -> TokenSet:
  form = {'grant_type': 'authorization_code', 'code': code}
  return _request_tokens(credentials, form, session, now)


def refresh_tokens(
  credentials: BungieCredentials,
  tokens: TokenSet,
  session: requests.Session,
  now: float | None = None,
) -> TokenSet:
  form = {'grant_type': 'refresh_token', 'refresh_token': tokens.refresh_token}
  return _request_tokens(credentials, form, session, now, previous=tokens)


def get_valid_tokens(
  credentials: BungieCredentials,
  session: requests.Session,
  path: Path = TOKEN_CACHE_PATH,
  now: float | None = None,
  minimum_lifetime_seconds: float = 0,
) -> TokenSet:
  '''Return a usable access token, refreshing and re-caching it if expired.

  Commands that change the account ask for a token that lasts the whole run.
  '''
  current_time = time.time() if now is None else now
  tokens = load_token_cache(path)
  if tokens is None:
    raise NotSignedInError('Not signed in yet. Run `uv run sweeper login`.')
  if tokens.access_token_is_fresh(current_time + minimum_lifetime_seconds):
    return tokens
  if not tokens.refresh_token_is_fresh(current_time):
    raise NotSignedInError(
      'Bungie sign-in expired after 90+ days unused. Run `uv run sweeper login`.'
    )
  refreshed_tokens = refresh_tokens(credentials, tokens, session, current_time)
  save_token_cache(refreshed_tokens, path)
  return refreshed_tokens


def _request_tokens(
  credentials: BungieCredentials,
  form: dict[str, str],
  session: requests.Session,
  now: float | None,
  previous: TokenSet | None = None,
) -> TokenSet:
  response = session.post(
    TOKEN_URL,
    data=form,
    auth=(credentials.client_id, credentials.client_secret),
    headers={'X-API-Key': credentials.api_key},
    timeout=30,
  )
  if response.status_code != 200:
    raise OAuthError(_describe_token_failure(response))
  request_time = time.time() if now is None else now
  return TokenSet.from_token_response(response.json(), request_time, previous)


def _describe_token_failure(response: requests.Response) -> str:
  try:
    payload = response.json()
  except ValueError:
    return f'Bungie token request failed with HTTP {response.status_code}.'
  error = payload.get('error', 'unknown_error')
  description = payload.get('error_description', '')
  return f'Bungie token request failed: {error} {description}'.strip()


def run_login(
  credentials: BungieCredentials,
  session: requests.Session,
  open_browser: bool = True,
  timeout_seconds: float = LOGIN_TIMEOUT_SECONDS,
  token_cache_path: Path = TOKEN_CACHE_PATH,
) -> TokenSet:
  '''Sign in through the browser and cache the resulting tokens.

  Bungie redirects to https://localhost after approval, so a short-lived HTTPS
  listener with a self-signed certificate catches the authorization code. The
  browser shows a certificate warning the first time; proceeding is safe
  because the listener only runs on this machine.
  '''
  redirect = urlparse(credentials.redirect_uri)
  if redirect.scheme != 'https' or redirect.hostname not in LOCAL_HOSTNAMES:
    raise OAuthError(
      'BUNGIE_REDIRECT_URI must look like https://localhost:6821/callback.'
    )
  expected_state = secrets.token_urlsafe(24)
  query = urlencode(
    {
      'client_id': credentials.client_id,
      'response_type': 'code',
      'state': expected_state,
    }
  )
  authorize_url = f'{AUTHORIZE_URL}?{query}'
  callback_result: dict[str, str] = {}
  answered = threading.Event()
  handler_class = _make_callback_handler(
    redirect.path or '/', expected_state, callback_result, answered
  )
  servers = _listen(redirect.port or 443, handler_class, _localhost_tls_context())
  # Only a running listener can be shut down; shutting down one that never
  # started would wait forever.
  running: list[_CallbackServer] = []
  try:
    for server in servers:
      threading.Thread(
        target=server.serve_forever, kwargs={'poll_interval': 0.5}, daemon=True
      ).start()
      running.append(server)
    print('Approve the sweeper in your browser. If no tab opened, visit:')
    print(authorize_url, flush=True)
    if open_browser:
      webbrowser.open(authorize_url)
    if not answered.wait(timeout_seconds):
      raise OAuthError('Timed out waiting for the Bungie sign-in redirect.')
  finally:
    for server in running:
      server.shutdown()
    for server in servers:
      server.server_close()
  if 'error' in callback_result:
    raise OAuthError(callback_result['error'])
  tokens = exchange_authorization_code(credentials, callback_result['code'], session)
  save_token_cache(tokens, token_cache_path)
  return tokens


class _CallbackServer(http.server.ThreadingHTTPServer):
  '''A loopback HTTPS listener for the sign-in redirect.

  Each connection gets its own thread and a timeout, and its TLS handshake
  happens there too, so a connection that stalls cannot block the others.
  '''

  daemon_threads = True

  def __init__(
    self,
    address: tuple[str, int],
    handler_class: type[http.server.BaseHTTPRequestHandler],
    tls_context: ssl.SSLContext,
    address_family: socket.AddressFamily,
  ) -> None:
    self.address_family = address_family
    self.tls_context = tls_context
    super().__init__(address, handler_class)

  def get_request(self) -> tuple[socket.socket, object]:
    connection, client_address = self.socket.accept()
    connection.settimeout(CONNECTION_TIMEOUT_SECONDS)
    return connection, client_address

  def finish_request(self, request: socket.socket, client_address: object) -> None:
    try:
      tls_connection = self.tls_context.wrap_socket(request, server_side=True)
    except OSError:
      return  # it gave up, or never spoke TLS
    try:
      self.RequestHandlerClass(tls_connection, client_address, self)
    finally:
      tls_connection.close()

  def handle_error(self, request: object, client_address: object) -> None:
    return  # a browser dropping a connection is not worth a traceback


def _listen(
  port: int,
  handler_class: type[http.server.BaseHTTPRequestHandler],
  tls_context: ssl.SSLContext,
) -> list[_CallbackServer]:
  servers: list[_CallbackServer] = []
  for host, family in LISTEN_ADDRESSES:
    try:
      servers.append(_CallbackServer((host, port), handler_class, tls_context, family))
    except OSError as error:
      no_ipv6 = family == socket.AF_INET6 and error.errno in (
        errno.EADDRNOTAVAIL,
        errno.EAFNOSUPPORT,
      )
      if no_ipv6:
        continue  # this machine has no IPv6 loopback
      for server in servers:
        server.server_close()
      raise OAuthError(f'Could not listen on localhost:{port}: {error}') from error
  return servers


def _make_callback_handler(
  callback_path: str,
  expected_state: str,
  callback_result: dict[str, str],
  answered: threading.Event,
) -> type[http.server.BaseHTTPRequestHandler]:
  class CallbackHandler(http.server.BaseHTTPRequestHandler):
    timeout = CONNECTION_TIMEOUT_SECONDS

    def do_GET(self) -> None:
      parsed_url = urlparse(self.path)
      if parsed_url.path != callback_path:
        self._respond(404, 'Not found.')
        return
      query = parse_qs(parsed_url.query)
      if query.get('state', [''])[0] != expected_state:
        # An old tab or a stray request: not this sign-in, so keep waiting.
        self._respond(
          400,
          'This sign-in link is out of date. Use the newest one, or close this tab.',
        )
        return
      if 'code' not in query:
        callback_result['error'] = 'Bungie returned no authorization code.'
        self._respond(400, 'Sign-in was not approved. You can close this tab.')
      else:
        callback_result['code'] = query['code'][0]
        self._respond(200, 'Signed in to Bungie. You can close this tab.')
      answered.set()

    def _respond(self, status: int, message: str) -> None:
      body = (
        f'<!doctype html><title>Sweeper sign-in</title><p>{html.escape(message)}</p>'
      ).encode()
      self.send_response(status)
      self.send_header('Content-Type', 'text/html; charset=utf-8')
      self.send_header('Content-Length', str(len(body)))
      self.end_headers()
      self.wfile.write(body)

    def log_message(self, *arguments: object) -> None:
      # Request lines contain the authorization code; never log them.
      return

  return CallbackHandler


def _localhost_tls_context(tls_directory: Path = TLS_DIRECTORY) -> ssl.SSLContext:
  certificate_path, key_path = ensure_localhost_certificate(tls_directory)
  context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
  context.load_cert_chain(certificate_path, key_path)
  return context


def ensure_localhost_certificate(
  tls_directory: Path = TLS_DIRECTORY,
) -> tuple[Path, Path]:
  '''Create a self-signed localhost certificate once and reuse it after.'''
  certificate_path = tls_directory / 'localhost-cert.pem'
  key_path = tls_directory / 'localhost-key.pem'
  if certificate_path.exists() and key_path.exists():
    return certificate_path, key_path
  tls_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
  # openssl keeps the mode of a file it overwrites, so the key is private from
  # its first byte, whichever openssl is on the PATH (LibreSSL would otherwise
  # create it readable by everyone until the chmod below).
  key_path.touch(mode=0o600)
  key_path.chmod(0o600)
  command = [
    'openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
    '-keyout', str(key_path), '-out', str(certificate_path),
    '-days', '3650', '-subj', '/CN=localhost',
    '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1',
  ]  # fmt: skip
  try:
    subprocess.run(command, check=True, capture_output=True)
  except (FileNotFoundError, subprocess.CalledProcessError) as error:
    raise OAuthError(
      f'Could not create a localhost certificate with openssl: {error}'
    ) from error
  key_path.chmod(0o600)
  return certificate_path, key_path
