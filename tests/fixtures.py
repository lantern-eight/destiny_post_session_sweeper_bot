'''Synthetic profile and manifest definitions for tests (no real account data).'''

from __future__ import annotations

from src.game_data import (
  POSTMASTER_BUCKET_HASH,
  STAT_NAMES,
  STAT_NAMES_BY_HASH,
  TUNING_PLUG_CATEGORY,
  VAULT_BUCKET_HASH,
)
from src.inventory import ArmorPiece
from src.preferences import ArmorPreferences, Preferences, WeaponPreferences

STAT_HASHES = {name: stat_hash for stat_hash, name in STAT_NAMES_BY_HASH.items()}
# The agreed defaults, fixed here so editing config.yaml never breaks a test.
PREFERENCES = Preferences(
  classes=('warlock', 'hunter', 'titan'),
  armor=ArmorPreferences(
    archetype_order=('Powerhouse', 'Colossus', 'Gunner'),
    archetype_override_min_tier=3,
    stat_priority=('Weapons', 'Super', 'Health', 'Grenade', 'Class', 'Melee'),
  ),
  weapons=WeaponPreferences(voltron_modes=('pve',)),
)
HELMET_BUCKET_HASH = 3448274439
CLASS_ITEM_BUCKET_HASH = 1585787867
TECHSEC_SET_HASH = 555


def make_piece(
  instance_id: str,
  stats: dict[str, int],
  *,
  tier: int = 5,
  archetype: str | None = None,
  tuned: str | None = None,
  set_name: str | None = None,
  raid: str | None = None,
  exotic: bool = False,
  rarity: str = 'legendary',
  name: str = 'Helm',
  perks: tuple[str, ...] = (),
  armor_perks: tuple[str, ...] = (),
  class_name: str = 'Warlock',
  masterworked: bool = False,
  locked: bool = False,
) -> ArmorPiece:
  '''A legendary Warlock helmet in the vault unless told otherwise.'''
  return ArmorPiece(
    instance_id=instance_id,
    name=name,
    class_name=class_name,
    slot='Helmet',
    rarity='exotic' if exotic else rarity,
    tier=tier,
    archetype=archetype,
    base_stats={**dict.fromkeys(STAT_NAMES, 0), **stats},
    tuned_stat=tuned,
    set_name=set_name,
    raid=raid,
    perks=armor_perks,
    exotic_perks=perks,
    is_locked=locked,
    is_masterworked=masterworked,
    location='vault',
  )


def investment(stat: str, value: int, conditional: bool = False) -> dict:
  return {
    'statTypeHash': STAT_HASHES[stat],
    'value': value,
    'isConditionallyActive': conditional,
  }


def plug(name: str, category: str, *investments: dict) -> dict:
  return {
    'displayProperties': {'name': name},
    'plug': {'plugCategoryIdentifier': category},
    'investmentStats': list(investments),
  }


def armor_definition(
  name: str, class_type: int, bucket_hash: int, tier_type: int, set_hash: int = 0
) -> dict:
  equipping_block = {'equipableItemSetHash': set_hash} if set_hash else {}
  return {
    'itemType': 2,
    'classType': class_type,
    'displayProperties': {'name': name},
    'inventory': {'bucketTypeHash': bucket_hash, 'tierType': tier_type},
    'equippingBlock': equipping_block,
  }


