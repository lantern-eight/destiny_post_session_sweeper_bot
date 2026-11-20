'''Command-line entry point: `uv run sweeper <command>`.'''

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import requests
import yaml

from src.account import Account, DryRunActions
from src.bungie_api import (
  ACCOUNT_COMPONENTS,
  PLAN_COMPONENTS,
  BungieApiError,
  BungieClient,
  choose_destiny_membership,
)
from src.credentials import BungieCredentials, MissingCredentialsError, load_credentials
from src.definitions import perk_categories
from src.files import CommandLockError, hold_command_lock, make_private_directory
from src.game_data import VAULT_BUCKET_HASH
from src.http_session import make_session
from src.inventory import locked_instance_ids
from src.manifest import TABLES_USED, Manifest, ManifestError
from src.oauth import NotSignedInError, OAuthError, get_valid_tokens, run_login
from src.paths import (
  COMMAND_LOCK_PATH,
  DATA_DIRECTORY,
  REPOSITORY_ROOT,
  RUNS_DIRECTORY,
)
from src.plan import (
  WHAT_IF_DIRECTORY,
  Definitions,
  Wishlists,
  build_plan,
  format_plan_summary,
  load_previous_keepers,
  save_plan,
)
from src.preferences import Preferences, PreferencesError, load_preferences
from src.profile_snapshot import (
  IncompleteProfileError,
  SnapshotError,
  check_profile_is_complete,
  format_profile_summary,
  load_profile_snapshot,
  save_profile_snapshot,
)
from src.reports import (
  format_clearing_report,
  format_finish_report,
  format_round_report,
)
from src.session import (
  PlanView,
  SessionError,
  check_plan_is_fresh,
  finish_session,
  is_stale,
  lock_changes,
  open_ties,
  require_session,
  run_round,
  start_session,
)
from src.state import CurrentTie, StateError, StateFile, ToolState
from src.terminal import StatusLine, echo
from src.text import count_label
from src.ties import (
  PROMPT,
  QUIT_ANSWERS,
  SKIP_ONLY_PROMPT,
  bring_copies_in,
  copy_letters,
  format_pick_outcome,
  format_tie_step,
  options_in_saved_order,
  pairs_for_pick,
  parse_pick,
  rolls_for_pairs,
  trait_pair_options,
)
from src.voltron import VoltronEntry, VoltronError, ensure_voltron, load_voltron_entries
from src.weapons import build_weapons
from src.wishlist import WishlistEntry, WishlistError, add_rolls, load_wishlist

# Commands that change the account ask for a token that outlasts a long run.
ACTION_TOKEN_LIFETIME_SECONDS = 30 * 60
# Rough time per lock or move, request included, for the heads-up on big runs.
SECONDS_PER_ACTION = 0.4
LONG_RUN_ACTIONS = 50
# Everything the tool writes is readable by you alone.
PRIVATE_UMASK = 0o077
# Errors that explain themselves: printed as one line, no traceback.
EXPLAINED_ERRORS = (
  CommandLockError,
  IncompleteProfileError,
  ManifestError,
  MissingCredentialsError,
  NotSignedInError,
  OAuthError,
  PreferencesError,
  SessionError,
  SnapshotError,
  StateError,
  VoltronError,
  WishlistError,
  BungieApiError,
  requests.RequestException,
  yaml.YAMLError,
)


class TieStepStopped(Exception):
  '''You stopped at the tie-step prompt.'''


class Progress:
  '''What a command changed before it stopped, for the note after an error.'''

  def __init__(self) -> None:
    self.state_files: list[StateFile] = []
    self.accounts: list[Account] = []

  def changed_something(self) -> bool:
    return any(state_file.saves for state_file in self.state_files) or any(
      account.actions_attempted
      for account in self.accounts
      if not isinstance(account.actions, DryRunActions)
    )


@dataclass
class Bungie:
  '''A signed-in connection to Bungie for one command.'''

  http: requests.Session
  client: BungieClient
  membership: dict
  credentials: BungieCredentials

  def fetch_profile(self, components: tuple[int, ...] = PLAN_COMPONENTS) -> dict:
    return self.client.get_profile(
      self.membership['membershipType'], self.membership['membershipId'], components
    )

  def refresh_sign_in(self) -> None:
    '''Make sure the sign-in outlasts the rest of a long command.'''
    tokens = get_valid_tokens(
      self.credentials,
      self.http,
      minimum_lifetime_seconds=ACTION_TOKEN_LIFETIME_SECONDS,
    )
    self.client.access_token = tokens.access_token


