'''Tests for color and the step line.'''

from __future__ import annotations

import io

import pytest

from src.terminal import (
  ANSI_CODE,
  CLEAR_LINE,
  StatusLine,
  echo,
  highlight,
  paint,
  wrap,
)


class TerminalStream(io.StringIO):
  '''Stands in for a terminal.'''

  def isatty(self) -> bool:
    return True


@pytest.fixture(autouse=True)
def color_terminal(monkeypatch):
  monkeypatch.delenv('NO_COLOR', raising=False)
  monkeypatch.setenv('TERM', 'xterm-256color')
  monkeypatch.setenv('COLUMNS', '80')


def test_reports_are_plain_unless_a_terminal_shows_them():
  report = 'Round 1 on your Warlock:\n  Locked 3 keepers.'
  log = io.StringIO()
  echo(report, log)
  assert log.getvalue() == report + '\n'
  terminal = TerminalStream()
  echo(report, terminal)
  assert terminal.getvalue() == highlight(report) + '\n'


def test_no_color_turns_color_off(monkeypatch):
  monkeypatch.setenv('NO_COLOR', '1')
  terminal = TerminalStream()
  echo('Next: `uv run sweeper next`', terminal)
  assert terminal.getvalue() == 'Next: `uv run sweeper next`\n'


def test_headings_labels_counts_and_commands_get_color():
  text = highlight(
    'Plan for Guardian\n'
    '  Armor    3 items: 2 keep, 1 shard, 0 review\n'
    '  Warning: Voltron is old\n'
    'Next: run `uv run sweeper next`.'
  )
  assert text.split('\n') == [
    paint('Plan for Guardian', 'bold'),
    f'  Armor    3 items: {paint("2 keep", "green")}, {paint("1 shard", "red")}, '
    f'{paint("0 review", "dim")}',
    f'  {paint("Warning:", "yellow")} Voltron is old',
    f'{paint("Next:", "bold", "cyan")} run {paint("`uv run sweeper next`", "cyan")}.',
  ]
  assert highlight('Session finished.') == paint('Session finished.', 'bold', 'green')


def test_a_line_that_indented_lines_follow_is_a_heading():
  text = highlight('Account: Guardian\nCharacters:\n  Warlock\nVault: 3 items')
  assert text.split('\n')[1:] == [
    paint('Characters:', 'bold'),
    '  Warlock',
    'Vault: 3 items',
  ]


def test_a_step_counts_up_in_place_and_clears_when_it_ends():
  terminal = TerminalStream()
  status = StatusLine(terminal)
  with status.step('Locking keepers', 2):
    status.advance()
    status.advance()
  assert terminal.getvalue() == (
    CLEAR_LINE
    + paint('Locking keepers...', 'dim')
    + CLEAR_LINE
    + paint('Locking keepers: 1 of 2', 'dim')
    + CLEAR_LINE
    + paint('Locking keepers: 2 of 2', 'dim')
    + CLEAR_LINE
  )


def test_a_step_of_unknown_length_counts_what_it_did():
  terminal = TerminalStream()
  status = StatusLine(terminal)
  with status.step('Bringing shards into your inventory'):
    status.advance()
  assert paint('Bringing shards into your inventory: 1', 'dim') in terminal.getvalue()


def test_a_step_with_nothing_to_do_shows_nothing():
  terminal = TerminalStream()
  status = StatusLine(terminal)
  with status.step('Moving keepers from your inventory to the vault', 0):
    pass
  assert terminal.getvalue() == ''


def test_in_a_log_each_step_that_gets_going_is_one_line():
  log = io.StringIO()
  status = StatusLine(log)
  with status.step('Connecting to Bungie'):
    pass
  with status.step('Locking keepers', 3):
    for _ in range(3):
      status.advance()
  with status.step('Bringing shards into your inventory'):
    status.advance()
  assert log.getvalue() == (
    'Locking keepers (3)...\nBringing shards into your inventory...\n'
  )


def test_a_note_goes_above_the_step_line():
  terminal = TerminalStream()
  status = StatusLine(terminal)
  with status.step('Loading game data'):
    start = len(terminal.getvalue())
    status.note('Downloading the Voltron wishlist (about 30 MB)...')
    assert terminal.getvalue()[start:] == (
      CLEAR_LINE
      + 'Downloading the Voltron wishlist (about 30 MB)...\n'
      + CLEAR_LINE
      + paint('Loading game data...', 'dim')
    )


def test_an_inner_step_hands_the_line_back():
  terminal = TerminalStream()
  status = StatusLine(terminal)
  with status.step('Finishing the last session', 1):
    status.advance()
    with status.step('Locking keepers', 1):
      status.advance()
    assert terminal.getvalue().endswith(
      CLEAR_LINE + paint('Finishing the last session: 1 of 1', 'dim')
    )


def test_a_step_that_fails_still_clears_its_line():
  terminal = TerminalStream()
  status = StatusLine(terminal)

  def lock_while_bungie_is_down() -> None:
    with status.step('Locking keepers', 5):
      status.advance()
      raise RuntimeError('Bungie is down')

  with pytest.raises(RuntimeError):
    lock_while_bungie_is_down()
  assert terminal.getvalue().endswith(CLEAR_LINE)


def test_the_step_line_fits_the_terminal(monkeypatch):
  monkeypatch.setenv('COLUMNS', '20')
  terminal = TerminalStream()
  status = StatusLine(terminal)
  with status.step('Moving keepers from your inventory to the vault', 9):
    status.advance()
  assert paint('Moving keepers from', 'dim') in terminal.getvalue()
  assert 'from your' not in terminal.getvalue()


def test_a_heading_inside_a_report_keeps_its_colors():
  text = highlight('Round 1:\n  Done.\nHeads up, these break loadouts:\n  Hammerhead')
  heading = text.split('\n')[2]
  assert heading == (
    '\033[1m'
    + paint('Heads up', 'yellow')
    + '\033[1m, these break loadouts:'
    + '\033[0m'
  )


def test_long_lines_wrap_under_themselves_without_breaking_a_name():
  names = "  Ergo Sum (2), Fatebringer, Illicit Invader Gauntlets, Luna's Howl"
  assert wrap(names, 50).split('\n') == [
    '  Ergo Sum (2), Fatebringer,',
    "    Illicit Invader Gauntlets, Luna's Howl",
  ]


def test_a_command_never_breaks_and_color_takes_no_room():
  wrapped = wrap(highlight('Then run `uv run sweeper next` now.'), 24)
  assert [ANSI_CODE.sub('', line) for line in wrapped.split('\n')] == [
    'Then run',
    '  `uv run sweeper next`',
    '  now.',
  ]
  colored = highlight('Locked 689 keepers.')
  assert wrap(colored, len('Locked 689 keepers.')) == colored


def test_only_a_terminal_gets_wrapped_lines(monkeypatch):
  monkeypatch.setenv('COLUMNS', '40')
  line = 'Moved 12 keepers from your inventory to the vault (3 you were carrying).'
  log = io.StringIO()
  echo(line, log)
  assert log.getvalue() == line + '\n'
  terminal = TerminalStream()
  echo(line, terminal)
  assert ANSI_CODE.sub('', terminal.getvalue()) == (
    'Moved 12 keepers from your inventory to\n  the vault (3 you were carrying).\n'
  )
