'''Build, save, and summarize a dry-run plan. Planning never changes the account.'''

from __future__ import annotations

import difflib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml

from src.armor_rules import Decision, decide_armor
from src.definitions import (
  definition_name,
  masterwork_stat,
  normalize_perk_name,
  perk_categories,
  plug_category,
)
from src.files import prune_old_files, write_atomically
from src.game_data import (
  CLASS_NAMES,
  COLUMN_PLUG_CATEGORIES,
  ITEM_TYPE_WEAPON,
  PRIMARY_PERK_PLUG_CATEGORIES,
  TRAIT_PLUG_CATEGORY,
)
from src.inventory import ArmorPiece, build_armor_pieces
from src.loadouts import Loadout, read_loadouts
from src.paths import RUNS_DIRECTORY
from src.preferences import Preferences
from src.profile_snapshot import display_name
from src.text import count_label, join_sections
from src.voltron import VoltronEntry
from src.weapon_rules import decide_weapons
from src.weapons import Weapon, alternatives, build_weapons, normalize_weapon_name
from src.wishlist import COLUMN_NAMES, WishlistEntry, column_plug_categories

ACTIONS = ('keep', 'shard', 'review')
ACTION_ORDER = {'keep': 0, 'review': 1, 'shard': 2}
PLAN_SUFFIX = '-plan.yaml'
# Plans made from a saved snapshot (`plan --profile`) are for trying config
# changes, so they go where apply never looks.
WHAT_IF_DIRECTORY = RUNS_DIRECTORY / 'what-if'
PLANS_TO_KEEP = 25
# This many former keepers turning into shards at once deserves a closer look.
BIG_CHANGE_COUNT = 20
# The summary's count rows line their numbers up after this many characters.
COUNT_LABEL_WIDTH = 13
# PyYAML's C parser and emitter read and write a 400 KB plan several times faster.
YAML_LOADER = getattr(yaml, 'CSafeLoader', yaml.SafeLoader)
YAML_DUMPER = getattr(yaml, 'CSafeDumper', yaml.SafeDumper)


class PlanFileError(RuntimeError):
  '''A saved plan is empty or cannot be read.'''


@dataclass(frozen=True)
class Definitions:
  '''Manifest tables the rules need, keyed by hash as strings.'''

  items: dict
  sets: dict
  plug_sets: dict
  loadout_names: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Wishlists:
  personal: dict[str, list[WishlistEntry]]
  voltron: dict[str, list[VoltronEntry]]


def build_plan(
  profile: dict,
  definitions: Definitions,
  preferences: Preferences,
  wishlists: Wishlists,
  previous_keepers: set[str],
  tool_locked_ids: set[str],
  now: datetime | None = None,
  snapshot_name: str | None = None,
) -> dict:
  '''Decide every weapon and armor piece. *snapshot_name* marks a what-if plan
  made from a saved snapshot instead of the live account.'''
  pieces = build_armor_pieces(profile, definitions.items, definitions.sets)
  weapons = build_weapons(profile, definitions.items, definitions.plug_sets)
  weapon_decisions = decide_weapons(
    weapons,
    preferences,
    wishlists.personal,
    wishlists.voltron,
    previous_keepers,
    primary_perk_names(definitions.items),
  )
  decisions = {
    **decide_armor(pieces, preferences, previous_keepers),
    **weapon_decisions.decisions,
  }
  locked_but_outdated = apply_user_locks(
    [*pieces, *weapons], decisions, tool_locked_ids
  )
  newly_sharded = _newly_sharded(
    [*pieces, *weapons], decisions, previous_keepers, tool_locked_ids
  )
  items = {}
  for piece in sorted(
    pieces,
    key=lambda piece: (
      ACTION_ORDER[decisions[piece.instance_id].action],
      piece.class_name,
      piece.slot,
      piece.name,
      piece.instance_id,
    ),
  ):
    items[piece.instance_id] = _armor_entry(piece, decisions[piece.instance_id])
  for weapon in sorted(
    weapons,
    key=lambda weapon: (
      ACTION_ORDER[decisions[weapon.instance_id].action],
      weapon.rarity,
      weapon.slot,
      weapon.group_name,
      weapon.instance_id,
    ),
  ):
    items[weapon.instance_id] = _weapon_entry(weapon, decisions[weapon.instance_id])
  loadouts_affected = _loadouts_affected(
    read_loadouts(profile, definitions.items, definitions.loadout_names), items
  )
  plan: dict = {
    'generated': (now or datetime.now(UTC)).isoformat(timespec='seconds'),
  }
  if snapshot_name:
    plan['from_snapshot'] = snapshot_name
  plan.update(
    {
      'account': display_name(
        profile.get('profile', {}).get('data', {}).get('userInfo', {})
      ),
      'current_character': _current_character(profile),
      'scope': ['armor', 'weapons'],
      'summary': {
        **_summary(pieces, weapons, decisions, locked_but_outdated),
        'newly_sharded': len(newly_sharded),
        'ties': len(weapon_decisions.ties),
        'crafting': len(weapon_decisions.crafting),
        'loadouts_affected': len(loadouts_affected),
      },
      'warnings': _wishlist_warnings(wishlists.personal, definitions.items),
      'locked_but_outdated': locked_but_outdated,
      'newly_sharded': newly_sharded,
      'ties': weapon_decisions.ties,
      'crafting': [
        {'weapon': weapon, 'copies': copies}
        for weapon, copies in weapon_decisions.crafting.items()
      ],
      'loadouts_affected': loadouts_affected,
      'items': items,
    }
  )
  return plan


