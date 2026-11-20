'''The tie step: you pick the trait pairs for a weapon Voltron cannot settle.

A tie is a weapon with no roll of yours in wishlist.yaml and two or more copies
that fully match Voltron rolls. Its copies come into your inventory, Voltron's
trait pairs are listed by number, and the pairs you pick are saved to
wishlist.yaml as your rolls, so the weapon is never a tie again. The numbers
and copy letters are saved when a tie starts and reused until you answer, so a
number always means the pair you were shown.
'''

from __future__ import annotations

import re
import string
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field

from src.account import Account, AccountItem, ItemActionError
from src.game_data import TRAIT_PLUG_CATEGORY
from src.session import (
  PlanView,
  RoundReport,
  clear_inventory,
  reconcile_locks,
  require_session,
  settle_pending_locks,
)
from src.state import CurrentTie, StateFile
from src.terminal import SILENT_STATUS, StatusLine
from src.voltron import VoltronEntry
from src.weapon_rules import describe
from src.weapons import Weapon, normalize_weapon_name
from src.wishlist import WishlistEntry, column_labels

# The most suggested barrels, magazines, and so on listed under each pair.
TOP_PARTS_PER_COLUMN = 3
PART_LABELS = {'magazines_gl': 'magazines'}
SKIP_ANSWERS = ('', 's', 'skip')
QUIT_ANSWERS = ('q', 'quit')
PROMPT = (
  'Pick the pairs to keep, comma-separated (e.g. 1, 3). '
  'Enter skips this weapon for the session, q stops here: '
)
SKIP_ONLY_PROMPT = 'Enter skips this weapon for the session, q stops here: '


@dataclass(frozen=True)
class TraitPairOption:
  traits: tuple[str, ...]
  suggestions: int  # Voltron rolls that include this pair
  parts: tuple[tuple[str, tuple[str, ...]], ...]  # ('barrels', most suggested)
  owners: tuple[str, ...]  # letters of your copies that can roll it


@dataclass
class CopiesBroughtIn:
  '''What bringing a tied weapon's copies in changed, and where each copy was.'''

  report: RoundReport
  where: dict[str, str] = field(default_factory=dict)
  # Copies that stayed where they were for lack of room.
  left_out: int = 0


def copy_letters(copy_ids: list[str]) -> dict[str, str]:
  '''A, B, C... for a weapon's copies, in plan order (oldest first).'''
  return {
    instance_id: string.ascii_uppercase[index]
    if index < len(string.ascii_uppercase)
    else str(index + 1)
    for index, instance_id in enumerate(copy_ids)
  }


def trait_pair_options(
  entries: list[VoltronEntry], copies_by_letter: dict[str, Weapon]
) -> list[TraitPairOption]:
  '''Voltron's trait pairs for one weapon: yours first, then most suggested.

  Voltron does not rank rolls, so "most suggested" counts how many of its
  barrel and magazine combinations include the pair.
  '''
  suggestions: Counter[frozenset[str]] = Counter()
  first_seen: dict[frozenset[str], tuple[str, ...]] = {}
  part_counts: dict[frozenset[str], dict[str, Counter[str]]] = defaultdict(
    lambda: defaultdict(Counter)
  )
  label_order: dict[str, int] = {}
  for entry in entries:
    traits = entry.traits
    if not traits:
      continue
    key = frozenset(traits)
    first_seen.setdefault(key, traits)
    suggestions[key] += 1
    for category, perk in entry.parts:
      label = PART_LABELS.get(category, category)
      if label:
        label_order.setdefault(label, len(label_order))
        part_counts[key][label][perk] += 1
  options = []
  for key, traits in first_seen.items():
    parts = tuple(
      (label, tuple(perk for perk, _ in counts.most_common(TOP_PARTS_PER_COLUMN)))
      for label, counts in sorted(
        part_counts[key].items(), key=lambda item: label_order[item[0]]
      )
    )
    options.append(
      TraitPairOption(
        traits, suggestions[key], parts, _owners(traits, copies_by_letter)
      )
    )
  options.sort(
    key=lambda option: (not option.owners, -option.suggestions, option.traits)
  )
  return options


def options_in_saved_order(
  saved_pairs: list[list[str]],
  options: list[TraitPairOption],
  copies_by_letter: dict[str, Weapon],
) -> list[TraitPairOption]:
  '''The pairs as they were first listed, so the numbers never change while a
  tie waits for your answer. Suggestions and owners are brought up to date.'''
  by_traits = {frozenset(option.traits): option for option in options}
  return [
    by_traits.get(frozenset(traits))
    or TraitPairOption(tuple(traits), 0, (), _owners(tuple(traits), copies_by_letter))
    for traits in saved_pairs
  ]


