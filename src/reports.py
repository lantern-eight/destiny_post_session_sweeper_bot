'''Terminal summaries for apply, next, ties, and done.

Each report is a few sections with a blank line between them: what the
command did, what waits for you, anything skipped, and what to run next. A
section's list puts one entry on each line, under a line ending in a colon.
'''

from __future__ import annotations

from collections import Counter

from src.game_data import SLOTS_BY_BUCKET_HASH
from src.session import FinishReport, RoundReport
from src.text import count_label, join_sections

SLOT_NAMES = tuple(SLOTS_BY_BUCKET_HASH.values())


def format_round_report(report: RoundReport) -> str:
  done = [f'Round {report.round_number} on your {report.class_name}:']
  if report.new_plan:
    done.append(f'  Using the newer plan {report.new_plan}.')
  if report.dismantled:
    done.append(
      f'  You dismantled {count_label(report.dismantled, "shard")} since the last '
      'round.'
    )
  done.extend(_clearing_lines(report))
  if report.pulled:
    done.append(
      f'  Pulled {count_label(report.pulled, "item")} from the Postmaster into the '
      'vault.'
    )
  if report.staged_now:
    done.append(f'  Staged {_by_slot(report.staged_now)}.')
  return join_sections(
    done,
    _to_dismantle(report),
    _still_to_come(report),
    _problem_section(report.problems),
    [_next_step(report)],
  )


def format_clearing_report(report: RoundReport) -> str:
  '''What clearing the way for the tie step changed, or "" if nothing did.'''
  lines = _clearing_lines(report)
  return join_sections(
    ['Cleared the way:', *lines] if lines else [], _problem_section(report.problems)
  )


def format_finish_report(report: FinishReport) -> str:
  done = [
    f'Putting your gear back on your {report.class_name}:',
    *_lock_lines(report.locked, report.unlocked),
  ]
  if report.character_gone:
    done.append(
      f'  The {report.class_name} this session ran on no longer exists, so the '
      'keepers you were carrying stay in the vault.'
    )
  else:
    if report.made_room:
      done.append(
        f'  Moved {count_label(report.made_room, "leftover shard")} to the vault to '
        'make room.'
      )
    done.append(
      f'  Put back {count_label(report.restored, "keeper")} you were carrying '
      'before the session.'
    )
  if report.returned:
    done.append(
      f'  Returned {count_label(report.returned, "tie-step copy", "tie-step copies")}'
      ' to where they came from.'
    )
  if report.returned_compared:
    copies = count_label(report.returned_compared, 'kept copy', 'kept copies')
    done.append(f'  Returned {copies} you compared against to the vault.')
  not_restored = _listing('Could not put these back:', report.not_restored)
  left = []
  if report.shards_left:
    left.append(
      f'{count_label(report.shards_left, "shard")} you did not dismantle '
      f'{"is" if report.shards_left == 1 else "are"} still in your inventory, '
      'unlocked.'
    )
  if report.shards_elsewhere:
    left.append(
      f'{count_label(report.shards_elsewhere, "more shard")} '
      f'{"waits" if report.shards_elsewhere == 1 else "wait"} in the vault or on '
      'your other characters for a later session.'
    )
  if report.other_classes:
    left.append(_other_classes_line(report.other_classes))
  return join_sections(
    done, not_restored, left, _problem_section(report.problems), ['Session finished.']
  )


def _to_dismantle(report: RoundReport) -> list[str]:
  '''The shards in your inventory, the kept copies next to them, and the
  loadouts dismantling them breaks.'''
  lines = []
  if report.waiting:
    lines.append(f'In your inventory to dismantle: {_by_slot(report.waiting)}')
  if report.comparing:
    lines.append(
      'Kept copies next to them, to compare (locked; back to the vault after):'
    )
    lines.append(f'  {_with_counts(report.comparing)}')
  lines.extend(
    _listing(
      'Heads up, dismantling these breaks loadouts (see loadouts_affected in the '
      'plan):',
      report.in_loadouts,
    )
  )
  return lines


