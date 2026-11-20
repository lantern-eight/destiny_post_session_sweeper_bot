'''The end-of-session flow behind apply, next, and done (see README.md).

Every command works from the latest plan and the live account: your locks
always win, items the plan does not cover (drops since planning) are left
alone, and equipped items never move. The one exception to acting only as the
plan says: everything in the session character's Postmaster goes to the vault
first, planned or not, so nothing is lost to a full Postmaster. The state file
is saved after every lock change and before any move it depends on, so an
interrupted run can simply be run again.
'''

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from src.account import Account, AccountItem, ItemActionError
from src.game_data import SLOTS_BY_BUCKET_HASH
from src.paths import RUNS_DIRECTORY
from src.plan import PlanFileError, latest_plan_path, read_plan
from src.state import Session, StateFile, ToolState
from src.terminal import SILENT_STATUS, StatusLine

# A session this old, or on a character you are no longer playing, is finished
# (your gear put back) before a new one starts.
STALE_SESSION_AGE = timedelta(hours=12)
# apply only starts from a plan you reviewed recently.
MAXIMUM_PLAN_AGE = timedelta(hours=2)
# Kept copies of one weapon brought in next to its shards, at most.
COMPARE_COPIES_PER_WEAPON = 2
SLOT_ORDER = {
  bucket_hash: index for index, bucket_hash in enumerate(SLOTS_BY_BUCKET_HASH)
}


class SessionError(RuntimeError):
  '''The command cannot run right now; the message says what to do instead.'''


@dataclass(frozen=True)
class PlanView:
  '''The parts of a saved plan the flow acts on.'''

  path: Path
  generated: datetime
  actions: dict[str, str]  # instance ID -> keep | shard | review
  # Items the plan kept only because you had locked them.
  kept_for_your_lock: frozenset[str]
  ties: list[dict]
  # Items in-game loadouts use (keepers included): instance ID -> loadout labels.
  loadouts_by_item: dict[str, list[str]] = field(default_factory=dict)
  # Weapon shards: the kept copies to bring in next to them, to compare.
  compare_with: dict[str, tuple[str, ...]] = field(default_factory=dict)

  @classmethod
  def from_plan(cls, path: Path, plan: dict) -> PlanView:
    items = plan.get('items') or {}
    return cls(
      path=path,
      generated=datetime.fromisoformat(plan['generated']),
      actions={instance_id: entry['action'] for instance_id, entry in items.items()},
      kept_for_your_lock=frozenset(
        entry['id'] for entry in plan.get('locked_but_outdated') or []
      ),
      ties=list(plan.get('ties') or []),
      loadouts_by_item={
        instance_id: entry['loadouts']
        for instance_id, entry in items.items()
        if entry.get('loadouts')
      },
      compare_with={
        instance_id: tuple(entry['compare_with'])
        for instance_id, entry in items.items()
        if entry.get('compare_with')
      },
    )

  @classmethod
  def load_latest(cls, runs_directory: Path = RUNS_DIRECTORY) -> PlanView:
    path = latest_plan_path(runs_directory)
    if path is None:
      raise SessionError('There is no plan yet. Run `uv run sweeper plan` first.')
    try:
      return cls.from_plan(path, read_plan(path))
    except PlanFileError as error:
      raise SessionError(str(error)) from error


def effective_action(
  item: AccountItem, plan: PlanView, tool_locked_ids: set[str]
) -> str | None:
  '''What to do with an item now: the plan's decision, but live locks win.

  None means the plan does not cover the item (it dropped after planning), so
  it is left alone.
  '''
  action = plan.actions.get(item.instance_id)
  if action is None:
    return None
  if item.is_locked and item.instance_id not in tool_locked_ids:
    return 'keep'  # your lock, possibly placed since planning
  if item.instance_id in plan.kept_for_your_lock:
    return 'shard'  # kept only for your lock, which you have since removed
  return action