def bring_copies_in(
  account: Account,
  plan: PlanView,
  state_file: StateFile,
  tie: dict,
  status: StatusLine = SILENT_STATUS,
) -> CopiesBroughtIn:
  '''Clear the way, then move the tied weapon's copies into your inventory.

  Copies that do not fit (the slot holds shards, or there are more copies than
  it has room for) stay where they are and are listed with where they are.
  '''
  session = require_session(state_file)
  if session.current_tie is None or session.current_tie.weapon != tie['weapon']:
    session.current_tie = CurrentTie(
      weapon=tie['weapon'], group_name=tie['group_name'], copies=list(tie['copies'])
    )
    state_file.save()
  settle_pending_locks(account, state_file)
  result = CopiesBroughtIn(RoundReport(session.class_name, session.round))
  reconcile_locks(account, plan, state_file, result.report, status)
  clear_inventory(account, plan, state_file, result.report, status)

  to_move: dict[int, list[AccountItem]] = defaultdict(list)
  for copy_id in tie['copies']:
    item = account.items.get(copy_id)
    if item is None:
      continue
    if item.is_carried_by(session.character_id):
      result.where[copy_id] = 'already here'
    elif item.is_movable:
      result.where[copy_id] = (
        'from the vault'
        if item.location == 'vault'
        else f'from your {account.characters[item.character_id].class_name}'
      )
      to_move[item.bucket_hash].append(item)
    else:
      result.where[copy_id] = f'{account.describe_location(item)}, not moved'
  carried_ids = set(session.carried)
  fitting: list[AccountItem] = []
  for bucket_hash, items in to_move.items():
    room = max(account.free_slots(session.character_id, bucket_hash), 0)
    for item in items[room:]:
      result.where[item.instance_id] = (
        f'{account.describe_location(item)}, no room to bring it in'
      )
      result.left_out += 1
    fitting.extend(items[:room])
  with status.step(f'Bringing in your {tie["weapon"]} copies', len(fitting)):
    for item in fitting:
      status.advance()
      # Remember where it came from before it moves; keepers you were carrying
      # go back to your inventory at done anyway.
      if (
        item.instance_id not in carried_ids
        and item.instance_id not in session.tie_origins
      ):
        session.tie_origins[item.instance_id] = item.character_id or 'vault'
        state_file.save()
      try:
        account.move_to_character(item, session.character_id)
      except ItemActionError as error:
        result.report.problems.append(str(error))
        result.where[item.instance_id] = f'{account.describe_location(item)}, not moved'
  return result


def format_tie_step(
  tie: dict,
  copies_by_letter: dict[str, Weapon],
  where: dict[str, str],
  options: list[TraitPairOption],
  ties_left: int,
) -> str:
  if not copies_by_letter:
    return f'Tie step: {tie["weapon"]} has no copies left.'
  first_copy = next(iter(copies_by_letter.values()))
  other_ties = ties_left - 1
  slots = sorted({weapon.slot for weapon in copies_by_letter.values()})
  slot_text = (
    f'the {slots[0]} slot'
    if len(slots) == 1
    else f'the {", ".join(slots[:-1])} and {slots[-1]} slots'
  )
  lines = [
    f'Tie step: {tie["weapon"]} ({first_copy.weapon_type}). '
    + (
      f'{other_ties} more tied weapon{"s" if other_ties != 1 else ""} after this one.'
      if other_ties
      else 'This is the last tied weapon.'
    ),
    f'Its copies, in your inventory in {slot_text} unless noted:',
  ]
  for letter, weapon in copies_by_letter.items():
    lines.append(
      f'  {letter}  {describe(weapon)} ({where.get(weapon.instance_id, "?")})'
    )
    trait_text = _trait_text(weapon)
    if trait_text:
      lines.append(f'     traits: {trait_text}')
  lines.append('')
  if not options:
    lines.append(
      'Voltron lists no trait pairs for it, so there is nothing to pick. Skip it '
      'for this session.'
    )
  else:
    lines.append("Voltron's trait pairs, the ones your copies can roll first:")
  for number, option in enumerate(options, start=1):
    mark = f'   <- you have: {", ".join(option.owners)}' if option.owners else ''
    lines.append(f'{number:>4}. {" + ".join(option.traits)}{mark}')
    for label, perks in option.parts:
      lines.append(f'        {label}: {", ".join(perks)}')
  return '\n'.join(lines)