ITEM_DEFINITIONS = {
  # A T5 Warlock Powerhouse helmet from the Techsec set.
  '1000': armor_definition('Techsec Hood', 2, HELMET_BUCKET_HASH, 5, TECHSEC_SET_HASH),
  # A legacy Titan helmet.
  '1001': armor_definition('Old Helm', 0, HELMET_BUCKET_HASH, 5),
  # A new-system exotic Warlock class item.
  '1002': armor_definition('Solipsism', 2, CLASS_ITEM_BUCKET_HASH, 6),
  '2000': plug('Powerhouse', 'armor_archetypes'),
  '2001': plug('', 'armor_stats', investment('Weapons', 30)),
  '2002': plug('', 'armor_stats', investment('Super', 25)),
  '2003': plug('', 'armor_stats', investment('Health', 20)),
  '2004': plug(
    'Upgrade Armor',
    'v460.plugs.armor.masterworks',
    investment('Melee', 5, conditional=True),
  ),
  '2005': plug('Empty Tuning Mod Socket', TUNING_PLUG_CATEGORY),
  '2006': plug(
    '+Weapons / -Health',
    TUNING_PLUG_CATEGORY,
    investment('Weapons', 5),
    investment('Health', -5),
  ),
  '2007': plug(
    '+Weapons / -Melee',
    TUNING_PLUG_CATEGORY,
    investment('Weapons', 5),
    investment('Melee', -5),
  ),
  '2008': plug(
    'Balanced Tuning',
    TUNING_PLUG_CATEGORY,
    *[investment(stat, 1, conditional=True) for stat in STAT_HASHES],
  ),
  '2009': plug('Weapons Mod', 'enhancements.v2_general', investment('Weapons', 10)),
  '2010': plug(
    '',
    'intrinsics',
    investment('Weapons', 9),
    investment('Class', 5),
    investment('Health', 1),
  ),
  '2011': plug(
    '',
    'intrinsics',
    investment('Grenade', 7),
    investment('Super', 1),
    investment('Melee', 7),
  ),
  '2012': plug(
    'Spirit of the Stag',
    'intrinsics',
    investment('Health', 30),
    investment('Class', 25),
  ),
  '2013': plug('Spirit of the Swarm', 'intrinsics'),
  '2014': plug('Bulwark', 'armor_archetypes'),
  '2015': plug('', 'armor_stats', investment('Grenade', 20)),
  '2016': plug(
    '+Health / -Melee',
    TUNING_PLUG_CATEGORY,
    investment('Health', 5),
    investment('Melee', -5),
  ),
  '2018': plug("Iron Lord's Pride", 'intrinsics'),
  # The empty raid mod socket on Vault of Glass armor.
  '2017': {
    **plug('Empty Mod Socket', 'enhancements.raid_v520'),
    'itemTypeDisplayName': 'Vault of Glass Armor Mod',
  },
}

SET_DEFINITIONS = {str(TECHSEC_SET_HASH): {'displayProperties': {'name': 'Techsec'}}}


def make_profile() -> dict:
  return {
    'profile': {
      'data': {
        'userInfo': {
          'bungieGlobalDisplayName': 'Guardian',
          'bungieGlobalDisplayNameCode': 7,
        }
      }
    },
    'characters': {
      'data': {
        'character-a': {'classType': 2, 'dateLastPlayed': '2026-09-26T21:00:00Z'}
      }
    },
    'profileInventory': {
      'data': {
        'items': [
          # Masterworked (state 4) T5 helmet in the vault.
          {
            'itemHash': 1000,
            'itemInstanceId': '11',
            'bucketHash': VAULT_BUCKET_HASH,
            'state': 4,
          },
          {
            'itemHash': 1002,
            'itemInstanceId': '13',
            'bucketHash': VAULT_BUCKET_HASH,
            'state': 0,
          },
        ]
      }
    },
    'characterInventories': {
      'data': {
        'character-a': {
          'items': [
            # Locked (state 1) legacy helmet waiting in the Postmaster.
            {
              'itemHash': 1001,
              'itemInstanceId': '12',
              'bucketHash': POSTMASTER_BUCKET_HASH,
              'state': 1,
            },
          ]
        }
      }
    },
    'characterEquipment': {'data': {}},
    'itemComponents': {
      'instances': {
        'data': {'11': {'gearTier': 5}, '12': {'gearTier': 0}, '13': {'gearTier': 5}}
      },
      'sockets': {
        'data': {
          '11': {
            'sockets': [
              {'plugHash': 2009},
              {'plugHash': 2004},
              {'plugHash': 2000},
              {'plugHash': 2001},
              {'plugHash': 2002},
              {'plugHash': 2003},
              {'plugHash': 2008},
            ]
          },
          '12': {
            'sockets': [
              {'plugHash': 2010},
              {'plugHash': 2011},
              {},
              {'plugHash': 2017},
              {'plugHash': 2018},
            ]
          },
          '13': {
            'sockets': [
              {'plugHash': 2012},
              {'plugHash': 2013},
              {'plugHash': 2014},
              {'plugHash': 2015},
              {'plugHash': 2005},
            ]
          },
        }
      },
      'reusablePlugs': {
        'data': {
          '11': {
            'plugs': {
              '6': [
                {'plugItemHash': 2008},
                {'plugItemHash': 2006},
                {'plugItemHash': 2007},
              ]
            }
          },
          '13': {
            'plugs': {
              '4': [
                {'plugItemHash': 2008},
                {'plugItemHash': 2006},
                {'plugItemHash': 2016},
              ]
            }
          },
        }
      },
    },
  }