@dataclass
class RoundReport:
  class_name: str
  round_number: int
  new_plan: str | None = None
  locked: int = 0
  # Shards the tool had locked and has now unlocked, by name.
  unlocked: list[str] = field(default_factory=list)
  moved_to_vault: int = 0
  # Set on the first round: keepers you were carrying, back at done.
  carried_keepers: int | None = None
  # Postmaster items that made it to the vault.
  pulled: int = 0
  dismantled: int = 0
  # Staged shards you locked in-game instead of dismantling.
  kept_by_you: list[str] = field(default_factory=list)
  # Staged shards a newer plan keeps (after a tie-step pick, say).
  kept_by_new_plan: list[str] = field(default_factory=list)
  staged_now: Counter[str] = field(default_factory=Counter)
  # After the round: shards in your inventory, by slot.
  waiting: Counter[str] = field(default_factory=Counter)
  # Kept weapons next to their shards in your inventory, to compare against.
  comparing: list[str] = field(default_factory=list)
  still_to_stage: Counter[str] = field(default_factory=Counter)
  other_classes: Counter[str] = field(default_factory=Counter)
  equipped_shards: list[str] = field(default_factory=list)
  # Shards the tool cannot bring to you, with where they are.
  stuck_shards: list[str] = field(default_factory=list)
  # Shards in your inventory that an in-game loadout uses.
  in_loadouts: list[str] = field(default_factory=list)
  not_in_plan: list[str] = field(default_factory=list)
  ties_left: int = 0
  problems: list[str] = field(default_factory=list)


@dataclass
class FinishReport:
  class_name: str
  locked: int = 0
  unlocked: list[str] = field(default_factory=list)
  restored: int = 0
  returned: int = 0
  # Kept weapons that were next to their shards, sent back to the vault.
  returned_compared: int = 0
  made_room: int = 0
  # Keepers you were carrying that could not go back, with where they are.
  not_restored: list[str] = field(default_factory=list)
  # The session's character was deleted, so nothing could go back to it.
  character_gone: bool = False
  shards_left: int = 0
  other_classes: Counter[str] = field(default_factory=Counter)
  # Shards still in the vault or on other characters, for another session.
  shards_elsewhere: int = 0
  problems: list[str] = field(default_factory=list)


def check_plan_is_fresh(plan: PlanView, now: datetime) -> None:
  '''A session starts only from a plan you reviewed recently.'''
  plan_age = now - plan.generated
  if plan_age > MAXIMUM_PLAN_AGE:
    hours = plan_age.total_seconds() / 3600
    raise SessionError(
      f'The latest plan is {hours:.1f} hours old. Run `uv run sweeper plan`, '
      'review it, then apply.'
    )


def is_stale(session: Session, account: Account, now: datetime) -> bool:
  '''Over 12 hours old, or on a character you are no longer playing (or that
  was deleted).'''
  return (
    session.character_id != account.latest_character_id()
    or now - session.started_at > STALE_SESSION_AGE
  )


def start_session(
  account: Account,
  plan: PlanView,
  state_file: StateFile,
  now: datetime,
  not_carried: frozenset[str] = frozenset(),
  status: StatusLine = SILENT_STATUS,
) -> RoundReport:
  '''Record the keepers you are carrying, then run the first round.

  Carried means locked in your inventory (by you, or by the tool in an earlier
  session) and not a shard; drops the plan does not cover count too.
  *not_carried* leaves out keepers that finishing a stale session locked a
  moment ago: they are new keepers, which stay in the vault.
  '''
  check_plan_is_fresh(plan, now)
  settle_pending_locks(account, state_file)
  state = state_file.state
  character_id = account.latest_character_id()
  carried = [
    item.instance_id
    for item in account.inventory(character_id)
    if item.is_locked
    and item.transferable
    and item.instance_id not in not_carried
    and effective_action(item, plan, state.tool_locked_ids) != 'shard'
  ]
  state.session = Session(
    character_id=character_id,
    class_name=account.characters[character_id].class_name,
    started=now.isoformat(timespec='seconds'),
    plan=plan.path.name,
    carried=carried,
  )
  state_file.save()
  return run_round(account, plan, state_file, status)


