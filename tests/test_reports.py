'''Tests for how the round and finish reports are laid out.'''

from __future__ import annotations

from collections import Counter

from src.reports import format_finish_report, format_round_report
from src.session import FinishReport, RoundReport


def test_a_round_report_is_in_sections_with_one_entry_to_a_line():
  report = RoundReport(
    'Warlock',
    2,
    locked=3,
    unlocked=['Sorrow MG2'],
    dismantled=9,
    staged_now=Counter({'Kinetic': 2}),
    waiting=Counter({'Kinetic': 2, 'Helmet': 1}),
    comparing=['Ergo Sum', 'Ergo Sum', 'Fatebringer'],
    in_loadouts=["Luna's Howl: Titan loadout 7 (Solar)"],
    still_to_stage=Counter({'Energy': 4}),
    stuck_shards=["Hammerhead (in your Titan's Postmaster)"],
    problems=['could not move Fatebringer (Kinetic): it is equipped'],
  )
  assert format_round_report(report) == (
    'Round 2 on your Warlock:\n'
    '  You dismantled 9 shards since the last round.\n'
    '  Locked 3 keepers.\n'
    '  Unlocked 1 shard the tool had locked: Sorrow MG2.\n'
    '  Staged 2 shards (Kinetic 2).\n'
    '\n'
    'In your inventory to dismantle: 3 shards (Kinetic 2, Helmet 1)\n'
    'Kept copies next to them, to compare (locked; back to the vault after):\n'
    '  Ergo Sum (2), Fatebringer\n'
    'Heads up, dismantling these breaks loadouts (see loadouts_affected in the '
    'plan):\n'
    "  Luna's Howl: Titan loadout 7 (Solar)\n"
    '\n'
    'Left for later rounds: 4 shards (Energy 4)\n'
    'Shards the tool cannot bring to you (dismantle or move them yourself):\n'
    "  Hammerhead (in your Titan's Postmaster)\n"
    '\n'
    'Skipped:\n'
    '  Could not move Fatebringer (Kinetic): it is equipped.\n'
    '\n'
    'Next: dismantle those shards in-game, then run `uv run sweeper next`.'
  )


def test_a_finish_report_ends_with_what_is_left():
  report = FinishReport(
    'Warlock',
    restored=4,
    not_restored=['Fatebringer (Kinetic), in the vault: there is no room'],
    shards_left=1,
    other_classes=Counter({'Titan': 2}),
  )
  assert format_finish_report(report) == (
    'Putting your gear back on your Warlock:\n'
    '  Put back 4 keepers you were carrying before the session.\n'
    '\n'
    'Could not put these back:\n'
    '  Fatebringer (Kinetic), in the vault: there is no room\n'
    '\n'
    '1 shard you did not dismantle is still in your inventory, unlocked.\n'
    'Armor shards waiting for a session on that class: Titan 2\n'
    '\n'
    'Session finished.'
  )