@dataclass
class GameData:
  '''Manifest tables plus Voltron, loaded once per command.'''

  http: requests.Session
  preferences: Preferences
  # Download notes and warnings go above the step line.
  status: StatusLine
  manifest: Manifest = field(init=False)
  definitions: Definitions = field(init=False)
  vault_capacity: int | None = field(init=False)
  _voltron: dict[str, list[VoltronEntry]] | None = None

  def __post_init__(self) -> None:
    self.manifest = Manifest(session=self.http, progress=self.status.note)
    self.definitions = Definitions(
      items=self.manifest.table('DestinyInventoryItemDefinition'),
      sets=self.manifest.table('DestinyEquipableItemSetDefinition'),
      plug_sets=self.manifest.table('DestinyPlugSetDefinition'),
      loadout_names=self.manifest.table('DestinyLoadoutNameDefinition'),
    )
    buckets = self.manifest.table('DestinyInventoryBucketDefinition')
    self.vault_capacity = buckets.get(str(VAULT_BUCKET_HASH), {}).get('itemCount')

  @property
  def voltron(self) -> dict[str, list[VoltronEntry]]:
    if self._voltron is None:
      download = ensure_voltron(session=self.http, progress=self.status.note)
      if download.note:
        self.status.note(f'Warning: {download.note}')
      self._voltron = load_voltron_entries(
        download.path,
        self.definitions.items,
        self.preferences.weapons.voltron_modes,
        manifest_version=self.manifest.version,
      )
    return self._voltron


def command_login(arguments: argparse.Namespace) -> None:
  credentials = load_credentials()
  run_login(credentials, make_session(), open_browser=not arguments.no_browser)
  echo('Signed in. Tokens saved to .bungie_token_cache (gitignored).')


def command_fetch(arguments: argparse.Namespace) -> None:
  bungie = _connect(arguments.status)
  profile = _read_profile(bungie, arguments)
  check_profile_is_complete(profile)
  snapshot_path = save_profile_snapshot(profile)
  echo(format_profile_summary(profile, bungie.membership))
  echo(f'Saved raw profile to {_relative(snapshot_path)}')


def command_plan(arguments: argparse.Namespace) -> None:
  '''Dry run: read the inventory, decide, and write the plan. Changes nothing.

  With --profile it plans from a saved snapshot instead, as a what-if kept
  apart from the plans apply uses.
  '''
  preferences = load_preferences()
  state = StateFile(dry_run=True).state
  status = arguments.status
  if arguments.profile:
    profile, snapshot_path = load_profile_snapshot(arguments.profile)
    snapshot_name: str | None = snapshot_path.name
    runs_directory = WHAT_IF_DIRECTORY
    http = make_session()
  else:
    bungie = _connect(status)
    profile = _read_profile(bungie, arguments)
    snapshot_name = None
    runs_directory = RUNS_DIRECTORY
    http = bungie.http
  check_profile_is_complete(profile)
  if not arguments.profile:
    save_profile_snapshot(profile)
  with status.step('Loading game data'):
    game_data = GameData(http, preferences, status)
  with status.step('Deciding what to keep'):
    plan = _build_plan(
      profile,
      game_data,
      _tool_locked_ids(state, profile),
      load_wishlist(),
      snapshot_name,
    )
  plan_path = save_plan(plan, runs_directory)
  echo(format_plan_summary(plan, _relative(plan_path)))