def run_round(
  account: Account,
  plan: PlanView,
  state_file: StateFile,
  status: StatusLine = SILENT_STATUS,
) -> RoundReport:
  '''One round: keepers out, Postmaster to the vault, locks, then shards in.

  *status* shows each step as it goes.
  '''
  session = require_session(state_file)
  if session.character_id not in account.characters:
    raise SessionError(
      f'The {session.class_name} this session runs on no longer exists. Run '
      '`uv run sweeper done` to end the session.'
    )
  settle_pending_locks(account, state_file)
  state = state_file.state
  report = RoundReport(session.class_name, session.round + 1)
  if session.round and plan.path.name != session.plan:
    report.new_plan = plan.path.name
  if session.round == 0:
    report.carried_keepers = len(session.carried)
  report.dismantled = sum(
    1 for instance_id in session.staged if instance_id not in account.instance_ids
  )
  clear_inventory(account, plan, state_file, report, status)
  _empty_postmaster(account, session, report, status)
  reconcile_locks(account, plan, state_file, report, status)
  _stage_shards(account, plan, state, session, report, status)
  _summarize(account, plan, state, session, report)
  session.round += 1
  session.plan = plan.path.name
  state_file.save()
  return report


def finish_session(
  account: Account,
  plan: PlanView,
  state_file: StateFile,
  status: StatusLine = SILENT_STATUS,
) -> FinishReport:
  '''Put your gear back and end the session: tie-step copies where they came
  from, then the keepers you were carrying.'''
  state = state_file.state
  session = require_session(state_file)
  report = FinishReport(session.class_name)
  settle_pending_locks(account, state_file)
  reconcile_locks(account, plan, state_file, report, status)
  carried_ids = set(session.carried)
  tie_copies = {
    instance_id: origin
    for instance_id, origin in session.tie_origins.items()
    if instance_id not in carried_ids
    and _can_return(account.items.get(instance_id), plan, state)
  }
  with status.step('Returning tie-step copies', len(tie_copies)):
    for instance_id, origin in tie_copies.items():
      status.advance()
      _return_tie_copy(account, account.items[instance_id], origin, report)
  compared = [
    account.items[instance_id]
    for instance_id in session.compared
    if instance_id not in carried_ids
    and _can_return(account.items.get(instance_id), plan, state)
    and account.items[instance_id].is_carried_by(session.character_id)
  ]
  with status.step('Returning the kept copies you compared against', len(compared)):
    for item in compared:
      status.advance()
      try:
        account.move_to_vault(item)
      except ItemActionError as error:
        report.problems.append(f'{error}; it stays {account.describe_location(item)}')
        continue
      report.returned_compared += 1
  if session.character_id in account.characters:
    _restore_carried(account, plan, state_file, session, report, status)
  else:
    report.character_gone = True
  _count_leftover_shards(account, plan, state, session, report)
  state.session = None
  state_file.save()
  return report


def settle_pending_locks(account: Account, state_file: StateFile) -> None:
  '''Settle locks whose reply never came back (a timeout, or Ctrl-C).

  An item locked by now was locked by the tool; one that is not locked never
  was.
  '''
  state = state_file.state
  if not state.pending_lock_ids:
    return
  for instance_id in state.pending_lock_ids:
    item = account.items.get(instance_id)
    if item is not None and item.is_locked:
      state.tool_locked_ids.add(instance_id)
  state.pending_lock_ids.clear()
  state_file.save()