def primary_perk_names(item_definitions: dict) -> frozenset[str]:
  '''Every perk name in a primary column (the traits): a copy must have the
  ones your roll lists.'''
  return frozenset(
    normalize_perk_name(definition_name(definition))
    for definition in item_definitions.values()
    if plug_category(definition) in PRIMARY_PERK_PLUG_CATEGORIES
  )


def apply_user_locks(
  items: list[ArmorPiece | Weapon],
  decisions: dict[str, Decision],
  tool_locked_ids: set[str],
) -> list[dict]:
  '''User locks always win. Returns the items kept only because of a lock.'''
  locked_but_outdated = []
  for item in items:
    if not item.is_locked or item.instance_id in tool_locked_ids:
      continue
    decision = decisions[item.instance_id]
    if decision.action == 'shard':
      locked_but_outdated.append(
        {**_item_label(item), 'rules_say': decision.reasons[0]}
      )
      decisions[item.instance_id] = Decision(
        'keep', ['locked by you (the rules would shard it)']
      )
    elif decision.action == 'keep':
      decision.reasons.append('locked by you')
  return locked_but_outdated


def save_plan(plan: dict, runs_directory: Path = RUNS_DIRECTORY) -> Path:
  '''Write the plan under its time, keeping only the newest PLANS_TO_KEEP.'''
  generated = datetime.fromisoformat(plan['generated'])
  path = runs_directory / f'{generated.strftime("%Y%m%dT%H%M%SZ")}{PLAN_SUFFIX}'
  write_atomically(
    path,
    yaml.dump(plan, Dumper=YAML_DUMPER, sort_keys=False, allow_unicode=True, width=100),
  )
  prune_old_files(runs_directory, f'*{PLAN_SUFFIX}', PLANS_TO_KEEP)
  return path


def latest_plan_path(runs_directory: Path = RUNS_DIRECTORY) -> Path | None:
  '''The newest saved plan, or None before the first `plan`.'''
  if not runs_directory.exists():
    return None
  plan_paths = sorted(runs_directory.glob(f'*{PLAN_SUFFIX}'))
  return plan_paths[-1] if plan_paths else None


def read_plan(path: Path) -> dict:
  '''A saved plan, or PlanFileError if it is empty or damaged.'''
  try:
    plan = yaml.load(path.read_text(encoding='utf-8'), Loader=YAML_LOADER)
  except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
    raise PlanFileError(
      f'The plan {path.name} cannot be read ({error}). '
      'Run `uv run sweeper plan` to make a new one.'
    ) from error
  if not isinstance(plan, dict) or 'generated' not in plan or 'items' not in plan:
    raise PlanFileError(
      f'The plan {path.name} is empty or cut short. '
      'Run `uv run sweeper plan` to make a new one.'
    )
  return plan


def load_previous_keepers(runs_directory: Path = RUNS_DIRECTORY) -> set[str]:
  '''What the newest plan kept by its rules, for the "kept last run" tiebreak.

  Items it kept only because you had locked them don't count: your lock kept
  them, not the rules. A damaged newest plan counts as no history.
  '''
  path = latest_plan_path(runs_directory)
  if path is None:
    return set()
  try:
    previous_plan = read_plan(path)
  except PlanFileError:
    return set()
  kept_for_your_lock = {
    entry['id'] for entry in previous_plan.get('locked_but_outdated') or []
  }
  return {
    instance_id
    for instance_id, entry in (previous_plan.get('items') or {}).items()
    if entry.get('action') == 'keep' and instance_id not in kept_for_your_lock
  }