def command_apply(arguments: argparse.Namespace) -> None:
  '''Start the end-of-session run (or continue it): locks, keepers out, round 1.'''
  status = arguments.status
  state_file = _open_state(arguments)
  bungie, game_data = _connect_for_actions(status)
  account = _open_account(bungie, game_data, arguments)
  plan = PlanView.load_latest(RUNS_DIRECTORY)
  now = _now()
  session = state_file.state.session
  not_carried: frozenset[str] = frozenset()
  if session is not None and is_stale(session, account, now):
    echo(
      f'The session from {session.started_at:%Y-%m-%d %H:%M} UTC on your '
      f'{session.class_name} was never finished; putting that gear back first.'
    )
    tool_locks_before = set(state_file.state.tool_locked_ids)
    echo(format_finish_report(finish_session(account, plan, state_file, status)))
    echo()
    # Keepers that finishing the old session just locked are new ones, which
    # stay in the vault, not keepers you were carrying.
    not_carried = frozenset(state_file.state.tool_locked_ids - tool_locks_before)
    session = None
  if session is None:
    check_plan_is_fresh(plan, now)
  _warn_if_long(account, plan, state_file)
  if session is None:
    report = start_session(account, plan, state_file, now, not_carried, status)
  else:
    echo(
      f'Continuing the session started {session.started_at:%H:%M} UTC on your '
      f'{session.class_name}.'
    )
    report = run_round(account, plan, state_file, status)
  echo(format_round_report(report))
  _print_dry_run_note(account)


def command_next(arguments: argparse.Namespace) -> None:
  '''Next round: count what you dismantled and stage more shards.'''
  state_file = _open_state(arguments)
  session = require_session(state_file)
  bungie, game_data = _connect_for_actions(arguments.status)
  account = _open_account(bungie, game_data, arguments)
  if account.latest_character_id() != session.character_id:
    echo(
      f'Note: this session is on your {session.class_name}; shards are staged there.'
    )
  plan = PlanView.load_latest(RUNS_DIRECTORY)
  _warn_if_long(account, plan, state_file)
  report = run_round(account, plan, state_file, arguments.status)
  echo(format_round_report(report))
  _print_dry_run_note(account)


def command_ties(arguments: argparse.Namespace) -> None:
  '''Tie step: bring one tied weapon's copies in, list its trait pairs, take a pick.'''
  state_file = _open_state(arguments)
  session = require_session(state_file)
  connection = _connect_for_actions(arguments.status)
  current_tie = session.current_tie
  if current_tie is not None and current_tie.answered:
    earlier = 'skip of' if current_tie.skipped else 'pick for'
    echo(f'Finishing your earlier {earlier} {current_tie.weapon} first.')
    _finish_tie_answer(state_file, connection, arguments)
    return
  if arguments.skip:
    if current_tie is None:
      _skip_next_tie(state_file, connection, arguments)
    else:
      _answer_tie(state_file, None, connection, arguments)
    return
  if arguments.pick is not None and current_tie is not None:
    numbers = _parse_pick_argument(arguments.pick, current_tie)
    _answer_tie(state_file, numbers, connection, arguments)
    return
  if arguments.pick is not None and not arguments.dry_run:
    raise SessionError(
      'No tie step is in progress. Run `uv run sweeper ties` first: it brings the '
      'copies in and shows the numbered pairs to pick from.'
    )
  option_count = _show_tie(state_file, connection, arguments)
  if option_count is None:
    return
  if arguments.pick is not None:
    # A dry-run pick from the list just shown.
    numbers = _parse_pick_argument(arguments.pick, session.current_tie)
    _answer_tie(state_file, numbers, connection, arguments)
    return
  if arguments.dry_run:
    echo()
    echo(
      'To answer, start the tie step for real with `uv run sweeper ties` (it saves '
      'this list); add `--pick N --dry-run` to preview a pick first.'
    )
    return
  if not sys.stdin.isatty():
    echo()
    echo(
      'Answer with `uv run sweeper ties --pick 2,18`, or `uv run sweeper ties '
      '--skip` to keep every copy for now.'
      if option_count
      else 'Nothing to pick, so skip it with `uv run sweeper ties --skip`.'
    )
    return
  try:
    numbers = _ask_for_pick(option_count)
  except TieStepStopped:
    echo('Stopped. The copies stay in your inventory; run ties again to pick.')
    return
  _answer_tie(state_file, numbers, connection, arguments)