def reconcile_locks(
  account: Account,
  plan: PlanView,
  state_file: StateFile,
  report: RoundReport | FinishReport,
  status: StatusLine = SILENT_STATUS,
) -> None:
  '''Lock unlocked keepers and unlock the tool's own locks on shards.

  Items in the Postmaster are locked after they are pulled. Your own locks are
  never removed.
  '''
  state = state_file.state
  session = require_session(state_file)
  # Vault items are locked through a character: the session's, while it exists.
  acting_character_id = (
    session.character_id
    if session.character_id in account.characters
    else account.latest_character_id()
  )
  changes = lock_changes(account, plan, state.tool_locked_ids)
  with status.step(_lock_step_label(changes), len(changes)):
    for item, locked in changes:
      status.advance()
      _set_locked(account, item, locked, acting_character_id, state_file, report)
  # Forget tool locks you removed in-game, and on items you dismantled.
  stale_ids = {
    instance_id
    for instance_id in state.tool_locked_ids
    if instance_id not in account.instance_ids
    or (instance_id in account.items and not account.items[instance_id].is_locked)
  }
  if stale_ids:
    state.tool_locked_ids -= stale_ids
    state_file.save()


def lock_changes(
  account: Account, plan: PlanView, tool_locked_ids: set[str]
) -> list[tuple[AccountItem, bool]]:
  '''(item, lock it?) for keepers to lock and the tool's own locks on shards.'''
  changes = []
  for item in account.items.values():
    if item.location == 'postmaster' or not item.lockable:
      continue
    action = effective_action(item, plan, tool_locked_ids)
    if action == 'keep' and not item.is_locked:
      changes.append((item, True))
    elif action == 'shard' and item.is_locked and item.instance_id in tool_locked_ids:
      changes.append((item, False))
  return changes


def clear_inventory(
  account: Account,
  plan: PlanView,
  state_file: StateFile,
  report: RoundReport,
  status: StatusLine = SILENT_STATUS,
) -> None:
  '''Move everything but shards out of the inventory: keepers go to the vault.

  Keepers you were carrying come back at done. The tied weapon waiting for
  your pick stays put, and so do kept weapons next to shards of theirs.
  '''
  state = state_file.state
  session = require_session(state_file)
  current_tie = session.current_tie
  waiting_copies = (
    set(current_tie.copies) if current_tie and not current_tie.answered else set()
  )
  staying = waiting_copies | _copies_still_compared(account, plan, state, session)
  keepers = [
    item
    for item in account.inventory(session.character_id)
    if item.instance_id not in staying
    and item.transferable
    and effective_action(item, plan, state.tool_locked_ids) not in (None, 'shard')
  ]
  staged = set(session.staged)
  with status.step('Moving keepers from your inventory to the vault', len(keepers)):
    for item in keepers:
      status.advance()
      try:
        account.move_to_vault(item)
      except ItemActionError as error:
        report.problems.append(str(error))
        continue
      report.moved_to_vault += 1
      if item.instance_id in staged:
        if item.is_locked and item.instance_id not in state.tool_locked_ids:
          report.kept_by_you.append(item.name)  # you locked it in-game
        else:
          report.kept_by_new_plan.append(item.name)


def open_ties(plan: PlanView, session: Session, account: Account) -> list[dict]:
  '''Tied weapons still waiting for your pick this session, in plan order.'''
  return [
    tie
    for tie in plan.ties
    if tie['weapon'] not in session.skipped_ties
    and sum(1 for instance_id in tie['tied'] if instance_id in account.items) >= 2
  ]


def require_session(state_file: StateFile) -> Session:
  '''The session in progress, or an error saying to start one.'''
  session = state_file.state.session
  if session is None:
    raise SessionError(
      'No session is in progress. Start one with `uv run sweeper apply`.'
    )
  return session


