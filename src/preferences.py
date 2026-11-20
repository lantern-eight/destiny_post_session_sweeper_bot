'''User preferences from config.yaml (committed; holds no secrets).'''

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from src.game_data import (
  ARCHETYPE_STATS,
  CLASS_NAMES,
  MODE_NAMES,
  STAT_DISPLAY_ORDER,
  STAT_NAMES,
)
from src.paths import CONFIG_PATH
from src.weapons import normalize_weapon_name

KNOWN_CLASSES = tuple(name.lower() for name in CLASS_NAMES.values())
KNOWN_VOLTRON_MODES = tuple(MODE_NAMES)


class PreferencesError(ValueError):
  '''config.yaml is missing a setting or names something that does not exist.'''


@dataclass(frozen=True)
class ExoticOverride:
  archetype_order: tuple[str, ...] | None = None
  keep_per_archetype: tuple[str, ...] = ()


@dataclass(frozen=True)
class ArmorPreferences:
  archetype_order: tuple[str, ...]
  archetype_override_min_tier: int
  stat_priority: tuple[str, ...]
  exotic_overrides: dict[str, ExoticOverride] = field(default_factory=dict)
  # Armor perks kept like a set: the best piece with each, per class and slot.
  perk_sets: tuple[str, ...] = ()


@dataclass(frozen=True)
class WeaponPreferences:
  voltron_modes: tuple[str, ...] = ('pve',)
  never_keep: tuple[str, ...] = ()


@dataclass(frozen=True)
class Preferences:
  classes: tuple[str, ...]
  armor: ArmorPreferences
  weapons: WeaponPreferences = field(default_factory=WeaponPreferences)


def load_preferences(path: Path = CONFIG_PATH) -> Preferences:
  try:
    raw_config = yaml.safe_load(path.read_text()) or {}
  except yaml.YAMLError as error:
    raise PreferencesError(f'config.yaml is not valid YAML: {error}') from error
  if not isinstance(raw_config, dict):
    raise PreferencesError('config.yaml must be a list of settings (see the README).')
  armor_config = _section(raw_config, 'armor')
  weapon_config = _section(raw_config, 'weapons')
  return Preferences(
    weapons=WeaponPreferences(
      voltron_modes=_voltron_modes(
        _names(weapon_config, 'voltron_modes', 'weapons.voltron_modes', ['pve'])
      ),
      never_keep=tuple(
        normalize_weapon_name(name)
        for name in _names(
          weapon_config, 'never_keep', 'weapons.never_keep', [], allow_empty=True
        )
      ),
    ),
    classes=_classes(_names(raw_config, 'classes', 'classes', list(KNOWN_CLASSES))),
    armor=ArmorPreferences(
      archetype_order=_archetypes(
        _names(
          armor_config,
          'archetype_order',
          'armor.archetype_order',
          [],
          allow_empty=True,
        )
      ),
      archetype_override_min_tier=_tier(
        armor_config.get('archetype_override_min_tier', 3)
      ),
      stat_priority=_stats(
        _names(armor_config, 'stat_priority', 'armor.stat_priority', list(STAT_NAMES))
      ),
      exotic_overrides=_exotic_overrides(armor_config.get('exotic_overrides') or {}),
      perk_sets=tuple(
        _names(armor_config, 'perk_sets', 'armor.perk_sets', [], allow_empty=True)
      ),
    ),
  )


def _section(raw_config: dict, key: str) -> dict:
  section = raw_config.get(key) or {}
  if not isinstance(section, dict):
    raise PreferencesError(f'{key} in config.yaml must hold settings, not a value.')
  return section


def _names(
  section: dict,
  key: str,
  setting: str,
  default: list[str],
  allow_empty: bool = False,
) -> list[str]:
  '''A list of names from config.yaml. A single name counts as a list of one.'''
  if key not in section:
    return default
  value = section[key]
  if value is None:
    value = []
  elif isinstance(value, str):
    value = [value]
  if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
    raise PreferencesError(
      f'{setting} in config.yaml must be a list of names, like [a, b] (got {value!r}).'
    )
  if not value and not allow_empty:
    raise PreferencesError(f'{setting} in config.yaml is empty; list at least one.')
  return value


def _classes(values: list[str]) -> tuple[str, ...]:
  classes = tuple(value.lower() for value in values)
  unknown = [value for value in classes if value not in KNOWN_CLASSES]
  if unknown:
    raise PreferencesError(f'Unknown class in config.yaml: {", ".join(unknown)}')
  return classes


def _archetypes(values: list[str]) -> tuple[str, ...]:
  unknown = [value for value in values if value not in ARCHETYPE_STATS]
  if unknown:
    raise PreferencesError(
      f'Unknown archetype in config.yaml: {", ".join(unknown)}. '
      f'Known: {", ".join(ARCHETYPE_STATS)}'
    )
  return tuple(values)


def _stats(values: list[str]) -> tuple[str, ...]:
  unknown = [value for value in values if value not in STAT_NAMES]
  if unknown:
    raise PreferencesError(
      f'Unknown stat in config.yaml: {", ".join(unknown)}. '
      f'Known: {", ".join(STAT_DISPLAY_ORDER)}'
    )
  return tuple(values)


def _voltron_modes(values: list[str]) -> tuple[str, ...]:
  modes = tuple(value.lower() for value in values)
  unknown = [mode for mode in modes if mode not in KNOWN_VOLTRON_MODES]
  if unknown:
    raise PreferencesError(
      f'Unknown voltron_modes in config.yaml: {", ".join(unknown)} (use pve, pvp)'
    )
  return modes


def _tier(value: object) -> int:
  # YAML reads yes/no as True/False, which Python would take for 1 and 0.
  if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
    raise PreferencesError(f'archetype_override_min_tier must be 1-5, got {value!r}')
  return value


def _exotic_overrides(raw_overrides: object) -> dict[str, ExoticOverride]:
  if not isinstance(raw_overrides, dict):
    raise PreferencesError(
      'armor.exotic_overrides in config.yaml must map exotic names to settings.'
    )
  overrides = {}
  for exotic_name, settings in raw_overrides.items():
    settings = settings or {}
    if not isinstance(settings, dict):
      raise PreferencesError(
        f'armor.exotic_overrides.{exotic_name} in config.yaml must hold settings.'
      )
    setting = f'armor.exotic_overrides.{exotic_name}'
    archetype_order = _names(
      settings, 'archetype_order', f'{setting}.archetype_order', [], allow_empty=True
    )
    overrides[exotic_name] = ExoticOverride(
      archetype_order=_archetypes(archetype_order) if archetype_order else None,
      keep_per_archetype=_archetypes(
        _names(
          settings,
          'keep_per_archetype',
          f'{setting}.keep_per_archetype',
          [],
          allow_empty=True,
        )
      ),
    )
  return overrides
