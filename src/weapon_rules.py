'''Weapon keep rules (see "How each item is decided" in README.md).

Copies of the same weapon compete (shiny versions separately). Each of your own
wishlist rolls keeps its best copy: one with the roll's traits, and the most of
its other perks. Otherwise the best copy overall is kept, ranked by Voltron, so
every weapon keeps at least one copy however weak its roll. When two or more
copies are full Voltron matches and you have no roll of your own for that
weapon, it is a tie that needs your taste: every tied copy is kept until you
pick in the tie step. A craftable weapon you have no crafted copy of also keeps
its best copies toward the pattern. Exotics keep one copy, except Ergo Sum,
which keeps one per frame + exotic trait pair. White weapons are always kept:
they are quest items and keepsakes. Green and blue weapons are shards unless you
lock them. Each shard names the kept copies to compare it against.
'''

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from src.armor_rules import COMPARE_COPIES, Decision
from src.game_data import PAIRED_EXOTIC_WEAPONS
from src.preferences import Preferences
from src.text import count_label
from src.voltron import VoltronEntry
from src.weapons import Weapon
from src.wishlist import WishlistEntry

# Copies of a craftable weapon kept until you have a crafted one, so Deepsight
# Harmonizers can turn them into pattern progress. The user's assumption:
# every pattern takes 5.
CRAFTING_COPIES = 5


@dataclass(frozen=True)
class RollScore:
  # Each of your rolls the copy matches, as reasons name it: "PvE roll with 2
  # of 3 secondary perks".
  personal_matches: tuple[str, ...]
  voltron_full_match: bool
  voltron_best_count: int
  voltron_best_size: int


@dataclass
class WeaponDecisions:
  decisions: dict[str, Decision] = field(default_factory=dict)
  # Weapons waiting for your pick: {'weapon', 'group_name', 'shiny', 'tied',
  # 'copies'}, where 'tied' are the full-match copies and 'copies' all of them.
  ties: list[dict] = field(default_factory=list)
  # Weapons keeping copies only for their crafting pattern: label -> how many
  # copies the pattern keeps.
  crafting: dict[str, int] = field(default_factory=dict)


def score_roll(
  weapon: Weapon,
  personal_entries: list[WishlistEntry],
  voltron_entries: list[VoltronEntry],
  primary_perk_names: frozenset[str],
) -> RollScore:
  personal_matches = tuple(
    f'{entry.title} roll{_roll_detail(weapon, entry, primary_perk_names)}'
    for entry in personal_entries
    if weapon.can_roll(entry.primary_perks(primary_perk_names))
  )
  best_count = best_size = 0
  full_match = False
  for entry in voltron_entries:
    matched = weapon.matchable_perk_count(entry.perks)
    if matched == len(entry.perks):
      full_match = True
      best_count, best_size = matched, len(entry.perks)
      break
    if matched > best_count:
      best_count, best_size = matched, len(entry.perks)
  return RollScore(personal_matches, full_match, best_count, best_size)


def decide_weapons(
  weapons: Iterable[Weapon],
  preferences: Preferences,
  personal_wishlist: dict[str, list[WishlistEntry]],
  voltron_entries: dict[str, list[VoltronEntry]],
  kept_last_run: set[str],
  primary_perk_names: frozenset[str],
) -> WeaponDecisions:
  weapons = list(weapons)
  result = WeaponDecisions()
  crafted_names = {weapon.group_name for weapon in weapons if weapon.is_crafted}
  groups: dict[tuple[str, str, bool], list[Weapon]] = defaultdict(list)
  for weapon in weapons:
    if weapon.rarity == 'white':
      result.decisions[weapon.instance_id] = Decision(
        'keep', ['white weapon (quest item or keepsake), always kept']
      )
    elif weapon.rarity in ('green', 'blue'):
      result.decisions[weapon.instance_id] = Decision(
        'shard',
        [f'{weapon.rarity} weapon: only white, legendary, and exotic weapons are kept'],
      )
    elif weapon.group_name in preferences.weapons.never_keep:
      result.decisions[weapon.instance_id] = Decision(
        'shard', [f'{weapon.group_name} is on your never_keep list']
      )
    else:
      groups[(weapon.rarity, *weapon.group_key)].append(weapon)
  for (rarity, group_name, _), copies in groups.items():
    if rarity == 'exotic' and group_name in PAIRED_EXOTIC_WEAPONS:
      _decide_paired_exotic(copies, kept_last_run, result.decisions)
    elif rarity == 'exotic':
      _decide_exotic(copies, kept_last_run, result.decisions)
    else:
      _decide_legendary(
        copies,
        personal_wishlist.get(group_name, []),
        voltron_entries.get(group_name, []),
        kept_last_run,
        primary_perk_names,
        result,
        has_crafted_copy=group_name in crafted_names,
      )
  return result