def parse_pick(answer: str, option_count: int) -> list[int] | None:
  '''Pair numbers from an answer like "2, 18". None means skip.'''
  text = answer.strip().lower()
  if text in SKIP_ANSWERS:
    return None
  numbers: list[int] = []
  for part in re.split(r'[,\s]+', text):
    if not part:
      continue
    if not part.isdigit():
      raise ValueError(f"'{part}' is not a pair number")
    number = int(part)
    if not option_count:
      raise ValueError('there are no pairs to pick, so skip this weapon instead')
    if not 1 <= number <= option_count:
      raise ValueError(f'there is no pair {number}; pick from 1 to {option_count}')
    if number not in numbers:
      numbers.append(number)
  if not numbers:
    raise ValueError('give at least one pair number, like 2 or 2, 18')
  return numbers


def pairs_for_pick(options: list[list[str]], numbers: list[int]) -> list[list[str]]:
  return [list(options[number - 1]) for number in numbers]


def rolls_for_pairs(
  pairs: list[list[str]],
  added: str = '',
  game_version: str = '',
  categories_by_perk: Mapping[str, frozenset[str]] | None = None,
) -> list[WishlistEntry]:
  '''Your picked pairs as wishlist rolls, labeled with the pair itself.

  Each records the date and the game version it was picked in, and, given
  each perk's plug categories (perk_categories), the column it goes under.
  '''
  return [
    WishlistEntry(
      ' + '.join(traits),
      tuple(traits),
      added,
      game_version,
      columns=column_labels(traits, categories_by_perk) if categories_by_perk else (),
    )
    for traits in pairs
  ]


def format_pick_outcome(
  current_tie: CurrentTie, new_plan: dict, old_actions: dict[str, str]
) -> str:
  '''What the new plan does with each copy, by the letters you were shown.

  Your rolls cover every version of a weapon, so a pick for the shiny version
  also decides the normal one (and the other way around); those copies are
  named too.
  '''
  items = new_plan.get('items') or {}
  kept, sharded = [], []
  for instance_id, letter in current_tie.letters.items():
    entry = items.get(instance_id)
    if entry:
      (kept if entry['action'] == 'keep' else sharded).append(letter)
  outcome = f'{current_tie.weapon}: keeping {", ".join(kept) or "none"}'
  if sharded:
    outcome += (
      f'; {", ".join(sharded)} {"is" if len(sharded) == 1 else "are"} now '
      f'{"a shard" if len(sharded) == 1 else "shards"}'
    )
  outcome += '.'
  also_decided = [
    f'{_copy_text(entry)} is now {"kept" if entry["action"] == "keep" else "a shard"}'
    for instance_id, entry in items.items()
    if instance_id not in current_tie.letters
    and entry.get('kind') == 'weapon'
    and normalize_weapon_name(entry['name']) == current_tie.group_name
    and old_actions.get(instance_id) not in (None, entry['action'])
  ]
  if also_decided:
    outcome += (
      f' The pick covers every version of {current_tie.group_name}, so it also '
      f'decides: {"; ".join(also_decided)}.'
    )
  return outcome


def _owners(traits: tuple[str, ...], copies_by_letter: dict[str, Weapon]) -> tuple:
  return tuple(
    letter for letter, weapon in copies_by_letter.items() if weapon.can_roll(traits)
  )


def _copy_text(entry: dict) -> str:
  '''A copy not listed in the tie step: "Hammerhead (shiny, T4, vault)".'''
  details = [
    *(['shiny'] if entry.get('shiny') else []),
    f'T{entry["tier"]}' if entry.get('tier') else 'untiered',
    entry.get('location', '?'),
  ]
  return f'{entry["name"]} ({", ".join(details)})'


def _trait_text(weapon: Weapon) -> str:
  trait_columns = [
    column
    for column, category in zip(
      weapon.perk_columns, weapon.column_categories, strict=False
    )
    if category == TRAIT_PLUG_CATEGORY
  ]
  if weapon.is_crafted:
    # A crafted copy can be reshaped into any pair its columns hold.
    selected = [column[0] for column in trait_columns]
    return f'{" + ".join(selected)} (crafted: can switch to any pair it can roll)'
  return ' + '.join(' | '.join(column) for column in trait_columns)
