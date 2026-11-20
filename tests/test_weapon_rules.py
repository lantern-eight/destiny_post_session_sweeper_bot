'''Tests for the weapon keep rules.'''

from __future__ import annotations

from src.preferences import ArmorPreferences, Preferences, WeaponPreferences
from src.voltron import VoltronEntry
from src.weapon_rules import WeaponDecisions, decide_weapons
from src.wishlist import WishlistEntry
from tests.weapon_fixtures import make_weapon

ARMOR_PREFERENCES = ArmorPreferences(
  archetype_order=('Powerhouse',), archetype_override_min_tier=3, stat_priority=()
)
PREFERENCES = Preferences(
  classes=('warlock',), armor=ARMOR_PREFERENCES, weapons=WeaponPreferences()
)
TRAIT_PERKS = frozenset(
  {'Explosive Payload', 'Firefly', 'Opening Shot', 'Kill Clip', 'Rapid Hit'}
)
GOD_ROLL = ('Arrowhead Brake', 'Accurized Rounds', 'Explosive Payload', 'Firefly')
VOLTRON = {'Fatebringer': [VoltronEntry(GOD_ROLL)]}
GOD_ROLL_COLUMNS = [[perk] for perk in GOD_ROLL]
# Three of the four god-roll perks: the barrel is off.
NEAR_ROLL_COLUMNS = [['Smallbore'], *GOD_ROLL_COLUMNS[1:]]


def decide(
  weapons,
  wishlist=None,
  voltron=VOLTRON,
  preferences=PREFERENCES,
) -> WeaponDecisions:
  return decide_weapons(
    weapons, preferences, wishlist or {}, voltron, set(), TRAIT_PERKS
  )


def actions(result: WeaponDecisions) -> dict[str, str]:
  return {
    instance_id: decision.action for instance_id, decision in result.decisions.items()
  }


def test_full_voltron_match_beats_a_partial_one_even_at_lower_tier():
  result = decide(
    [make_weapon('1', GOD_ROLL_COLUMNS), make_weapon('2', NEAR_ROLL_COLUMNS, tier=5)]
  )
  assert actions(result) == {'1': 'keep', '2': 'shard'}
  assert 'Voltron 3/4' in result.decisions['2'].reasons[0]
  assert result.ties == []


def test_tied_full_matches_are_all_kept_and_reported():
  copies = [
    make_weapon('1', GOD_ROLL_COLUMNS),
    make_weapon('2', GOD_ROLL_COLUMNS, tier=5),
    make_weapon('3', NEAR_ROLL_COLUMNS),
  ]
  result = decide(copies)
  assert actions(result) == {'1': 'keep', '2': 'keep', '3': 'shard'}
  assert 'tie step' in result.decisions['1'].reasons[0]
  assert result.ties == [
    {
      'weapon': 'Fatebringer',
      'group_name': 'Fatebringer',
      'shiny': False,
      'tied': ['1', '2'],
      'copies': ['1', '2', '3'],
    }
  ]


def test_your_pick_settles_a_tie():
  wishlist = {'Fatebringer': [WishlistEntry('pick', ('Explosive Payload', 'Firefly'))]}
  copies = [
    make_weapon('1', GOD_ROLL_COLUMNS),
    make_weapon(
      '2', [['Smallbore'], ['Accurized Rounds'], ['Opening Shot'], ['Firefly']]
    ),
  ]
  voltron = {
    'Fatebringer': [
      VoltronEntry(GOD_ROLL),
      VoltronEntry(('Smallbore', 'Accurized Rounds', 'Opening Shot', 'Firefly')),
    ]
  }
  result = decide(copies, wishlist=wishlist, voltron=voltron)
  assert actions(result) == {'1': 'keep', '2': 'shard'}
  assert result.ties == []


def test_each_personal_roll_keeps_its_own_best_copy():
  wishlist = {
    'Fatebringer': [
      WishlistEntry('add clear', ('Explosive Payload', 'Firefly')),
      WishlistEntry('boss damage', ('Explosive Payload', 'Opening Shot')),
    ]
  }
  copies = [
    make_weapon('1', [['Explosive Payload'], ['Firefly']]),
    make_weapon('2', [['Explosive Payload'], ['Opening Shot']]),
    make_weapon('3', [['Explosive Payload'], ['Kill Clip']], tier=5),
  ]
  result = decide(copies, wishlist=wishlist)
  assert actions(result) == {'1': 'keep', '2': 'keep', '3': 'shard'}
  assert result.decisions['1'].reasons == ["matches your 'add clear' roll"]