def command_done(arguments: argparse.Namespace) -> None:
  '''Put your gear back and end the session.'''
  state_file = _open_state(arguments)
  if state_file.state.session is None:
    echo('No session is in progress, so there is nothing to put back.')
    return
  bungie, game_data = _connect_for_actions(arguments.status)
  account = _open_account(bungie, game_data, arguments)
  plan = PlanView.load_latest(RUNS_DIRECTORY)
  report = finish_session(account, plan, state_file, arguments.status)
  echo(format_finish_report(report))
  _print_dry_run_note(account)


def command_update_data(arguments: argparse.Namespace) -> None:
  '''Refresh the manifest and Voltron now instead of on first use.'''
  session = make_session()
  manifest = Manifest(session=session, progress=arguments.status.note)
  version = manifest.ensure_tables(force=arguments.force)
  if manifest.offline:
    echo(f'Bungie could not be reached, so the cached manifest {version} stays.')
  else:
    echo(f'Manifest {version}: {count_label(len(TABLES_USED), "table")} cached.')
  download = ensure_voltron(
    session=session,
    refresh=True,
    accept_smaller=arguments.force,
    progress=arguments.status.note,
  )
  if download.note:
    arguments.status.note(f'Warning: {download.note}')
  echo(f'Voltron wishlist cached at {_relative(download.path)}.')


def _show_tie(
  state_file: StateFile,
  connection: tuple[Bungie, GameData],
  arguments: argparse.Namespace,
) -> int | None:
  '''Bring the next tied weapon's copies in and list its pairs.

  A tie already waiting for your answer is listed again with the same numbers.
  Returns how many pairs there are, or None when no tie is waiting.
  '''
  session = require_session(state_file)
  bungie, game_data = connection
  profile = _read_profile(bungie, arguments)
  account = _account_from_profile(profile, bungie, game_data, arguments)
  plan = PlanView.load_latest(RUNS_DIRECTORY)
  waiting = open_ties(plan, session, account)
  current_tie = session.current_tie
  if current_tie is not None and all(
    tie['weapon'] != current_tie.weapon for tie in waiting
  ):
    session.current_tie = current_tie = None  # no longer tied (a newer plan)
    state_file.save()
  if not waiting:
    skipped = f' (skipped this session: {", ".join(session.skipped_ties)})'
    echo(
      'No tied weapons are waiting for a pick'
      f'{skipped if session.skipped_ties else ""}. '
      'Next: `uv run sweeper done` puts your gear back.'
    )
    return None
  tie = next(
    (tie for tie in waiting if current_tie and tie['weapon'] == current_tie.weapon),
    waiting[0],
  )
  brought_in = bring_copies_in(account, plan, state_file, tie, arguments.status)
  current_tie = session.current_tie
  weapons = {
    weapon.instance_id: weapon
    for weapon in build_weapons(
      profile, game_data.definitions.items, game_data.definitions.plug_sets
    )
  }
  if not current_tie.letters:
    current_tie.letters = copy_letters(
      [
        copy_id
        for copy_id in tie['copies']
        if copy_id in weapons and copy_id in account.items
      ]
    )
  copies_by_letter = {
    letter: weapons[copy_id]
    for copy_id, letter in current_tie.letters.items()
    if copy_id in weapons
  }
  with arguments.status.step("Reading Voltron's rolls"):
    voltron_entries = game_data.voltron.get(tie['group_name'], [])
  options = trait_pair_options(voltron_entries, copies_by_letter)
  if current_tie.options:
    options = options_in_saved_order(current_tie.options, options, copies_by_letter)
  else:
    current_tie.options = [list(option.traits) for option in options]
  state_file.save()
  clearing = format_clearing_report(brought_in.report)
  if clearing:
    echo(clearing)
    echo()
  echo(format_tie_step(tie, copies_by_letter, brought_in.where, options, len(waiting)))
  if brought_in.left_out:
    echo()
    echo(
      f'{count_label(brought_in.left_out, "copy", "copies")} did not fit. Dismantle '
      'the shards in that slot (or lock any you want to keep), then run '
      '`uv run sweeper ties` again to bring them in; the numbers stay the same.'
    )
  _print_dry_run_note(account)
  return len(options)