def _empty_postmaster(
  account: Account, session: Session, report: RoundReport, status: StatusLine
) -> None:
  '''Send this character's Postmaster weapons and armor to the vault.

  Everything goes, planned or not, so nothing is lost to a full Postmaster.
  Bungie can only pull into the character's inventory, so each item passes
  through it on the way. Keepers are then locked like any other; shards come
  back in the rounds. Armor for another class stays put.
  '''
  postmaster_items = [
    item
    for item in account.items.values()
    if item.location == 'postmaster'
    and item.character_id == session.character_id
    and item.fits_class(session.class_name)
  ]
  with status.step('Sending your Postmaster to the vault', len(postmaster_items)):
    for item in postmaster_items:
      status.advance()
      try:
        account.pull_from_postmaster(item)
      except ItemActionError as error:
        report.problems.append(str(error))
        continue
      try:
        account.move_to_vault(item)
      except ItemActionError as error:
        report.problems.append(f'{error}; it is in your inventory now')
        continue
      report.pulled += 1


def _stage_shards(
  account: Account,
  plan: PlanView,
  state: ToolState,
  session: Session,
  report: RoundReport,
  status: StatusLine,
) -> None:
  '''Fill each slot with shards: any weapon, and armor for this class.

  The vault goes first; weapon shards on your other characters come through it.
  A weapon's shards come with up to 2 of its kept copies from the vault, so you
  can compare them before you dismantle. Shards already waiting get theirs
  first.
  '''
  waiting: dict[int, list[AccountItem]] = defaultdict(list)
  candidates: dict[int, list[AccountItem]] = defaultdict(list)
  for item in account.items.values():
    if effective_action(item, plan, state.tool_locked_ids) != 'shard':
      continue
    if item.is_carried_by(session.character_id):
      waiting[item.bucket_hash].append(item)
    elif _can_stage(item, session):
      candidates[item.bucket_hash].append(item)

  def staging_order(item: AccountItem) -> tuple:
    return (item.location != 'vault', item.name, int(item.instance_id))

  with status.step('Bringing shards into your inventory'):
    for bucket_hash in sorted(waiting.keys() | candidates.keys(), key=SLOT_ORDER.get):
      free_slots = account.free_slots(session.character_id, bucket_hash)
      for item in sorted(waiting[bucket_hash], key=staging_order):
        copies = _copies_to_compare(account, plan, state, session, item)
        for copy in copies[: max(free_slots, 0)]:
          free_slots -= _bring_in_to_compare(account, session, copy, report, status)
      for item in sorted(candidates[bucket_hash], key=staging_order):
        if free_slots <= 0:
          break
        copies = _copies_to_compare(account, plan, state, session, item)
        if len(copies) + 1 > free_slots:
          continue  # no room for its kept copies too; a later round brings them
        for copy in copies:
          free_slots -= _bring_in_to_compare(account, session, copy, report, status)
        status.advance()
        try:
          account.move_to_character(item, session.character_id)
        except ItemActionError as error:
          report.problems.append(str(error))
          continue
        free_slots -= 1
        report.staged_now[item.slot] += 1


def _bring_in_to_compare(
  account: Account,
  session: Session,
  copy: AccountItem,
  report: RoundReport,
  status: StatusLine,
) -> int:
  '''Move a kept copy in next to its shards. Returns the slots it took.'''
  status.advance()
  try:
    account.move_to_character(copy, session.character_id)
  except ItemActionError as error:
    report.problems.append(str(error))
    return 0
  session.compared.append(copy.instance_id)
  return 1