def test_a_roll_with_choices_keeps_one_copy_and_names_its_mode():
  wishlist = {
    'Fatebringer': [
      WishlistEntry('PvE', ('Explosive Payload', 'Firefly | Kill Clip'), mode='pve')
    ]
  }
  copies = [
    make_weapon('1', [['Explosive Payload'], ['Firefly']]),
    make_weapon('2', [['Explosive Payload'], ['Kill Clip']], tier=5),
    make_weapon('3', [['Explosive Payload'], ['Rapid Hit']], tier=5),
  ]
  # Copies 1 and 2 both match; without Voltron, tier picks the one kept.
  result = decide(copies, wishlist=wishlist, voltron={})
  assert actions(result) == {'1': 'shard', '2': 'keep', '3': 'shard'}
  assert result.decisions['2'].reasons == ["matches your 'PvE' roll"]
  # The choices are traits, so a copy with neither one does not match.
  only_copy = decide([copies[2]], wishlist=wishlist, voltron={})
  assert only_copy.decisions['3'].reasons[0].startswith('best Fatebringer copy')


def test_traits_decide_a_wishlist_match_and_barrels_only_rank():
  wishlist = {
    'Fatebringer': [
      WishlistEntry('mine', ('Arrowhead Brake', 'Explosive Payload', 'Firefly'))
    ]
  }
  copies = [
    make_weapon('1', [['Smallbore'], ['Explosive Payload'], ['Firefly']]),
    make_weapon('2', [['Arrowhead Brake'], ['Explosive Payload'], ['Firefly']]),
    make_weapon('3', [['Arrowhead Brake'], ['Explosive Payload'], ['Kill Clip']]),
  ]
  # Without copy 2, copy 1 still matches: the listed barrel is only a preference.
  assert actions(decide([copies[0], copies[2]], wishlist=wishlist)) == {
    '1': 'keep',
    '3': 'shard',
  }
  assert actions(decide(copies, wishlist=wishlist)) == {
    '1': 'shard',
    '2': 'keep',
    '3': 'shard',
  }


def test_a_copy_with_your_roll_loses_to_the_one_with_more_secondary_perks():
  wishlist = {
    'Fatebringer': [
      WishlistEntry('add clear', ('Arrowhead Brake', 'Explosive Payload', 'Firefly')),
      WishlistEntry('boss damage', ('Explosive Payload', 'Opening Shot')),
    ]
  }
  copies = [
    make_weapon('1', [['Arrowhead Brake'], ['Explosive Payload'], ['Firefly']]),
    make_weapon('2', [['Smallbore'], ['Explosive Payload'], ['Firefly']]),
    make_weapon('3', [['Smallbore'], ['Explosive Payload'], ['Opening Shot']], tier=5),
  ]
  result = decide(copies, wishlist=wishlist, voltron={})
  assert actions(result) == {'1': 'keep', '2': 'shard', '3': 'keep'}
  assert result.decisions['1'].reasons == [
    "matches your 'add clear' roll with 1 of 1 secondary perk"
  ]
  # Copy 3 is the better keeper (T5), but copy 2 lost to the one its roll keeps.
  assert result.decisions['2'].reasons == [
    'another Fatebringer copy is kept: 1 (untiered, your add clear roll with 1 of '
    '1 secondary perk, no Voltron match); this one is untiered, your add clear roll '
    'with 0 of 1 secondary perk, no Voltron match'
  ]


def test_the_masterwork_ranks_after_secondary_perks_and_before_tier():
  roll = WishlistEntry(
    'mine',
    ('Arrowhead Brake', 'Explosive Payload', 'Firefly'),
    masterwork=('Range',),
  )
  wishlist = {'Fatebringer': [roll]}
  traits = [['Explosive Payload'], ['Firefly']]
  with_masterwork = make_weapon(
    '1', [['Smallbore'], *traits], masterwork_options=('Range',)
  )
  higher_tier = make_weapon(
    '2', [['Smallbore'], *traits], tier=5, masterwork_options=('Handling',)
  )
  with_barrel = make_weapon(
    '3', [['Arrowhead Brake'], *traits], masterwork_options=('Stability',)
  )
  result = decide([with_masterwork, higher_tier], wishlist=wishlist, voltron={})
  assert actions(result) == {'1': 'keep', '2': 'shard'}
  assert result.decisions['1'].reasons == [
    "matches your 'mine' roll with 0 of 1 secondary perk and the Range masterwork"
  ]
  assert (
    'this one is T5, your mine roll with 0 of 1 secondary perk but not the Range '
    'masterwork'
  ) in result.decisions['2'].reasons[0]
  # A secondary perk counts for more than the masterwork.
  result = decide([with_masterwork, with_barrel], wishlist=wishlist, voltron={})
  assert actions(result) == {'1': 'shard', '3': 'keep'}