def _skip_next_tie(
  state_file: StateFile,
  connection: tuple[Bungie, GameData],
  arguments: argparse.Namespace,
) -> None:
  '''--skip before the tie step started: skip the weapon it would show next.'''
  session = require_session(state_file)
  bungie, game_data = connection
  account = _open_account(bungie, game_data, arguments)
  waiting = open_ties(PlanView.load_latest(RUNS_DIRECTORY), session, account)
  if not waiting:
    echo('No tied weapons are waiting for a pick, so there is nothing to skip.')
    return
  tie = waiting[0]
  session.current_tie = CurrentTie(
    weapon=tie['weapon'], group_name=tie['group_name'], copies=list(tie['copies'])
  )
  _answer_tie(state_file, None, connection, arguments)


def _answer_tie(
  state_file: StateFile,
  numbers: list[int] | None,
  connection: tuple[Bungie, GameData],
  arguments: argparse.Namespace,
) -> None:
  '''Record your answer (a pick is saved to wishlist.yaml), then act on it.

  The answer is saved in the session first, so if anything after this fails,
  running ties again finishes it.
  '''
  session = require_session(state_file)
  current_tie = session.current_tie
  _, game_data = connection
  if numbers is None:
    current_tie.skipped = True
    session.skipped_ties.append(current_tie.weapon)
    echo(
      f'Skipped {current_tie.weapon}: every tied copy stays kept, and it will not '
      'come up again this session.'
    )
  else:
    pairs = pairs_for_pick(current_tie.options, numbers)
    rolls = rolls_for_pairs(
      pairs,
      # Your local date: an evening pick is dated today, not tomorrow (UTC).
      added=date.today().isoformat(),
      game_version=game_data.manifest.version,
      categories_by_perk=perk_categories(game_data.definitions.items),
    )
    _save_rolls(current_tie.group_name, rolls, arguments.dry_run)
    current_tie.picked = pairs
  state_file.save()
  _finish_tie_answer(state_file, connection, arguments)


def _save_rolls(group_name: str, rolls: list[WishlistEntry], dry_run: bool) -> None:
  labels = [roll.label for roll in rolls]
  if dry_run:
    echo(f'Would save to wishlist.yaml under {group_name}: {"; ".join(labels)}.')
    return
  added = [roll.label for roll in add_rolls(group_name, rolls)]
  if added:
    echo(f'Saved to wishlist.yaml under {group_name}: {"; ".join(added)}.')
  already_there = [label for label in labels if label not in added]
  if already_there:
    echo(f'wishlist.yaml already has {"; ".join(already_there)} for {group_name}.')


def _finish_tie_answer(
  state_file: StateFile,
  connection: tuple[Bungie, GameData],
  arguments: argparse.Namespace,
) -> None:
  '''Plan again after a pick, run the round that follows, then close the tie.'''
  session = require_session(state_file)
  current_tie = session.current_tie
  bungie, game_data = connection
  with arguments.status.step('Connecting to Bungie'):
    bungie.refresh_sign_in()  # you may have sat at the prompt for a while
  plan = PlanView.load_latest(RUNS_DIRECTORY)
  profile = _read_profile(bungie, arguments)
  if current_tie.picked is not None:
    wishlist = load_wishlist()
    if arguments.dry_run:
      # Nothing was saved, so add the pick to what wishlist.yaml says.
      wishlist.setdefault(current_tie.group_name, []).extend(
        rolls_for_pairs(current_tie.picked)
      )
    with arguments.status.step('Planning again'):
      new_plan = _build_plan(
        profile, game_data, _tool_locked_ids(state_file.state, profile), wishlist
      )
    outcome = format_pick_outcome(current_tie, new_plan, plan.actions)
    if arguments.dry_run:
      echo(f'Planning again would give: {outcome}')
      plan = PlanView.from_plan(Path('(the plan after this pick)'), new_plan)
    else:
      plan_path = save_plan(new_plan, RUNS_DIRECTORY)
      echo(f'Planned again ({plan_path.name}). {outcome}')
      plan = PlanView.from_plan(plan_path, new_plan)
    echo()
  account = _account_from_profile(profile, bungie, game_data, arguments)
  report = run_round(account, plan, state_file, arguments.status)
  session.current_tie = None
  state_file.save()
  echo(format_round_report(report))
  _print_dry_run_note(account)