def _still_to_come(report: RoundReport) -> list[str]:
  '''Shards for later: later rounds, other classes, and ones the tool can't move.'''
  lines = []
  if report.still_to_stage:
    lines.append(f'Left for later rounds: {_by_slot(report.still_to_stage)}')
  if report.other_classes:
    lines.append(_other_classes_line(report.other_classes))
  lines.extend(
    _listing(
      'Equipped, so not staged (swap them out to dismantle them):',
      report.equipped_shards,
    )
  )
  lines.extend(
    _listing(
      'Shards the tool cannot bring to you (dismantle or move them yourself):',
      report.stuck_shards,
    )
  )
  lines.extend(
    _listing(
      'Careful: these are unlocked but not in the plan (new since planning), so '
      'not shards:',
      report.not_in_plan,
    )
  )
  return lines


def _clearing_lines(report: RoundReport) -> list[str]:
  '''Locks changed and keepers moved out of your inventory.'''
  lines = _lock_lines(report.locked, report.unlocked)
  if report.kept_by_you:
    lines.append(f'  Kept because you locked them: {_with_counts(report.kept_by_you)}.')
  if report.kept_by_new_plan:
    lines.append(
      '  No longer shards under the newer plan: '
      f'{_with_counts(report.kept_by_new_plan)}.'
    )
  if report.moved_to_vault:
    comes_back = ''
    if report.carried_keepers:
      verb = 'comes' if report.carried_keepers == 1 else 'come'
      comes_back = f' ({report.carried_keepers} you were carrying {verb} back at done)'
    lines.append(
      f'  Moved {count_label(report.moved_to_vault, "keeper")} from your inventory '
      f'to the vault{comes_back}.'
    )
  return lines


def _with_counts(names: list[str]) -> str:
  '''"Ergo Sum (2), Fatebringer" from a list of names.'''
  return ', '.join(
    f'{name} ({count})' if count > 1 else name for name, count in Counter(names).items()
  )


def _lock_lines(locked: int, unlocked: list[str]) -> list[str]:
  lines = []
  if locked:
    lines.append(f'  Locked {count_label(locked, "keeper")}.')
  if unlocked:
    lines.append(
      f'  Unlocked {count_label(len(unlocked), "shard")} the tool had locked: '
      f'{_with_counts(unlocked)}.'
    )
  return lines


def _listing(heading: str, entries: list[str]) -> list[str]:
  '''*heading*, then one entry on each line under it; nothing without entries.'''
  return [heading, *(f'  {entry}' for entry in entries)] if entries else []


def _other_classes_line(other_classes: Counter[str]) -> str:
  waiting = ', '.join(
    f'{class_name} {count}' for class_name, count in sorted(other_classes.items())
  )
  return f'Armor shards waiting for a session on that class: {waiting}'


def _problem_section(problems: list[str]) -> list[str]:
  return _listing(
    'Skipped:', [f'{problem[0].upper()}{problem[1:]}.' for problem in problems]
  )


def _next_step(report: RoundReport) -> str:
  if report.waiting:
    return 'Next: dismantle those shards in-game, then run `uv run sweeper next`.'
  if report.still_to_stage:
    return 'Next: run `uv run sweeper next` to stage more.'
  if report.ties_left:
    return (
      f'No shards left to stage. Next: `uv run sweeper ties` to pick rolls for '
      f'{count_label(report.ties_left, "tied weapon")}, or `uv run sweeper done` '
      'to put your gear back.'
    )
  return 'No shards left to stage. Next: `uv run sweeper done` puts your gear back.'


def _by_slot(counts: Counter[str]) -> str:
  total = sum(counts.values())
  slots = ', '.join(f'{slot} {counts[slot]}' for slot in SLOT_NAMES if counts[slot])
  return f'{count_label(total, "shard")} ({slots})'