def test_wishlist_roll_without_traits_requires_everything_it_lists():
  wishlist = {'Fatebringer': [WishlistEntry('barrel only', ('Arrowhead Brake',))]}
  copies = [
    make_weapon('1', [['Smallbore'], ['Explosive Payload']]),
    make_weapon('2', [['Arrowhead Brake'], ['Kill Clip']]),
  ]
  assert actions(decide(copies, wishlist=wishlist)) == {'1': 'shard', '2': 'keep'}


def test_your_roll_beats_a_voltron_god_roll():
  wishlist = {'Fatebringer': [WishlistEntry('mine', ('Opening Shot',))]}
  copies = [
    make_weapon('1', GOD_ROLL_COLUMNS),
    make_weapon('2', [['Smallbore'], ['Opening Shot']]),
  ]
  assert actions(decide(copies, wishlist=wishlist)) == {'1': 'shard', '2': 'keep'}


def test_adept_breaks_ties_between_equally_close_rolls():
  copies = [
    make_weapon('1', NEAR_ROLL_COLUMNS, tier=5),
    make_weapon('2', NEAR_ROLL_COLUMNS, name='Fatebringer (Adept)', adept=True),
  ]
  assert actions(decide(copies)) == {'1': 'shard', '2': 'keep'}


def test_more_perk_options_break_ties_before_tier():
  copies = [
    make_weapon('1', NEAR_ROLL_COLUMNS, tier=5),
    make_weapon('2', [['Smallbore', 'Full Bore'], *NEAR_ROLL_COLUMNS[1:]], tier=3),
  ]
  assert actions(decide(copies)) == {'1': 'shard', '2': 'keep'}


def test_shiny_version_is_kept_separately():
  result = decide(
    [make_weapon('1', GOD_ROLL_COLUMNS), make_weapon('2', GOD_ROLL_COLUMNS, shiny=True)]
  )
  assert actions(result) == {'1': 'keep', '2': 'keep'}
  assert 'Fatebringer (shiny)' in result.decisions['2'].reasons[0]
  assert result.ties == []


def test_a_weapon_with_no_matching_roll_still_keeps_one_copy():
  result = decide([make_weapon('1', [['Smallbore'], ['Kill Clip']])])
  assert result.decisions['1'].action == 'keep'
  assert 'Voltron' in result.decisions['1'].reasons[0]


def test_never_keep_list_shards_every_copy():
  preferences = Preferences(
    classes=('warlock',),
    armor=ARMOR_PREFERENCES,
    weapons=WeaponPreferences(never_keep=('Fatebringer',)),
  )
  result = decide([make_weapon('1', GOD_ROLL_COLUMNS)], preferences=preferences)
  assert result.decisions['1'].action == 'shard'
  assert 'never_keep' in result.decisions['1'].reasons[0]


def test_white_weapons_are_always_kept_even_as_duplicates():
  quest_weapons = [
    make_weapon(instance_id, [['Rapid Hit']], name='Khvostov 7G-02', rarity='white')
    for instance_id in ('1', '2')
  ]
  assert actions(decide(quest_weapons, voltron={})) == {'1': 'keep', '2': 'keep'}


def test_exotic_keeps_crafted_copy_over_masterworked_one():
  copies = [
    make_weapon(
      '1', [['Intrinsic']], name='Revision Zero', rarity='exotic', masterworked=True
    ),
    make_weapon(
      '2', [['Intrinsic']], name='Revision Zero', rarity='exotic', crafted=True
    ),
  ]
  assert actions(decide(copies, voltron={})) == {'1': 'shard', '2': 'keep'}


