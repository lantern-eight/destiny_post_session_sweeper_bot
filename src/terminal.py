'''Color, line wrapping, and a line naming the step a long command is on.

The reports stay plain text, which is what tests and logs see. Color and
wrapping are added only as they are printed to a terminal. Color is keyed on
what every report already uses: headings, labels such as "Next:" and
"Warning:", counts of keepers and shards, and commands in backticks. NO_COLOR
turns it off (https://no-color.org).
'''

from __future__ import annotations

import os
import re
import shutil
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TextIO

CLEAR_LINE = '\r\033[K'
RESET = '\033[0m'
ANSI_CODE = re.compile(r'\033\[[0-9;]*m')
STYLE_CODES = {
  'bold': '1',
  'dim': '2',
  'red': '31',
  'green': '32',
  'yellow': '33',
  'magenta': '35',
  'cyan': '36',
}
# Labels the commands open a line or a sentence with, and how they look.
LABEL_STYLES = {
  'Error:': ('bold', 'red'),
  'Warning:': ('yellow',),
  'Heads up': ('yellow',),
  'Careful:': ('yellow',),
  'Skipped:': ('yellow',),
  'Dry run:': ('magenta',),
  'Next:': ('bold', 'cyan'),
  'Session finished.': ('bold', 'green'),
}
COUNT_STYLES = {'keep': 'green', 'shard': 'red', 'review': 'yellow'}
HIGHLIGHTS = re.compile(
  r'(?P<command>`[^`\n]+`)'
  rf'|(?<![\w`])(?P<label>{"|".join(re.escape(label) for label in LABEL_STYLES)})'
  r'|\b(?P<count>(?P<number>\d+) (?:more |leftover )?'
  r'(?P<noun>keep|keepers?|shards?|review)\b)'
)
# Wide terminals still wrap here: longer lines are hard to follow.
MAX_LINE_WIDTH = 100
# What a line is wrapped between: words, and commands in backticks (colored or
# not, with what follows them, such as a full stop), which never break.
WRAP_PIECES = re.compile(r'(?:\033\[[0-9;]*m)*`[^`\n]*`\S*|\S+')
# A line breaks after these when it can, so a name in a list or the value
# after a label stays whole.
BREAK_AFTER = (',', ':')


def wants_color(stream: TextIO) -> bool:
  '''Color for a terminal, unless NO_COLOR is set.'''
  return is_terminal(stream) and not os.environ.get('NO_COLOR')


def is_terminal(stream: TextIO) -> bool:
  '''A terminal, not a file or a pipe: it can rewrite its last line in place.'''
  isatty = getattr(stream, 'isatty', None)
  return bool(isatty and isatty()) and os.environ.get('TERM') != 'dumb'


def paint(text: str, *styles: str) -> str:
  codes = ';'.join(STYLE_CODES[style] for style in styles)
  return f'\033[{codes}m{text}{RESET}'


def highlight(text: str) -> str:
  '''*text* in color: labels, counts, and commands, and headings in bold.

  A heading is the first line of a report, or a line ending in a colon that
  lines indented further follow.
  '''
  lines = text.split('\n')
  highlighted = []
  for index, line in enumerate(lines):
    colored = HIGHLIGHTS.sub(_paint_match, line)
    highlighted.append(_bold(colored) if _is_heading(lines, index) else colored)
  return '\n'.join(highlighted)


def wrap(text: str, width: int) -> str:
  '''Each line of *text* in lines of at most *width* characters, color codes
  aside. What does not fit goes on the lines under it, indented 2 more. A
  break falls after a comma or colon past halfway when there is one, so the
  names in a list stay whole.'''
  return '\n'.join(
    wrapped for line in text.split('\n') for wrapped in _wrap_line(line, width)
  )


def echo(text: str = '', stream: TextIO | None = None) -> None:
  '''Print *text* to stdout (or *stream*): on a terminal, in color and wrapped
  to its width. With no text, a blank line.'''
  if stream is None:
    stream = sys.stdout
  if wants_color(stream):
    text = highlight(text)
  if is_terminal(stream):
    columns = shutil.get_terminal_size().columns
    text = wrap(text, min(columns - 1, MAX_LINE_WIDTH))
  print(text, file=stream, flush=True)