def _summarize(
  account: Account,
  plan: PlanView,
  state: ToolState,
  session: Session,
  report: RoundReport,
) -> None:
  '''Where every shard is after the round, and what is still to come.'''
  staged = []
  for item in sorted(
    account.items.values(), key=lambda item: (SLOT_ORDER[item.bucket_hash], item.name)
  ):
    action = effective_action(item, plan, state.tool_locked_ids)
    carried = item.is_carried_by(session.character_id)
    if action is None:
      if carried and not item.is_locked:
        report.not_in_plan.append(f'{item.name} ({item.slot})')
      continue
    if action != 'shard':
      continue
    if carried:
      staged.append(item.instance_id)
      report.waiting[item.slot] += 1
      loadout_labels = plan.loadouts_by_item.get(item.instance_id)
      if loadout_labels:
        report.in_loadouts.append(f'{item.name}: {", ".join(loadout_labels)}')
    elif item.location == 'equipped':
      holder = account.characters[item.character_id].class_name
      report.equipped_shards.append(f'{item.name} ({holder})')
    elif not item.fits_class(session.class_name):
      report.other_classes[item.class_name] += 1
    elif _can_stage(item, session):
      report.still_to_stage[item.slot] += 1
    else:
      report.stuck_shards.append(f'{item.name} ({account.describe_location(item)})')
  session.staged = staged
  session.compared = [
    instance_id
    for instance_id in session.compared
    if instance_id in account.items
    and account.items[instance_id].is_carried_by(session.character_id)
  ]
  report.comparing = sorted(
    account.items[instance_id].name for instance_id in session.compared
  )
  report.ties_left = len(open_ties(plan, session, account))


def _copies_to_compare(
  account: Account,
  plan: PlanView,
  state: ToolState,
  session: Session,
  shard: AccountItem,
) -> list[AccountItem]:
  '''Kept copies to bring in from the vault next to *shard*, if not there yet.'''
  compared_names = [
    item.name
    for item in account.inventory(session.character_id)
    if item.instance_id in session.compared
  ]
  copies: list[AccountItem] = []
  for instance_id in plan.compare_with.get(shard.instance_id, ()):
    keeper = account.items.get(instance_id)
    if (
      keeper is None
      or keeper.location != 'vault'
      or not keeper.is_movable
      or keeper.bucket_hash != shard.bucket_hash
      or effective_action(keeper, plan, state.tool_locked_ids) == 'shard'
    ):
      continue
    same_weapon = compared_names.count(keeper.name) + sum(
      1 for copy in copies if copy.name == keeper.name
    )
    if same_weapon < COMPARE_COPIES_PER_WEAPON:
      copies.append(keeper)
  return copies


def _copies_still_compared(
  account: Account, plan: PlanView, state: ToolState, session: Session
) -> set[str]:
  '''Compared copies with shards still in your inventory or still to stage.'''
  if not session.compared:
    return set()
  still_wanted: set[str] = set()
  for item in account.items.values():
    if effective_action(item, plan, state.tool_locked_ids) != 'shard':
      continue
    if item.is_carried_by(session.character_id) or _can_stage(item, session):
      still_wanted.update(plan.compare_with.get(item.instance_id, ()))
  return set(session.compared) & still_wanted


def _can_stage(item: AccountItem, session: Session) -> bool:
  return (
    item.is_movable
    and not item.is_carried_by(session.character_id)
    and item.fits_class(session.class_name)
  )


def _can_return(item: AccountItem | None, plan: PlanView, state: ToolState) -> bool:
  '''Whether a carried keeper or tie-step copy should go back where it was.'''
  if item is None or not item.is_movable:
    return False
  return effective_action(item, plan, state.tool_locked_ids) != 'shard'


def _return_tie_copy(
  account: Account, item: AccountItem, origin: str, report: FinishReport
) -> None:
  '''Send a tie-step keeper home: the vault, or the character it came from.

  If that character has no room, the vault is the next best place.
  '''
  if origin not in account.characters:
    origin = 'vault'  # it came from the vault, or from a deleted character
  if origin == 'vault':
    if item.location == 'vault':
      return  # already home
    try:
      account.move_to_vault(item)
    except ItemActionError as error:
      report.problems.append(f'{error}; it stays {account.describe_location(item)}')
      return
  else:
    if item.is_carried_by(origin):
      return  # already home
    try:
      account.move_to_character(item, origin)
    except ItemActionError as error:
      try:
        account.move_to_vault(item)
      except ItemActionError:
        report.problems.append(f'{error}; it stays {account.describe_location(item)}')
        return
      report.problems.append(f'{error}, so it went to the vault instead')
      return
  report.returned += 1


