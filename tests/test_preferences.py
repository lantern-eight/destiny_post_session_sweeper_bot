'''Tests for reading and validating config.yaml.'''

from __future__ import annotations

import pytest

from src.preferences import PreferencesError, load_preferences


def write_config(tmp_path, text: str):
  config_path = tmp_path / 'config.yaml'
  config_path.write_text(text)
  return config_path


def test_repository_config_loads_the_agreed_defaults():
  preferences = load_preferences()
  assert preferences.classes == ('warlock', 'hunter', 'titan')
  assert preferences.armor.archetype_order == ('Powerhouse', 'Colossus', 'Gunner')
  assert preferences.armor.archetype_override_min_tier == 3
  assert preferences.armor.stat_priority[0] == 'Weapons'
  assert preferences.armor.perk_sets == ("Iron Lord's Pride",)


def test_perk_sets_default_to_none(tmp_path):
  config_path = write_config(tmp_path, 'armor:\n  archetype_order: [Powerhouse]\n')
  assert load_preferences(config_path).armor.perk_sets == ()


def test_exotic_overrides_are_parsed(tmp_path):
  config_path = write_config(
    tmp_path,
    'armor:\n'
    '  archetype_order: [Powerhouse]\n'
    '  exotic_overrides:\n'
    '    Celestial Nighthawk:\n'
    '      archetype_order: [Colossus, Paragon]\n'
    '    Ophidian Aspect:\n'
    '      keep_per_archetype: [Powerhouse, Gunner]\n',
  )
  overrides = load_preferences(config_path).armor.exotic_overrides
  assert overrides['Celestial Nighthawk'].archetype_order == ('Colossus', 'Paragon')
  assert overrides['Ophidian Aspect'].keep_per_archetype == ('Powerhouse', 'Gunner')


def test_unknown_archetype_is_rejected(tmp_path):
  config_path = write_config(tmp_path, 'armor:\n  archetype_order: [Powerhose]\n')
  with pytest.raises(PreferencesError, match='Powerhose'):
    load_preferences(config_path)


def test_unknown_stat_is_rejected(tmp_path):
  config_path = write_config(tmp_path, 'armor:\n  stat_priority: [Mobility]\n')
  with pytest.raises(PreferencesError, match='Mobility'):
    load_preferences(config_path)


def test_minimum_tier_must_be_one_to_five(tmp_path):
  config_path = write_config(tmp_path, 'armor:\n  archetype_override_min_tier: 7\n')
  with pytest.raises(PreferencesError, match='1-5'):
    load_preferences(config_path)


def test_weapon_settings_are_parsed(tmp_path):
  config_path = write_config(
    tmp_path,
    'weapons:\n  voltron_modes: [pve, pvp]\n  never_keep: [Fatebringer (Adept)]\n',
  )
  weapons = load_preferences(config_path).weapons
  assert weapons.voltron_modes == ('pve', 'pvp')
  assert weapons.never_keep == ('Fatebringer',)


def test_unknown_voltron_mode_is_rejected(tmp_path):
  config_path = write_config(tmp_path, 'weapons:\n  voltron_modes: [gambit]\n')
  with pytest.raises(PreferencesError, match='gambit'):
    load_preferences(config_path)


def test_a_single_name_counts_as_a_list_of_one(tmp_path):
  config_path = write_config(
    tmp_path, 'classes: warlock\nweapons:\n  never_keep: Hammerhead\n'
  )
  preferences = load_preferences(config_path)
  assert preferences.classes == ('warlock',)
  assert preferences.weapons.never_keep == ('Hammerhead',)


@pytest.mark.parametrize(
  ('text', 'message'),
  [
    ('classes:\n', 'classes in config.yaml is empty'),
    ('classes: [warlock, 3]\n', 'must be a list of names'),
    ('armor:\n  archetype_override_min_tier: yes\n', 'must be 1-5, got True'),
    ('armor: Powerhouse\n', 'armor in config.yaml must hold settings'),
    ('classes: [warlock\n', 'not valid YAML'),
    (
      'weapons:\n  voltron_modes: []\n',
      'weapons.voltron_modes in config.yaml is empty',
    ),
  ],
)
def test_config_mistakes_get_a_clear_message(tmp_path, text, message):
  with pytest.raises(PreferencesError, match=message):
    load_preferences(write_config(tmp_path, text))
