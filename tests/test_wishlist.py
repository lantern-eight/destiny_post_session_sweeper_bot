'''Tests for reading your own rolls from wishlist.yaml.'''

from __future__ import annotations

import pytest

from src.wishlist import (
  WishlistEntry,
  WishlistError,
  add_rolls,
  column_labels,
  load_wishlist,
)


def write_wishlist(tmp_path, text: str):
  path = tmp_path / 'wishlist.yaml'
  path.write_text(text)
  return path


def test_weapon_and_perk_names_are_normalized_and_labels_default(tmp_path):
  path = write_wishlist(
    tmp_path,
    'Fatebringer (Adept):\n'
    '  - label: add clear\n'
    '    perks: [Explosive Payload, Golden Tricorn Enhanced]\n'
    '  - perks: [Firefly]\n',
  )
  assert load_wishlist(path) == {
    'Fatebringer': [
      WishlistEntry('add clear', ('Explosive Payload', 'Golden Tricorn')),
      WishlistEntry('roll 2', ('Firefly',)),
    ]
  }


def test_choices_and_a_mode_are_read(tmp_path):
  path = write_wishlist(
    tmp_path,
    'Blast Furnace:\n'
    '  - mode: PvP\n'
    '    perks: [Headseeker|Zen Moment, Kill Clip Enhanced | Desperado]\n'
    '  - label: add clear\n'
    '    mode: pve\n'
    '    perks: [Kinetic Tremors]\n',
  )
  entries = load_wishlist(path)['Blast Furnace']
  assert entries == [
    WishlistEntry(
      'PvP', ('Headseeker | Zen Moment', 'Kill Clip | Desperado'), mode='pvp'
    ),
    WishlistEntry('add clear', ('Kinetic Tremors',), mode='pve'),
  ]
  assert [entry.title for entry in entries] == ['PvP', 'add clear (PvE)']


def test_perks_are_read_one_column_to_a_line(tmp_path):
  path = write_wishlist(
    tmp_path,
    'Blast Furnace:\n'
    '  - mode: pve\n'
    '    perks:\n'
    '      - perk_1: Kinetic Tremors\n'
    '      - perk_2: [Chaos Reshaped, Firefly Enhanced]\n'
    "      - origin_trait: Forge's Kin\n"
    '      - Fluted Barrel\n'
    '      - masterwork: Handling\n',
  )
  (entry,) = load_wishlist(path)['Blast Furnace']
  assert entry == WishlistEntry(
    'PvE',
    ('Kinetic Tremors', 'Chaos Reshaped | Firefly', "Forge's Kin", 'Fluted Barrel'),
    mode='pve',
    masterwork=('Handling',),
  )
  # A bare name is the older layout, with no column.
  assert entry.columns == ('perk_1', 'perk_2', 'origin_trait', '')


def test_columns_are_named_the_way_the_game_places_each_perk():
  categories = {
    'Feeding Frenzy': frozenset({'frames'}),
    'Target Lock': frozenset({'frames'}),
    "Forge's Kin": frozenset({'origins'}),
    'Spike Grenades': frozenset({'magazines_gl'}),
  }
  perks = [
    'Feeding Frenzy',
    "Forge's Kin",
    'Target Lock',
    'Spike Grenades',
    'Not In The Game',
  ]
  assert column_labels(perks, categories) == (
    'perk_1',
    'origin_trait',
    'perk_2',
    'magazine',
    '',
  )


def test_a_mode_other_than_pve_or_pvp_is_refused(tmp_path):
  path = write_wishlist(
    tmp_path, 'Fatebringer:\n  - mode: raids\n    perks: [Firefly]\n'
  )
  with pytest.raises(WishlistError, match="roll 1 has mode 'raids': use pve or pvp"):
    load_wishlist(path)


def test_added_rolls_keep_their_mode_columns_choices_and_masterwork(tmp_path):
  path = tmp_path / 'wishlist.yaml'
  roll = WishlistEntry(
    'PvE',
    ('Kinetic Tremors', 'Chaos Reshaped | Firefly'),
    mode='pve',
    columns=('perk_1', 'perk_2'),
    masterwork=('Handling',),
  )
  add_rolls('Blast Furnace', [roll], path)
  assert path.read_text() == (
    'Blast Furnace:\n'
    '  - mode: pve\n'
    '    perks:\n'
    '      - perk_1: Kinetic Tremors\n'
    '      - perk_2: [Chaos Reshaped, Firefly]\n'
    '      - masterwork: Handling\n'
  )
  (loaded,) = load_wishlist(path)['Blast Furnace']
  assert loaded == roll
  assert loaded.columns == ('perk_1', 'perk_2')


def test_comment_only_file_means_no_rolls(tmp_path):
  assert load_wishlist(write_wishlist(tmp_path, '# nothing yet\n')) == {}


def test_missing_file_means_no_rolls(tmp_path):
  assert load_wishlist(tmp_path / 'missing.yaml') == {}


def test_roll_without_perks_is_rejected(tmp_path):
  path = write_wishlist(tmp_path, 'Fatebringer:\n  - label: add clear\n')
  with pytest.raises(WishlistError, match='Fatebringer roll 1'):
    load_wishlist(path)