def _restore_carried(
  account: Account,
  plan: PlanView,
  state_file: StateFile,
  session: Session,
  report: FinishReport,
  status: StatusLine,
) -> None:
  '''Bring the keepers you were carrying back, making room where needed.'''
  restores: dict[int, list[AccountItem]] = defaultdict(list)
  for instance_id in session.carried:
    item = account.items.get(instance_id)
    if item is None or item.is_carried_by(session.character_id):
      continue
    if _can_return(item, plan, state_file.state):
      restores[item.bucket_hash].append(item)
  total = sum(len(items) for items in restores.values())
  with status.step('Putting back the keepers you were carrying', total):
    for bucket_hash in sorted(restores, key=SLOT_ORDER.get):
      items = restores[bucket_hash]
      report.made_room += _make_room(
        account, plan, state_file, session, bucket_hash, len(items), report
      )
      for item in items:
        status.advance()
        try:
          account.move_to_character(item, session.character_id)
        except ItemActionError as error:
          report.not_restored.append(
            f'{item.name} ({item.slot}), {account.describe_location(item)}: '
            f'{error.reason}'
          )
          continue
        report.restored += 1


def _count_leftover_shards(
  account: Account,
  plan: PlanView,
  state: ToolState,
  session: Session,
  report: FinishReport,
) -> None:
  for item in account.items.values():
    if effective_action(item, plan, state.tool_locked_ids) != 'shard':
      continue
    if item.is_carried_by(session.character_id):
      report.shards_left += 1
    elif item.location == 'equipped':
      continue
    elif not item.fits_class(session.class_name):
      report.other_classes[item.class_name] += 1
    else:
      report.shards_elsewhere += 1


def _make_room(
  account: Account,
  plan: PlanView,
  state_file: StateFile,
  session: Session,
  bucket_hash: int,
  needed: int,
  report: FinishReport,
) -> int:
  '''Move leftover shards to the vault until *needed* slots are free.'''
  shortfall = needed - account.free_slots(session.character_id, bucket_hash)
  moved = 0
  for item in account.inventory(session.character_id, bucket_hash):
    if moved >= shortfall:
      break
    action = effective_action(item, plan, state_file.state.tool_locked_ids)
    if action != 'shard' or not item.transferable:
      continue
    try:
      account.move_to_vault(item)
    except ItemActionError as error:
      report.problems.append(str(error))
      continue
    moved += 1
  return moved


def _lock_step_label(changes: list[tuple[AccountItem, bool]]) -> str:
  locking = any(locked for _, locked in changes)
  unlocking = not all(locked for _, locked in changes)
  if locking and unlocking:
    return 'Locking keepers and unlocking shards the tool had locked'
  return 'Locking keepers' if locking else 'Unlocking shards the tool had locked'


def _set_locked(
  account: Account,
  item: AccountItem,
  locked: bool,
  acting_character_id: str,
  state_file: StateFile,
  report: RoundReport | FinishReport,
) -> None:
  '''Lock or unlock, record it as the tool's own lock, and save at once.

  A lock is written down as pending before it is sent, so a reply lost to a
  timeout or Ctrl-C never turns the tool's lock into what looks like yours.
  '''
  state = state_file.state
  if locked:
    state.pending_lock_ids.add(item.instance_id)
    state_file.save()
  try:
    account.set_locked(item, locked, acting_character_id)
  except ItemActionError as error:
    report.problems.append(str(error))
    if locked:
      state.pending_lock_ids.discard(item.instance_id)
      state_file.save()
    return
  if locked:
    state.pending_lock_ids.discard(item.instance_id)
    state.tool_locked_ids.add(item.instance_id)
    report.locked += 1
  else:
    state.tool_locked_ids.discard(item.instance_id)
    report.unlocked.append(item.name)
  state_file.save()