def _ask_for_pick(option_count: int) -> list[int] | None:
  '''Ask at the prompt until the answer makes sense. None means skip.'''
  while True:
    try:
      answer = input(PROMPT if option_count else SKIP_ONLY_PROMPT)
    except EOFError:
      answer = 'q'
    if answer.strip().lower() in QUIT_ANSWERS:
      raise TieStepStopped
    try:
      return parse_pick(answer, option_count)
    except ValueError as error:
      echo(f'{error}.')


def _parse_pick_argument(pick: str, current_tie: CurrentTie) -> list[int]:
  try:
    numbers = parse_pick(pick, len(current_tie.options))
  except ValueError as error:
    raise SessionError(f'{error}.') from error
  if numbers is None:
    raise SessionError('Give the pair numbers, like `--pick 2,18`, or use `--skip`.')
  return numbers


def _build_plan(
  profile: dict,
  game_data: GameData,
  tool_locked_ids: set[str],
  personal_wishlist: dict[str, list[WishlistEntry]],
  snapshot_name: str | None = None,
) -> dict:
  return build_plan(
    profile,
    game_data.definitions,
    game_data.preferences,
    Wishlists(personal=personal_wishlist, voltron=game_data.voltron),
    previous_keepers=load_previous_keepers(RUNS_DIRECTORY),
    tool_locked_ids=tool_locked_ids,
    snapshot_name=snapshot_name,
  )


def _tool_locked_ids(state: ToolState, profile: dict) -> set[str]:
  '''The tool's locks, counting pending ones that went through.'''
  if not state.pending_lock_ids:
    return set(state.tool_locked_ids)
  return state.tool_locked_ids | (state.pending_lock_ids & locked_instance_ids(profile))


def _connect(status: StatusLine, minimum_token_lifetime: float = 0) -> Bungie:
  with status.step('Connecting to Bungie'):
    http = make_session()
    credentials = load_credentials()
    tokens = get_valid_tokens(
      credentials, http, minimum_lifetime_seconds=minimum_token_lifetime
    )
    client = BungieClient(credentials.api_key, tokens.access_token, http)
    memberships = client.get_memberships_for_current_user()
  return Bungie(http, client, choose_destiny_membership(memberships), credentials)


def _connect_for_actions(status: StatusLine) -> tuple[Bungie, GameData]:
  bungie = _connect(status, ACTION_TOKEN_LIFETIME_SECONDS)
  with status.step('Loading game data'):
    return bungie, GameData(bungie.http, load_preferences(), status)


def _open_state(arguments: argparse.Namespace) -> StateFile:
  state_file = StateFile(dry_run=arguments.dry_run)
  arguments.progress.state_files.append(state_file)
  return state_file


def _open_account(
  bungie: Bungie, game_data: GameData, arguments: argparse.Namespace
) -> Account:
  '''The account as it is right now. Only where things are is fetched.'''
  profile = _read_profile(bungie, arguments, ACCOUNT_COMPONENTS)
  return _account_from_profile(profile, bungie, game_data, arguments)


def _read_profile(
  bungie: Bungie,
  arguments: argparse.Namespace,
  components: tuple[int, ...] = PLAN_COMPONENTS,
) -> dict:
  with arguments.status.step('Reading your account'):
    return bungie.fetch_profile(components)


def _account_from_profile(
  profile: dict,
  bungie: Bungie,
  game_data: GameData,
  arguments: argparse.Namespace,
) -> Account:
  account = Account.from_profile(
    profile,
    game_data.definitions.items,
    DryRunActions() if arguments.dry_run else bungie.client,
    bungie.membership['membershipType'],
    game_data.vault_capacity,
  )
  arguments.progress.accounts.append(account)
  return account


def _warn_if_long(account: Account, plan: PlanView, state_file: StateFile) -> None:
  changes = len(lock_changes(account, plan, state_file.state.tool_locked_ids))
  if changes >= LONG_RUN_ACTIONS:
    minutes = max(1, round(changes * SECONDS_PER_ACTION / 60))
    echo(
      f'Heads up: {count_label(changes, "lock")} to set first; Bungie allows about '
      'one action every 0.1 seconds, so this takes about '
      f'{count_label(minutes, "minute")}. '
      "Don't lock or unlock anything in-game until it finishes: a lock you add "
      "meanwhile could be mistaken for one of the tool's."
    )
    echo()


