'''Small helpers for the text the commands print.'''

from __future__ import annotations


def count_label(number: int, singular: str, plural: str | None = None) -> str:
  '''"1 shard", "3 shards", or with an irregular plural, "2 tie-step copies".'''
  return f'{number} {singular if number == 1 else plural or singular + "s"}'


def join_sections(*sections: list[str]) -> str:
  '''A report from its sections, each a list of lines, with a blank line
  between them. Empty sections are left out.'''
  return '\n\n'.join('\n'.join(section) for section in sections if section)