def test_every_full_voltron_match_counts_the_same():
  # Voltron lists the pair on its own and with a barrel and magazine. Copy 1
  # matches the longer line; copy 2 has only the traits, but it is Adept and T5.
  wishlist = {'Fatebringer': [WishlistEntry('pick', ('Explosive Payload', 'Firefly'))]}
  voltron = {
    'Fatebringer': [
      VoltronEntry(GOD_ROLL),
      VoltronEntry(('Explosive Payload', 'Firefly')),
    ]
  }
  copies = [
    make_weapon('1', GOD_ROLL_COLUMNS, tier=1),
    make_weapon(
      '2',
      [['Smallbore'], ['Ricochet Rounds'], ['Explosive Payload'], ['Firefly']],
      name='Fatebringer (Adept)',
      adept=True,
      tier=5,
    ),
  ]
  result = decide(copies, wishlist=wishlist, voltron=voltron)
  assert actions(result) == {'1': 'shard', '2': 'keep'}


def test_a_craftable_weapon_you_have_not_crafted_keeps_five_copies():
  copies = [
    make_weapon(str(number), GOD_ROLL_COLUMNS, tier=number, craftable=True)
    for number in range(1, 6)
  ]
  copies.append(make_weapon('6', NEAR_ROLL_COLUMNS, tier=5, craftable=True))
  result = decide(copies)
  assert actions(result) == {**dict.fromkeys('12345', 'keep'), '6': 'shard'}
  assert result.decisions['1'].reasons == [
    'no crafted Fatebringer yet: one of up to 5 copies kept toward its crafting pattern'
  ]
  # Five full matches, but no tie: they are all kept for the pattern anyway.
  assert result.ties == []
  assert result.crafting == {'Fatebringer': 5}
  # The shard is compared against the two best of them.
  assert result.decisions['6'].compare_with == ('5', '4')


def test_a_crafted_copy_ends_the_pattern_hunt():
  copies = [
    make_weapon('1', GOD_ROLL_COLUMNS, crafted=True, craftable=True),
    make_weapon('2', NEAR_ROLL_COLUMNS, tier=5, craftable=True),
    make_weapon('3', NEAR_ROLL_COLUMNS, tier=4, craftable=True),
  ]
  result = decide(copies)
  assert actions(result) == {'1': 'keep', '2': 'shard', '3': 'shard'}
  assert result.crafting == {}


def test_only_the_craftable_version_counts_toward_the_pattern():
  copies = [
    make_weapon('1', GOD_ROLL_COLUMNS, name='Fatebringer (Adept)', adept=True),
    make_weapon('2', NEAR_ROLL_COLUMNS, craftable=True),
    make_weapon('3', NEAR_ROLL_COLUMNS, tier=5),
  ]
  result = decide(copies)
  # The Adept copy is still the best one, and no other version gives progress.
  assert actions(result) == {'1': 'keep', '2': 'keep', '3': 'shard'}
  assert 'best Fatebringer copy' in result.decisions['1'].reasons[0]
  assert result.crafting == {'Fatebringer': 1}


def test_ergo_sum_keeps_one_copy_per_frame_and_trait_pair():
  def ergo_sum(instance_id: str, frame: str, tier: int = 0):
    return make_weapon(
      instance_id,
      [['Hungry Edge']],
      name='Ergo Sum',
      rarity='exotic',
      tier=tier,
      pair=(frame, 'Arc Conductor'),
    )

  copies = [
    ergo_sum('1', 'Caster Frame'),
    ergo_sum('2', 'Caster Frame', tier=4),
    ergo_sum('3', 'Vortex Frame'),
  ]
  result = decide(copies)
  assert actions(result) == {'1': 'shard', '2': 'keep', '3': 'keep'}
  assert result.decisions['2'].reasons == [
    'exotic Ergo Sum, Caster Frame + Arc Conductor (T4)'
  ]
  assert result.decisions['1'].reasons == [
    'another Ergo Sum copy with Caster Frame + Arc Conductor is kept: 2 (T4)'
  ]
  assert result.decisions['1'].compare_with == ('2',)


def test_green_and_blue_weapons_are_shards():
  weapons = [
    make_weapon('1', [['Rapid Hit']], name='Blue Gun', rarity='blue'),
    make_weapon('2', [['Rapid Hit']], name='Green Gun', rarity='green'),
  ]
  result = decide(weapons, voltron={})
  assert actions(result) == {'1': 'shard', '2': 'shard'}
  assert result.decisions['1'].reasons == [
    'blue weapon: only white, legendary, and exotic weapons are kept'
  ]