def _print_dry_run_note(account: Account) -> None:
  if isinstance(account.actions, DryRunActions):
    echo()
    echo(
      'Dry run: nothing was changed. A real run would send '
      f'{count_label(len(account.actions.calls), "action")} to Bungie.'
    )


def _now() -> datetime:
  return datetime.now(UTC)


def _relative(path: Path) -> Path:
  try:
    return path.relative_to(REPOSITORY_ROOT)
  except ValueError:
    return path


def _secure_files() -> None:
  '''Everything the tool writes is readable by you alone, and so is data/.'''
  os.umask(PRIVATE_UMASK)
  make_private_directory(DATA_DIRECTORY)


def _print_recovery_note(arguments: argparse.Namespace) -> None:
  if getattr(arguments, 'dry_run', False):
    echo('Dry run: nothing was changed.', sys.stderr)
  elif arguments.progress.changed_something():
    echo(
      'Everything done so far is recorded. Run the same command again to pick '
      'up where it stopped.',
      sys.stderr,
    )


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(
    prog='sweeper',
    description='Keep or shard Destiny 2 gear at the end of a session.',
  )
  subcommands = parser.add_subparsers(dest='command', required=True)

  login_parser = subcommands.add_parser(
    'login', help='sign in to Bungie in your browser'
  )
  login_parser.add_argument(
    '--no-browser',
    action='store_true',
    help='print the sign-in link instead of opening it',
  )
  login_parser.set_defaults(handler=command_login)

  fetch_parser = subcommands.add_parser(
    'fetch', help='save a read-only inventory snapshot to data/profiles/'
  )
  fetch_parser.set_defaults(handler=command_fetch)

  plan_parser = subcommands.add_parser(
    'plan', help='dry run: decide keep/shard and write a plan to data/runs/'
  )
  plan_parser.add_argument(
    '--profile',
    metavar='SNAPSHOT',
    help=(
      'plan from a saved snapshot (a file name in data/profiles/, or a path) '
      'as a what-if in data/runs/what-if/; apply never uses it'
    ),
  )
  plan_parser.set_defaults(handler=command_plan)

  for name, handler, help_text in (
    ('apply', command_apply, 'start the session: lock keepers and stage shards'),
    ('next', command_next, 'stage the next round of shards'),
    ('ties', command_ties, 'tie step: pick rolls for a weapon Voltron cannot settle'),
    ('done', command_done, 'put your gear back and end the session'),
  ):
    command_parser = subcommands.add_parser(name, help=help_text)
    command_parser.add_argument(
      '--dry-run',
      action='store_true',
      help='show what would happen without changing anything',
    )
    command_parser.set_defaults(handler=handler)
    if name == 'ties':
      choice = command_parser.add_mutually_exclusive_group()
      choice.add_argument(
        '--pick', metavar='NUMBERS', help='pair numbers to keep, e.g. 2,18'
      )
      choice.add_argument('--skip', action='store_true', help='keep every copy for now')

  update_parser = subcommands.add_parser(
    'update-data', help='refresh the Bungie manifest and Voltron wishlist caches now'
  )
  update_parser.add_argument(
    '--force',
    action='store_true',
    help=(
      'download the manifest tables again even if cached, and accept a Voltron '
      'wishlist much smaller than the last one'
    ),
  )
  update_parser.set_defaults(handler=command_update_data)
  return parser


def main(argv: list[str] | None = None) -> int:
  arguments = build_parser().parse_args(argv)
  arguments.progress = Progress()
  # Says which step the command is on; steps end before anything else prints.
  arguments.status = StatusLine(sys.stderr)
  try:
    _secure_files()
    with hold_command_lock(COMMAND_LOCK_PATH):
      arguments.handler(arguments)
  except EXPLAINED_ERRORS as error:
    echo(f'Error: {error}', sys.stderr)
    _print_recovery_note(arguments)
    return 1
  except KeyboardInterrupt:
    echo('\nStopped.', sys.stderr)
    _print_recovery_note(arguments)
    return 130
  return 0


if __name__ == '__main__':
  sys.exit(main())