def format_plan_summary(plan: dict, plan_path: Path) -> str:
  summary = plan['summary']
  counts = [f'Plan for {plan["account"]}']
  for kind in ('armor', 'weapons'):
    counts.append(_count_row(kind.capitalize(), summary[kind]))
    if kind == 'armor':
      counts.extend(
        _count_row(class_name, class_counts, indent='    ')
        for class_name, class_counts in summary['armor_by_class'].items()
      )
  footer = []
  if plan.get('from_snapshot'):
    footer.append(
      f'This is a what-if plan from {plan["from_snapshot"]}; apply never uses it.'
    )
  footer += ['Nothing was changed in your account.', f'Plan: {plan_path}']
  return join_sections(
    counts,
    _plan_notes(plan),
    _ties_section(plan['ties']),
    _crafting_section(plan.get('crafting') or []),
    _loadouts_section(plan.get('loadouts_affected') or []),
    footer,
  )


def _count_row(label: str, counts: dict, indent: str = '  ') -> str:
  '''"  Armor       379 items   323 keep    56 shard    0 review", in columns.'''
  items = sum(counts[action] for action in ACTIONS)
  return (
    f'{indent}{label:<{COUNT_LABEL_WIDTH - len(indent)}}'
    f'{items:>4} {"item" if items == 1 else "items":<5}'
    f'{counts["keep"]:>6} keep{counts["shard"]:>6} shard{counts["review"]:>5} review'
  )


def _plan_notes(plan: dict) -> list[str]:
  '''Items kept only for your lock, former keepers that are shards now, and
  warnings.'''
  notes = []
  locked_only = plan['summary']['locked_but_outdated']
  if locked_only:
    notes.append(
      f'{locked_only} kept only because you locked them (see locked_but_outdated '
      'in the plan)'
    )
  newly_sharded = plan.get('newly_sharded') or []
  if newly_sharded:
    tool_locked = sum(1 for entry in newly_sharded if entry.get('tool_locked'))
    unlock_note = (
      f'; apply unlocks the {tool_locked} the tool had locked' if tool_locked else ''
    )
    now_shards = 'is a shard' if len(newly_sharded) == 1 else 'are shards'
    notes.append(
      f'{count_label(len(newly_sharded), "item")} the last plan kept {now_shards} '
      f'now (see newly_sharded in the plan){unlock_note}'
    )
    if len(newly_sharded) >= BIG_CHANGE_COUNT:
      notes.append(
        'That is a big change. If you did not expect it (a new wishlist roll or '
        'config change), Voltron or the manifest may have changed; check the plan '
        'before you apply it.'
      )
  notes.extend(f'Warning: {warning}' for warning in plan['warnings'])
  return notes


def _ties_section(ties: list[dict]) -> list[str]:
  if not ties:
    return []
  return [
    'Tied rolls, every copy kept until you pick with `uv run sweeper ties`:',
    *(f'  {tie["weapon"]}' for tie in ties),
  ]


def _crafting_section(crafting: list[dict]) -> list[str]:
  if not crafting:
    return []
  width = max(len(entry['weapon']) for entry in crafting)
  return [
    'Extra copies kept toward a crafting pattern, for Deepsight Harmonizers:',
    *(
      f'  {entry["weapon"]:<{width}}   {count_label(entry["copies"], "copy", "copies")}'
      for entry in crafting
    ),
  ]


def _loadouts_section(affected: list[dict]) -> list[str]:
  if not affected:
    return []
  width = max(len(loadout['loadout']) for loadout in affected)
  return [
    'In-game loadouts that use a shard (to rebuild them, see loadouts_affected):',
    *(
      f'  {loadout["loadout"]:<{width}}   {", ".join(loadout["shards"])}'
      for loadout in affected
    ),
  ]


def _item_label(item: ArmorPiece | Weapon) -> dict:
  '''ID, name, class (armor only), and slot, for lists in the plan.'''
  label = {'id': item.instance_id, 'name': item.name}
  class_name = getattr(item, 'class_name', None)
  if class_name:
    label['class'] = class_name
  label['slot'] = item.slot
  return label