def weapon_label(weapon: Weapon) -> str:
  return f'{weapon.group_name} (shiny)' if weapon.is_shiny else weapon.group_name


def describe(weapon: Weapon, score: RollScore | None = None) -> str:
  '''Short label such as "crafted, T5, Voltron match".'''
  parts = []
  if weapon.is_crafted:
    parts.append('crafted')
  elif weapon.is_enhanced:
    parts.append('enhanced')
  if weapon.is_adept:
    parts.append('Adept')
  parts.append(f'T{weapon.tier}' if weapon.tier else 'untiered')
  if score is not None:
    parts.extend(f'your {match}' for match in score.personal_matches)
    if score.voltron_full_match:
      parts.append('Voltron match')
    elif score.voltron_best_count:
      parts.append(f'Voltron {score.voltron_best_count}/{score.voltron_best_size}')
    else:
      parts.append('no Voltron match')
  return ', '.join(parts)


def _decide_legendary(
  copies: list[Weapon],
  personal_entries: list[WishlistEntry],
  voltron_entries: list[VoltronEntry],
  kept_last_run: set[str],
  primary_perk_names: frozenset[str],
  result: WeaponDecisions,
  has_crafted_copy: bool,
) -> None:
  scores = {
    weapon.instance_id: score_roll(
      weapon, personal_entries, voltron_entries, primary_perk_names
    )
    for weapon in copies
  }

  def rank(weapon: Weapon) -> tuple:
    score = scores[weapon.instance_id]
    return (
      0 if score.personal_matches else 1,
      0 if score.voltron_full_match else 1,
      # Every full match counts the same, however many perks its Voltron line
      # lists; the count only ranks partial matches (3/4 beats 2/4).
      0 if score.voltron_full_match else -score.voltron_best_count,
      0 if weapon.is_adept else 1,
      -weapon.option_count,
      -weapon.tier,
      0 if weapon.instance_id in kept_last_run else 1,
      int(weapon.instance_id),
    )

  keep_reasons: dict[str, list[str]] = defaultdict(list)
  # For each of your rolls that a copy matches: its primary perks, and the copy
  # it keeps.
  roll_keepers: list[tuple[tuple[str, ...], Weapon]] = []
  for entry in personal_entries:
    primary_perks = entry.primary_perks(primary_perk_names)
    matching = [weapon for weapon in copies if weapon.can_roll(primary_perks)]
    if matching:
      # The traits decide the match. The roll's secondary perks rank the copies
      # that have them, then its masterwork, before anything else.
      best = min(
        matching,
        key=lambda weapon: (
          -weapon.matchable_perk_count(entry.perks),
          0 if weapon.can_have_masterwork(entry.masterwork) else 1,
          *rank(weapon),
        ),
      )
      roll_keepers.append((primary_perks, best))
      keep_reasons[best.instance_id].append(
        f"matches your '{entry.title}' roll"
        f'{_roll_detail(best, entry, primary_perk_names)}'
      )

  # With no crafted copy yet, the best copies of a version with a crafting
  # pattern are kept toward it. Versions without one give no progress.
  pattern_copies = (
    []
    if has_crafted_copy
    else sorted((weapon for weapon in copies if weapon.is_craftable), key=rank)[
      :CRAFTING_COPIES
    ]
  )

  # No tie while you collect a pattern: those copies are kept anyway, and a
  # crafted copy lets you choose its perks.
  if not personal_entries and not pattern_copies:
    full_matches = [
      weapon for weapon in copies if scores[weapon.instance_id].voltron_full_match
    ]
    if len(full_matches) >= 2:
      for weapon in full_matches:
        keep_reasons[weapon.instance_id].append(
          'tied recommended roll: kept until you pick in the tie step'
        )
      result.ties.append(
        {
          'weapon': weapon_label(full_matches[0]),
          'group_name': full_matches[0].group_name,
          'shiny': full_matches[0].is_shiny,
          'tied': sorted(weapon.instance_id for weapon in full_matches),
          'copies': sorted(weapon.instance_id for weapon in copies),
        }
      )

  if not keep_reasons:
    best = min(copies, key=rank)
    keep_reasons[best.instance_id].append(
      f'best {weapon_label(best)} copy ({describe(best, scores[best.instance_id])})'
    )
  if any(not keep_reasons[weapon.instance_id] for weapon in pattern_copies):
    result.crafting[weapon_label(pattern_copies[0])] = len(pattern_copies)
  for weapon in pattern_copies:
    keep_reasons[weapon.instance_id].append(
      f'no crafted {weapon.group_name} yet: one of up to {CRAFTING_COPIES} '
      'copies kept toward its crafting pattern'
    )
  keepers = sorted(
    (weapon for weapon in copies if keep_reasons[weapon.instance_id]), key=rank
  )
  compare_with = tuple(weapon.instance_id for weapon in keepers[:COMPARE_COPIES])
  for weapon in copies:
    if keep_reasons[weapon.instance_id]:
      result.decisions[weapon.instance_id] = Decision(
        'keep', keep_reasons[weapon.instance_id]
      )
    else:
      # A copy with one of your rolls lost to the copy that roll keeps.
      winner = next(
        (
          keeper
          for primary_perks, keeper in roll_keepers
          if weapon.can_roll(primary_perks)
        ),
        keepers[0],
      )
      result.decisions[weapon.instance_id] = Decision(
        'shard',
        [
          f'another {weapon_label(weapon)} copy is kept: {winner.instance_id} '
          f'({describe(winner, scores[winner.instance_id])}); this one is '
          f'{describe(weapon, scores[weapon.instance_id])}'
        ],
        compare_with=compare_with,
      )


