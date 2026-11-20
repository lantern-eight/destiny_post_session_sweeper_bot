'''Tests for reading the Voltron wishlist into named, mode-filtered rolls.'''

from __future__ import annotations

import pytest

from src import voltron
from src.voltron import (
  VoltronEntry,
  VoltronError,
  classify_mode,
  load_voltron_entries,
)

ITEM_DEFINITIONS = {
  '100': {'displayProperties': {'name': 'Fatebringer'}},
  '101': {'displayProperties': {'name': 'Fatebringer (Adept)'}},
  '201': {'displayProperties': {'name': 'Explosive Payload'}},
  '202': {'displayProperties': {'name': 'Firefly'}},
  '203': {'displayProperties': {'name': 'Opening Shot'}},
  '204': {'displayProperties': {'name': 'Golden Tricorn Enhanced'}},
}

VOLTRON_TEXT = '''title:PvE Podcast 99 - Hand Cannons

// Fatebringer - PvE god roll
//notes:Great for clearing adds (PvE-God M+KB)
dimwishlist:item=100&perks=201,202
dimwishlist:item=101&perks=201,202

// Fatebringer - duel roll
dimwishlist:item=100&perks=203,204#notes:|tags:mkb,pvp
dimwishlist:item=100&perks=201,999
dimwishlist:item=-100&perks=201
'''


@pytest.fixture
def voltron_path(tmp_path):
  path = tmp_path / 'voltron.txt'
  path.write_text(VOLTRON_TEXT)
  return path


def test_pve_entries_are_named_deduplicated_and_pvp_is_dropped(voltron_path):
  entries = load_voltron_entries(voltron_path, ITEM_DEFINITIONS, ('pve',))
  assert entries == {'Fatebringer': [VoltronEntry(('Explosive Payload', 'Firefly'))]}


def test_pvp_entries_are_kept_when_pvp_is_enabled(voltron_path):
  entries = load_voltron_entries(voltron_path, ITEM_DEFINITIONS, ('pve', 'pvp'))
  assert VoltronEntry(('Opening Shot', 'Golden Tricorn')) in entries['Fatebringer']


def test_mode_comes_from_the_first_text_that_names_one():
  assert classify_mode('|tags:mkb,pvp', 'PvE heading') == 'pvp'
  assert classify_mode('', 'Fatebringer - PvE god roll') == 'pve'
  assert classify_mode('pve and pvp') == 'any'
  assert classify_mode('', '', 'The Best Hand Cannons') == 'any'
  assert classify_mode('Crucible favorite') == 'pvp'


def test_entries_know_which_perks_are_traits(tmp_path):
  definitions = {
    **ITEM_DEFINITIONS,
    '205': {
      'displayProperties': {'name': 'Arrowhead Brake'},
      'plug': {'plugCategoryIdentifier': 'barrels'},
    },
    '206': {
      'displayProperties': {'name': 'Demolitionist'},
      'plug': {'plugCategoryIdentifier': 'frames'},
    },
    '207': {
      'displayProperties': {'name': 'Firefly'},
      'plug': {'plugCategoryIdentifier': 'frames'},
    },
  }
  path = tmp_path / 'voltron.txt'
  path.write_text('dimwishlist:item=100&perks=205,206,207#notes:pve\n')
  entry = load_voltron_entries(path, definitions, ('pve',))['Fatebringer'][0]
  assert entry.traits == ('Demolitionist', 'Firefly')
  assert entry.parts == (('barrels', 'Arrowhead Brake'),)


def test_parsed_rolls_are_cached_until_the_inputs_change(voltron_path, monkeypatch):
  cache_path = voltron_path.with_name('parsed.json')
  entries = load_voltron_entries(
    voltron_path, ITEM_DEFINITIONS, ('pve',), 'v1', parsed_cache_path=cache_path
  )

  def parse_again(*arguments):
    raise AssertionError('parsed again instead of using the cache')

  monkeypatch.setattr(voltron, '_parse', parse_again)
  cached = load_voltron_entries(
    voltron_path, ITEM_DEFINITIONS, ('pve',), 'v1', parsed_cache_path=cache_path
  )
  assert cached == entries
  with pytest.raises(AssertionError, match='parsed again'):
    load_voltron_entries(
      voltron_path, ITEM_DEFINITIONS, ('pve',), 'v2', parsed_cache_path=cache_path
    )


def test_too_many_unknown_perks_means_voltron_cannot_be_trusted(tmp_path):
  path = tmp_path / 'voltron.txt'
  known = 'dimwishlist:item=100&perks=201,202\n' * 900
  unknown = 'dimwishlist:item=100&perks=201,999\n' * 100
  path.write_text(known + unknown)
  with pytest.raises(VoltronError, match='1,000 Voltron rolls name weapons or perks'):
    load_voltron_entries(path, ITEM_DEFINITIONS, ('pve',))
