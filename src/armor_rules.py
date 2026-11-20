'''Armor keep rules: stat champions, set and raid coverage, and exotics (see
README.md).

A piece is kept if any rule claims it; everything else is marked to shard.
Only legendary and exotic armor competes: white, green, and blue pieces are
shards. User locks are applied afterwards, in src/plan.py.
'''

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from src.game_data import STAT_DISPLAY_ORDER
from src.inventory import ArmorPiece
from src.preferences import ArmorPreferences, Preferences

# Kept copies a shard names to compare against, at most.
COMPARE_COPIES = 2


@dataclass
class Decision:
  action: str  # keep | shard | review
  reasons: list[str] = field(default_factory=list)
  # Shards: the kept copies to compare against, best first. Weapons name their
  # best copies; armor names kept copies of the same name.
  compare_with: tuple[str, ...] = ()


class ArmorRanking:
  '''Sort keys where smaller means better, so min() picks the keeper.'''

  def __init__(self, preferences: ArmorPreferences, kept_last_run: set[str]) -> None:
    self.preferences = preferences
    self.kept_last_run = kept_last_run
    self.unranked_stat = len(preferences.stat_priority)

  def ladder_key(
    self, piece: ArmorPiece, archetype_order: tuple[str, ...] | None = None
  ) -> tuple:
    '''tier, base total, archetype order, stat priority (primary, secondary,
    tertiary, tuned), masterworked, kept last run, then oldest.'''
    stat_ranks = [self._stat_rank(stat) for stat in piece.ordered_stats]
    stat_ranks += [self.unranked_stat] * (3 - len(stat_ranks))
    return (
      -piece.tier,
      -piece.base_total,
      self._archetype_rank(piece, archetype_order),
      *stat_ranks,
      self._stat_rank(piece.tuned_stat),
      0 if piece.is_masterworked else 1,
      0 if piece.instance_id in self.kept_last_run else 1,
      int(piece.instance_id),
    )

  def champion_key(self, piece: ArmorPiece, stat: str) -> tuple:
    return (-piece.reachable_value(stat), *self.ladder_key(piece))

  def set_coverage_key(self, piece: ArmorPiece) -> tuple:
    return (0 if self.overrides_tier(piece) else 1, *self.ladder_key(piece))

  def overrides_tier(self, piece: ArmorPiece) -> bool:
    '''A preferred archetype at the minimum tier or higher beats any tier.'''
    return (
      piece.archetype in self.preferences.archetype_order
      and piece.tier >= self.preferences.archetype_override_min_tier
    )

  def _archetype_rank(
    self, piece: ArmorPiece, archetype_order: tuple[str, ...] | None
  ) -> int:
    order = archetype_order or self.preferences.archetype_order
    return order.index(piece.archetype) if piece.archetype in order else len(order)

  def _stat_rank(self, stat: str | None) -> int:
    priority = self.preferences.stat_priority
    return priority.index(stat) if stat in priority else self.unranked_stat


def decide_armor(
  pieces: Iterable[ArmorPiece], preferences: Preferences, kept_last_run: set[str]
) -> dict[str, Decision]:
  '''Keep or shard every armor piece. Classes not in config.yaml go to review.'''
  pieces = list(pieces)
  ranking = ArmorRanking(preferences.armor, kept_last_run)
  in_scope = [
    piece for piece in pieces if piece.class_name.lower() in preferences.classes
  ]
  competing = [piece for piece in in_scope if not piece.is_low_rarity]
  keep_reasons: dict[str, list[str]] = defaultdict(list)
  # The best piece per class and slot of each set; of each raid, whose armor
  # has a mod socket only that raid's mods fit; and of each perk in perk_sets.
  coverages: list[tuple[Callable[[ArmorPiece], str | None], str]] = [
    (lambda piece: piece.set_name, ''),
    (lambda piece: piece.raid, ', for its raid mod slot'),
    *((_has_perk(perk), ', for that perk') for perk in preferences.armor.perk_sets),
  ]
  coverage_winners = [
    (group_of, _keep_one_per_slot(competing, ranking, keep_reasons, group_of, note))
    for group_of, note in coverages
  ]
  _keep_stat_champions(competing, ranking, keep_reasons)
  _keep_exotics(competing, ranking, keep_reasons, preferences.armor)

  in_scope_ids = {piece.instance_id for piece in in_scope}
  decisions = {}
  for piece in pieces:
    if piece.instance_id not in in_scope_ids:
      decisions[piece.instance_id] = Decision(
        'review', [f'{piece.class_name} is not in config.yaml classes']
      )
    elif piece.is_low_rarity:
      decisions[piece.instance_id] = Decision(
        'shard',
        [f'{piece.rarity} armor: only legendary and exotic armor competes for a spot'],
      )
    elif keep_reasons[piece.instance_id]:
      decisions[piece.instance_id] = Decision('keep', keep_reasons[piece.instance_id])
    else:
      decisions[piece.instance_id] = Decision(
        'shard', [_shard_reason(piece, coverage_winners)]
      )
  _name_copies_to_compare(pieces, decisions, ranking)
  return decisions


def describe(piece: ArmorPiece) -> str:
  '''Short label such as "T5 Powerhouse" or "legacy, 62 total".'''
  if piece.is_legacy:
    return f'legacy, {piece.base_total} total'
  label = f'T{piece.tier} {piece.archetype or "no archetype"}'
  if piece.tuned_stat:
    label += f', tuned {piece.tuned_stat}'
  return label