class StatusLine:
  '''Says which step a long command is on.

  On a terminal it is one line, rewritten as the step goes ("Locking keepers:
  120 of 689") and cleared when the step ends, so only the report stays on
  screen. Anywhere else, such as a log, each step that gets going prints one
  plain line. Without a stream it shows no steps.
  '''

  def __init__(self, stream: TextIO | None = None) -> None:
    self.stream = stream
    self.live = stream is not None and is_terminal(stream)
    self.color = stream is not None and wants_color(stream)
    self._label = ''
    self._total: int | None = None
    self._count = 0

  @contextmanager
  def step(self, label: str, total: int | None = None) -> Iterator[None]:
    '''A step of *total* items (when known), each counted by advance().

    A step of 0 items has nothing to do, so it shows nothing. A step inside
    another hands the line back to it when it ends.
    '''
    if self.stream is None or total == 0:
      yield
      return
    outer_step = (self._label, self._total, self._count)
    self._label, self._total, self._count = label, total, 0
    if self.live:
      self._draw()
    try:
      yield
    finally:
      self._label, self._total, self._count = outer_step
      if self.live and self._label:
        self._draw()
      elif self.live:
        self._write(CLEAR_LINE)

  def advance(self) -> None:
    '''The step's next item is under way.'''
    if self.stream is None or not self._label:
      return
    self._count += 1
    if self.live:
      self._draw()
    elif self._count == 1:
      total = f' ({self._total})' if self._total else ''
      self._write(f'{self._label}{total}...\n')

  def note(self, message: str) -> None:
    '''A message for stderr that stays, such as a download starting or a
    warning. On a terminal it goes above the step line.'''
    if self.live:
      self._write(CLEAR_LINE)
    echo(message, self.stream or sys.stderr)
    if self.live and self._label:
      self._draw()

  def _draw(self) -> None:
    if not self._count:
      progress = '...'
    elif self._total:
      progress = f': {self._count} of {self._total}'
    else:
      progress = f': {self._count}'
    # One screen line, or each redraw would leave a wrapped copy behind.
    width = shutil.get_terminal_size().columns - 1
    text = (self._label + progress)[:width]
    self._write(CLEAR_LINE + (paint(text, 'dim') if self.color else text))

  def _write(self, text: str) -> None:
    self.stream.write(text)
    self.stream.flush()


# For callers with no one watching, such as tests: shows no steps.
SILENT_STATUS = StatusLine()


def _is_heading(lines: list[str], index: int) -> bool:
  line = lines[index]
  if len(lines) == 1 or not line.strip():
    return False
  following = lines[index + 1] if index + 1 < len(lines) else ''
  return index == 0 or (
    line.endswith(':')
    and bool(following.strip())
    and _indent(following) > _indent(line)
  )


def _indent(line: str) -> int:
  return len(line) - len(line.lstrip(' '))


def _bold(line: str) -> str:
  '''*line* in bold, keeping the colors already in it.'''
  bold = f'\033[{STYLE_CODES["bold"]}m'
  return bold + line.replace(RESET, RESET + bold) + RESET


def _paint_match(match: re.Match) -> str:
  if match['command']:
    return paint(match['command'], 'cyan')
  if match['label']:
    return paint(match['label'], *LABEL_STYLES[match['label']])
  style = next(
    style for start, style in COUNT_STYLES.items() if match['noun'].startswith(start)
  )
  return paint(match['count'], 'dim' if match['number'] == '0' else style)


def _wrap_line(line: str, width: int) -> list[str]:
  if _width([('', line)]) <= width:
    return [line]
  hanging = ' ' * (_indent(ANSI_CODE.sub('', line)) + 2)
  lines: list[str] = []
  # The pieces on the line being built, each with the space before it.
  current: list[tuple[str, str]] = []
  position = 0
  for match in WRAP_PIECES.finditer(line):
    gap, piece = line[position : match.start()], match.group()
    position = match.end()
    if current and _width([*current, (gap, piece)]) > width:
      kept = _pieces_to_keep(current, (gap, piece), hanging, width)
      lines.append(_join(current[:kept]))
      carried = [*current[kept:], (gap, piece)]
      current = [(hanging, carried[0][1]), *carried[1:]]
    else:
      current.append((gap, piece))
  lines.append(_join(current))
  return lines


def _pieces_to_keep(
  current: list[tuple[str, str]],
  overflow: tuple[str, str],
  hanging: str,
  width: int,
) -> int:
  '''How many of the line's pieces stay on it: all of them, or those up to its
  last comma or colon when that comes past halfway and the rest fit on the
  next line.'''
  breaks = [
    index
    for index in range(1, len(current) + 1)
    if ANSI_CODE.sub('', current[index - 1][1]).endswith(BREAK_AFTER)
  ]
  if breaks and breaks[-1] < len(current):
    kept = breaks[-1]
    rest = [(hanging, current[kept][1]), *current[kept + 1 :], overflow]
    if _width(current[:kept]) >= width // 2 and _width(rest) <= width:
      return kept
  return len(current)


def _join(pieces: list[tuple[str, str]]) -> str:
  return ''.join(space + piece for space, piece in pieces)


def _width(pieces: list[tuple[str, str]]) -> int:
  '''How many characters the pieces take on screen.'''
  return len(ANSI_CODE.sub('', _join(pieces)))