def _roll_detail(
  weapon: Weapon, entry: WishlistEntry, primary_perk_names: frozenset[str]
) -> str:
  '''" with 2 of 3 secondary perks and the Range masterwork": how much of the
  rest of the roll the copy can have along with its primary perks. "" for a
  roll of traits alone.'''
  primary_count = len(entry.primary_perks(primary_perk_names))
  secondary_count = len(entry.perks) - primary_count
  detail = ''
  if secondary_count:
    matched = weapon.matchable_perk_count(entry.perks) - primary_count
    detail = f' with {matched} of {count_label(secondary_count, "secondary perk")}'
  if entry.masterwork:
    masterwork = f'the {" or ".join(entry.masterwork)} masterwork'
    if weapon.can_have_masterwork(entry.masterwork):
      detail += f' and {masterwork}' if detail else f' with {masterwork}'
    else:
      detail += f' but not {masterwork}' if detail else f' without {masterwork}'
  return detail


def _decide_paired_exotic(
  copies: list[Weapon], kept_last_run: set[str], decisions: dict[str, Decision]
) -> None:
  '''Ergo Sum: one copy of each frame + trait pair, like exotic class items.'''
  copies_by_pair: dict[tuple[str, ...], list[Weapon]] = defaultdict(list)
  for weapon in copies:
    copies_by_pair[weapon.exotic_pair].append(weapon)
  for pair, pair_copies in copies_by_pair.items():
    _decide_exotic(pair_copies, kept_last_run, decisions, pair)


def _decide_exotic(
  copies: list[Weapon],
  kept_last_run: set[str],
  decisions: dict[str, Decision],
  pair: tuple[str, ...] = (),
) -> None:
  '''Keep the best copy. *pair* is the frame + trait the copies share, if any.'''

  def rank(weapon: Weapon) -> tuple:
    return (
      0 if weapon.is_crafted else 1,
      -weapon.tier,
      0 if weapon.is_masterworked else 1,
      0 if weapon.instance_id in kept_last_run else 1,
      int(weapon.instance_id),
    )

  keeper = min(copies, key=rank)
  pair_label = ' + '.join(pair)
  for weapon in copies:
    if weapon is keeper:
      detail = f', {pair_label}' if pair else ''
      decisions[weapon.instance_id] = Decision(
        'keep', [f'exotic {weapon_label(weapon)}{detail} ({describe(weapon)})']
      )
    else:
      detail = f' with {pair_label}' if pair else ''
      decisions[weapon.instance_id] = Decision(
        'shard',
        [
          f'another {weapon_label(weapon)} copy{detail} is kept: '
          f'{keeper.instance_id} ({describe(keeper)})'
        ],
        compare_with=(keeper.instance_id,),
      )