def _newly_sharded(
  items: list[ArmorPiece | Weapon],
  decisions: dict[str, Decision],
  previous_keepers: set[str],
  tool_locked_ids: set[str],
) -> list[dict]:
  '''Items the last plan kept that are shards now, such as a copy a better drop
  replaced. apply unlocks the ones the tool had locked.'''
  newly_sharded = []
  for item in items:
    decision = decisions[item.instance_id]
    if item.instance_id not in previous_keepers or decision.action != 'shard':
      continue
    entry = _item_label(item)
    if item.is_locked and item.instance_id in tool_locked_ids:
      entry['tool_locked'] = True
    entry['rules_say'] = decision.reasons[0]
    newly_sharded.append(entry)
  return newly_sharded


def _loadouts_affected(loadouts: list[Loadout], items: dict[str, dict]) -> list[dict]:
  '''Loadouts that use a shard, with all their gear and mods to rebuild them.

  Every planned item a loadout uses lists its loadouts, keepers included: a
  keeper becomes a shard during a run if you remove the lock that kept it.
  '''
  affected = []
  loadouts_by_item: dict[str, list[str]] = defaultdict(list)
  for loadout in loadouts:
    gear = [piece for piece in loadout.gear if piece.instance_id in items]
    for piece in gear:
      loadouts_by_item[piece.instance_id].append(loadout.label)
    shards = [piece for piece in gear if items[piece.instance_id]['action'] == 'shard']
    if not shards:
      continue
    affected.append(
      {
        'loadout': loadout.label,
        'shards': [items[piece.instance_id]['name'] for piece in shards],
        'gear': [
          {
            'name': items[piece.instance_id]['name'],
            'slot': items[piece.instance_id]['slot'],
            'mods': list(piece.mods),
            **({'shard': True} if piece in shards else {}),
          }
          for piece in gear
        ],
      }
    )
  for instance_id, labels in loadouts_by_item.items():
    items[instance_id]['loadouts'] = labels
  return affected


def _armor_entry(piece: ArmorPiece, decision: Decision) -> dict:
  entry: dict = {
    'name': piece.name,
    'kind': 'armor',
    'class': piece.class_name,
    'slot': piece.slot,
    'tier': piece.tier,
  }
  if piece.is_low_rarity:
    entry['rarity'] = piece.rarity
  if piece.archetype:
    entry['archetype'] = piece.archetype
  entry['stats'] = piece.display_stats
  entry['total'] = piece.base_total
  if piece.tuned_stat:
    entry['tuned'] = piece.tuned_stat
  if piece.set_name:
    entry['set'] = piece.set_name
  if piece.raid:
    entry['raid'] = piece.raid
  if piece.is_exotic:
    entry['exotic'] = True
  if piece.exotic_perks:
    entry['perks'] = list(piece.exotic_perks)
  entry['locked'] = piece.is_locked
  entry['location'] = piece.location
  entry['action'] = decision.action
  entry['reasons'] = decision.reasons
  if decision.compare_with:
    # Kept pieces of the same name a round brings in next to this shard.
    entry['compare_with'] = list(decision.compare_with)
  return entry


def _weapon_entry(weapon: Weapon, decision: Decision) -> dict:
  entry: dict = {
    'name': weapon.name,
    'kind': 'weapon',
    'type': weapon.weapon_type,
    'slot': weapon.slot,
    'rarity': weapon.rarity,
    'tier': weapon.tier,
  }
  for flag, is_set in (
    ('shiny', weapon.is_shiny),
    ('adept', weapon.is_adept),
    ('crafted', weapon.is_crafted),
    ('enhanced', weapon.is_enhanced),
    ('craftable', weapon.is_craftable and not weapon.is_crafted),
  ):
    if is_set:
      entry[flag] = True
  # Crafted copies can reach their whole perk pool, so only show what is
  # equipped; random rolls show every option per column.
  entry['perks'] = (
    list(weapon.selected_perks)
    if weapon.is_crafted
    else [' | '.join(column) for column in weapon.perk_columns]
  )
  if weapon.masterwork:
    entry['masterwork'] = weapon.masterwork
  if weapon.exotic_pair:
    entry['pair'] = ' + '.join(weapon.exotic_pair)
  entry['locked'] = weapon.is_locked
  entry['location'] = weapon.location
  entry['action'] = decision.action
  entry['reasons'] = decision.reasons
  if decision.compare_with:
    # The kept copies a round brings in next to this shard, to compare.
    entry['compare_with'] = list(decision.compare_with)
  return entry