def test_added_rolls_keep_the_comments_of_an_empty_wishlist(tmp_path):
  path = tmp_path / 'wishlist.yaml'
  path.write_text(
    '# Your own weapon rolls.\n#\n# Fatebringer:\n#   - label: add clear\n'
  )
  added = add_rolls(
    'Hammerhead',
    [WishlistEntry('Feeding Frenzy + Target Lock', ('Feeding Frenzy', 'Target Lock'))],
    path,
  )
  assert len(added) == 1
  text = path.read_text()
  assert text.startswith(
    '# Your own weapon rolls.\n#\n# Fatebringer:\n#   - label: add clear\n'
  )
  # A roll without columns is written by name alone.
  assert text.endswith(
    'Hammerhead:\n  - label: Feeding Frenzy + Target Lock\n'
    '    perks:\n      - Feeding Frenzy\n      - Target Lock\n'
  )
  assert load_wishlist(path)['Hammerhead'][0].perks == ('Feeding Frenzy', 'Target Lock')


def test_added_rolls_join_the_weapon_and_skip_repeats(tmp_path):
  path = tmp_path / 'wishlist.yaml'
  path.write_text(
    '# My rolls\n'
    'Hammerhead (Adept):  # the good one\n'
    '  - label: mine\n'
    '    perks: [Feeding Frenzy, Target Lock]\n'
    'Fatebringer: []\n'
  )
  rolls = [
    WishlistEntry('again', ('Target Lock', 'Feeding Frenzy')),
    WishlistEntry('Subsistence + Rampage', ('Subsistence', 'Rampage')),
  ]
  added = add_rolls('Hammerhead', rolls, path)
  assert [roll.label for roll in added] == ['Subsistence + Rampage']
  add_rolls(
    'Fatebringer', [WishlistEntry('pick', ('Explosive Payload', 'Firefly'))], path
  )
  text = path.read_text()
  assert '# My rolls' in text
  assert 'Hammerhead (Adept):  # the good one' in text
  wishlist = load_wishlist(path)
  assert [entry.label for entry in wishlist['Hammerhead']] == [
    'mine',
    'Subsistence + Rampage',
  ]
  assert wishlist['Fatebringer'][0].perks == ('Explosive Payload', 'Firefly')


def test_a_new_weapon_goes_at_the_end_and_the_rest_stays_as_written(tmp_path):
  written = (
    'Fatebringer:  # hand-wrapped\n'
    '  - label: mine\n'
    '    perks: [Explosive Payload | Opening Shot, Firefly | Kill Clip,\n'
    '      Arrowhead Brake]\n'
  )
  path = write_wishlist(tmp_path, written)
  roll = WishlistEntry(
    'pick', ('Cascade Point', 'Explosive Light'), columns=('perk_1', 'perk_2')
  )
  add_rolls('Edge Transit', [roll], path)
  assert path.read_text() == (
    f'{written}\n'
    'Edge Transit:\n'
    '  - label: pick\n'
    '    perks:\n'
    '      - perk_1: Cascade Point\n'
    '      - perk_2: Explosive Light\n'
  )
  assert list(load_wishlist(path)) == ['Fatebringer', 'Edge Transit']


def test_rolls_record_when_and_in_which_game_version_they_were_added(tmp_path):
  path = tmp_path / 'wishlist.yaml'
  roll = WishlistEntry(
    'Outlaw + Kill Clip',
    ('Outlaw', 'Kill Clip'),
    added='2026-09-27',
    game_version='244213.26.06.29.2000-1-bnet.65864',
    columns=('perk_1', 'perk_2'),
  )
  add_rolls('Fatebringer', [roll], path)
  assert path.read_text() == (
    'Fatebringer:\n'
    '  - label: Outlaw + Kill Clip\n'
    '    perks:\n'
    '      - perk_1: Outlaw\n'
    '      - perk_2: Kill Clip\n'
    '    added: 2026-09-27\n'
    '    game_version: 244213.26.06.29.2000-1-bnet.65864\n'
  )
  assert load_wishlist(path)['Fatebringer'] == [roll]


def test_a_weapon_listed_twice_is_refused_instead_of_dropped(tmp_path):
  path = write_wishlist(
    tmp_path,
    'Fatebringer:\n  - perks: [Firefly]\nHammerhead: []\nFatebringer:\n'
    '  - perks: [Outlaw]\n',
  )
  message = r'lists Fatebringer more than once \(lines 1 and 4\)'
  with pytest.raises(WishlistError, match=message):
    load_wishlist(path)
  with pytest.raises(WishlistError, match=message):
    add_rolls('Fatebringer', [WishlistEntry('pick', ('Outlaw', 'Kill Clip'))], path)


@pytest.mark.parametrize(
  ('text', 'message'),
  [
    ('Fatebringer: [Firefly\n', 'not valid YAML'),
    ('Fatebringer: Firefly\n', 'must be a list of rolls'),
    (
      'Fatebringer:\n  - perks:\n      - {perk_1: Firefly, perk_2: Rampage}\n',
      'one column and its perk to a line',
    ),
    ('Fatebringer:\n  - perks:\n      - barrel:\n', 'nothing under barrel'),
    ('Fatebringer:\n  - perks:\n      - masterwork: Range\n', 'needs a list of perks'),
  ],
)
def test_broken_wishlists_say_what_is_wrong(tmp_path, text, message):
  with pytest.raises(WishlistError, match=message):
    load_wishlist(write_wishlist(tmp_path, text))