def _has_perk(perk: str) -> Callable[[ArmorPiece], str | None]:
  '''Groups the pieces that have *perk* under its name.'''
  return lambda piece: perk if perk in piece.perks else None


def _keep_one_per_slot(
  pieces: list[ArmorPiece],
  ranking: ArmorRanking,
  keep_reasons: dict[str, list[str]],
  group_of: Callable[[ArmorPiece], str | None],
  note: str = '',
) -> dict[tuple[str, str, str], ArmorPiece]:
  '''Keep the best piece for each class, group (a set or a raid), and slot.'''
  groups: dict[tuple[str, str, str], list[ArmorPiece]] = defaultdict(list)
  for piece in pieces:
    group = group_of(piece)
    if group and not piece.is_exotic:
      groups[(piece.class_name, group, piece.slot)].append(piece)
  winners = {}
  for (class_name, group, slot), candidates in groups.items():
    winner = min(candidates, key=ranking.set_coverage_key)
    winners[(class_name, group, slot)] = winner
    keep_reasons[winner.instance_id].append(
      f'{group} {slot} for {class_name}{note} ({describe(winner)})'
    )
  return winners


def _keep_stat_champions(
  pieces: list[ArmorPiece],
  ranking: ArmorRanking,
  keep_reasons: dict[str, list[str]],
) -> None:
  groups: dict[tuple[str, str], list[ArmorPiece]] = defaultdict(list)
  for piece in pieces:
    if not piece.is_exotic:
      groups[(piece.class_name, piece.slot)].append(piece)
  for (class_name, slot), candidates in groups.items():
    # In the game's order, so a piece's reasons read like its stats.
    for stat in STAT_DISPLAY_ORDER:
      champion = min(
        candidates, key=lambda piece, stat=stat: ranking.champion_key(piece, stat)
      )
      value = champion.reachable_value(stat)
      if value <= 0:
        continue
      value_label = (
        f'{value} with tuning' if champion.tuning_bonus(stat) else f'{value}'
      )
      keep_reasons[champion.instance_id].append(
        f'best {stat} {slot} for {class_name} ({value_label})'
      )


def _keep_exotics(
  pieces: list[ArmorPiece],
  ranking: ArmorRanking,
  keep_reasons: dict[str, list[str]],
  preferences: ArmorPreferences,
) -> None:
  groups: dict[tuple[str, str, tuple[str, ...]], list[ArmorPiece]] = defaultdict(list)
  for piece in pieces:
    if piece.is_exotic:
      groups[(piece.class_name, piece.name, piece.exotic_perks)].append(piece)
  for (class_name, name, perks), candidates in groups.items():
    label = f'{name} ({" + ".join(perks)})' if perks else name
    override = preferences.exotic_overrides.get(name)
    archetype_order = override.archetype_order if override else None
    kept_any = False
    for archetype in override.keep_per_archetype if override else ():
      matching = [piece for piece in candidates if piece.archetype == archetype]
      if matching:
        best = min(
          matching, key=lambda piece: ranking.ladder_key(piece, archetype_order)
        )
        keep_reasons[best.instance_id].append(
          f'exotic {label} for {class_name}, best {archetype} copy'
        )
        kept_any = True
    if not kept_any:
      best = min(
        candidates, key=lambda piece: ranking.ladder_key(piece, archetype_order)
      )
      keep_reasons[best.instance_id].append(
        f'exotic {label} for {class_name} ({describe(best)})'
      )


def _name_copies_to_compare(
  pieces: list[ArmorPiece], decisions: dict[str, Decision], ranking: ArmorRanking
) -> None:
  '''Each shard names the kept pieces of the same name (for exotic class items,
  the same perk pair too), so a round can bring them in next to it.'''
  keepers: dict[tuple, list[ArmorPiece]] = defaultdict(list)
  for piece in pieces:
    if decisions[piece.instance_id].action == 'keep':
      keepers[(piece.class_name, piece.name, piece.exotic_perks)].append(piece)
  for piece in pieces:
    decision = decisions[piece.instance_id]
    copies = keepers.get((piece.class_name, piece.name, piece.exotic_perks))
    if decision.action == 'shard' and copies:
      best = sorted(copies, key=ranking.ladder_key)[:COMPARE_COPIES]
      decision.compare_with = tuple(copy.instance_id for copy in best)


def _shard_reason(
  piece: ArmorPiece,
  coverage_winners: list[
    tuple[Callable[[ArmorPiece], str | None], dict[tuple[str, str, str], ArmorPiece]]
  ],
) -> str:
  if piece.is_exotic:
    return f'a better copy of {piece.name} is kept ({describe(piece)} here)'
  reason = f'not the best {piece.slot} for {piece.class_name} in any stat'
  if piece.is_legacy:
    reason = f'legacy piece, {reason}'
  for group_of, winners in coverage_winners:
    group = group_of(piece)
    winner = winners.get((piece.class_name, group or '', piece.slot))
    if winner:
      reason += (
        f'; {group} {piece.slot} kept is {winner.instance_id} '
        f'({describe(winner)}) instead of this {describe(piece)}'
      )
  return reason