def _summary(
  pieces: list[ArmorPiece],
  weapons: list[Weapon],
  decisions: dict[str, Decision],
  locked_but_outdated: list[dict],
) -> dict:
  def counts(items: list[ArmorPiece] | list[Weapon]) -> dict:
    totals = Counter(decisions[item.instance_id].action for item in items)
    return {
      'items': len(items),
      **{action: totals.get(action, 0) for action in ACTIONS},
    }

  class_names = sorted({piece.class_name for piece in pieces})
  return {
    'armor': counts(pieces),
    'armor_by_class': {
      class_name: {
        key: value
        for key, value in counts(
          [piece for piece in pieces if piece.class_name == class_name]
        ).items()
        if key != 'items'
      }
      for class_name in class_names
    },
    'weapons': counts(weapons),
    'locked_but_outdated': len(locked_but_outdated),
  }


def _wishlist_warnings(
  personal_wishlist: dict[str, list[WishlistEntry]], item_definitions: dict
) -> list[str]:
  '''Flag names in wishlist.yaml that match nothing in the game.

  A misspelled weapon name would quietly switch its rolls off, and a perk or
  masterwork the game doesn't know, or a perk under the wrong column, would
  never count. Each warning suggests the closest real name.
  '''
  if not personal_wishlist:
    return []
  known_weapons = {
    normalize_weapon_name(definition_name(definition))
    for definition in item_definitions.values()
    if definition.get('itemType') == ITEM_TYPE_WEAPON
  }
  categories_by_perk = perk_categories(item_definitions)
  masterwork_stats = {masterwork_stat(plug) for plug in item_definitions.values()}
  masterwork_stats.discard('')
  known_stats = {stat.casefold() for stat in masterwork_stats}
  warnings = []
  for weapon_name, entries in personal_wishlist.items():
    if weapon_name not in known_weapons:
      warnings.append(
        f"wishlist.yaml: '{weapon_name}' is not a weapon name"
        f'{_suggestion(weapon_name, known_weapons)}, so its rolls are ignored'
      )
    for entry in entries:
      where = f"in {weapon_name} '{entry.title}'"
      warnings.extend(_perk_warnings(entry, where, categories_by_perk))
      warnings.extend(
        f"wishlist.yaml: '{stat}' {where} is not a masterwork stat"
        f'{_suggestion(stat, masterwork_stats)}'
        for stat in entry.masterwork
        if stat.casefold() not in known_stats
      )
  return warnings


def _perk_warnings(
  entry: WishlistEntry, where: str, categories_by_perk: dict[str, frozenset[str]]
) -> list[str]:
  '''Perks the game doesn't know, columns that don't exist, and perks under a
  column that isn't theirs.'''
  warnings = []
  columns = entry.columns or ('',) * len(entry.perks)
  for perk, column in zip(entry.perks, columns, strict=True):
    expected = column_plug_categories(column) if column else ()
    if expected is None:
      warnings.append(
        f"wishlist.yaml: '{column}' {where} is not a column name"
        f'{_suggestion(column, set(COLUMN_NAMES))}'
      )
      expected = ()
    for choice in alternatives(perk):
      categories = categories_by_perk.get(choice)
      if categories is None:
        warnings.append(
          f"wishlist.yaml: '{choice}' {where} is not a perk name"
          f'{_suggestion(choice, set(categories_by_perk))}'
        )
      elif expected and not categories & set(expected):
        warnings.append(
          f"wishlist.yaml: '{choice}' {where} is under {column}, but it is "
          f'{_perk_kind(categories)}'
        )
  return warnings


def _perk_kind(categories: frozenset[str]) -> str:
  '''What the game says a perk is: "a trait", "an origin trait", "a barrel".'''
  if TRAIT_PLUG_CATEGORY in categories:
    kind = 'trait'
  else:
    kind = next(
      (
        column.replace('_', ' ')
        for column, column_categories in COLUMN_PLUG_CATEGORIES.items()
        if categories & set(column_categories)
      ),
      '',
    )
  if not kind:
    return 'not a weapon perk'
  return f'{"an" if kind[0] in "aeiou" else "a"} {kind}'


def _suggestion(name: str, known_names: set[str]) -> str:
  '''" (did you mean X?)" for the closest known name, or "".'''
  by_lowercase = {known_name.lower(): known_name for known_name in known_names}
  close_match = by_lowercase.get(name.lower())
  if close_match is None:
    matches = difflib.get_close_matches(name, known_names, n=1)
    close_match = matches[0] if matches else None
  return f' (did you mean {close_match}?)' if close_match else ''


def _current_character(profile: dict) -> str | None:
  characters = profile.get('characters', {}).get('data', {}).values()
  if not characters:
    return None
  latest = max(characters, key=lambda character: character.get('dateLastPlayed', ''))
  return CLASS_NAMES.get(latest.get('classType'))
