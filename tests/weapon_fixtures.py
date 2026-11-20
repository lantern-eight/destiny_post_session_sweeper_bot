'''Synthetic weapon definitions, a weapon profile, and a Weapon builder for tests.'''

from __future__ import annotations

from src.game_data import VAULT_BUCKET_HASH, WEAPON_PERKS_SOCKET_CATEGORY_HASH
from src.weapons import Weapon, normalize_weapon_name
from tests.fixtures import plug

ENERGY_BUCKET_HASH = 2465295065
TRACKER_CATEGORY = 'v400.plugs.weapons.masterworks.trackers'


def weapon_definition(
  name: str, *, adept: bool = False, shiny: bool = False, craftable: bool = False
) -> dict:
  inventory = {'bucketTypeHash': ENERGY_BUCKET_HASH, 'tierType': 5}
  if craftable:
    inventory['recipeItemHash'] = 7000
  return {
    'itemType': 3,
    'itemTypeDisplayName': 'Hand Cannon',
    'displayProperties': {'name': name},
    'inventory': inventory,
    'isAdept': adept,
    'isHolofoil': shiny,
    'sockets': {
      'socketCategories': [
        {
          'socketCategoryHash': WEAPON_PERKS_SOCKET_CATEGORY_HASH,
          'socketIndexes': [1, 2, 3, 4, 5],
        }
      ],
      'socketEntries': [
        {},  # intrinsic frame
        {'randomizedPlugSetHash': 9001},  # barrel
        {'randomizedPlugSetHash': 9002},  # magazine
        {'randomizedPlugSetHash': 9003},  # trait 1
        {'randomizedPlugSetHash': 9004},  # trait 2
        {},  # kill tracker
      ],
    },
  }


WEAPON_DEFINITIONS = {
  '3000': weapon_definition('Fatebringer (Adept)', adept=True),
  '3100': plug('Arrowhead Brake', 'barrels'),
  '3101': plug('Smallbore', 'barrels'),
  '3102': plug('Accurized Rounds', 'magazines'),
  '3103': plug('Ricochet Rounds', 'magazines'),
  '3104': plug('Explosive Payload', 'frames'),
  '3105': plug('Firefly', 'frames'),
  '3106': plug('Golden Tricorn Enhanced', 'frames'),
  '3107': plug('Opening Shot', 'frames'),
  '3108': plug('Kill Tracker', TRACKER_CATEGORY),
  '3109': plug('Empty Traits Socket', 'frames'),
}

# The crafting pools a crafted copy can reshape into, one per perk column.
PLUG_SET_DEFINITIONS = {
  '9001': {'reusablePlugItems': [{'plugItemHash': 3100}, {'plugItemHash': 3101}]},
  '9002': {'reusablePlugItems': [{'plugItemHash': 3102}, {'plugItemHash': 3103}]},
  '9003': {
    'reusablePlugItems': [
      {'plugItemHash': 3104},
      {'plugItemHash': 3106},
      {'plugItemHash': 3109},
    ]
  },
  '9004': {'reusablePlugItems': [{'plugItemHash': 3105}, {'plugItemHash': 3107}]},
}


def make_weapon_profile() -> dict:
  '''Two Adept Fatebringers: a T5 random roll ('21') and a crafted copy ('22').'''
  equipped_perks = [
    {},
    {'plugHash': 3100},
    {'plugHash': 3102},
    {'plugHash': 3104},
    {'plugHash': 3105},
    {'plugHash': 3108},
  ]
  return {
    'profileInventory': {
      'data': {
        'items': [
          {
            'itemHash': 3000,
            'itemInstanceId': '21',
            'bucketHash': VAULT_BUCKET_HASH,
            'state': 0,
          },
          {
            'itemHash': 3000,
            'itemInstanceId': '22',
            'bucketHash': VAULT_BUCKET_HASH,
            'state': 8,
          },
        ]
      }
    },
    'characterInventories': {'data': {}},
    'characterEquipment': {'data': {}},
    'itemComponents': {
      'instances': {'data': {'21': {'gearTier': 5}, '22': {'gearTier': 0}}},
      'sockets': {
        'data': {'21': {'sockets': equipped_perks}, '22': {'sockets': equipped_perks}}
      },
      'reusablePlugs': {
        'data': {
          '21': {
            'plugs': {
              '1': [{'plugItemHash': 3100}, {'plugItemHash': 3101}],
              '2': [{'plugItemHash': 3102}],
              '3': [{'plugItemHash': 3104}, {'plugItemHash': 3106}],
              '4': [{'plugItemHash': 3105}],
            }
          }
        }
      },
    },
  }


def make_weapon(
  instance_id: str,
  columns: list[list[str]],
  *,
  name: str = 'Fatebringer',
  rarity: str = 'legendary',
  tier: int = 0,
  shiny: bool = False,
  adept: bool = False,
  crafted: bool = False,
  craftable: bool = False,
  pair: tuple[str, ...] = (),
  masterworked: bool = False,
  locked: bool = False,
  masterwork_options: tuple[str, ...] = (),
) -> Weapon:
  return Weapon(
    instance_id=instance_id,
    name=name,
    group_name=normalize_weapon_name(name),
    weapon_type='Hand Cannon',
    slot='Energy',
    rarity=rarity,
    tier=tier,
    is_shiny=shiny,
    is_adept=adept,
    is_crafted=crafted,
    is_masterworked=masterworked,
    is_locked=locked,
    perk_columns=tuple(tuple(column) for column in columns),
    selected_perks=tuple(column[0] for column in columns),
    location='vault',
    is_craftable=craftable,
    exotic_pair=pair,
    masterwork=masterwork_options[0] if masterwork_options else '',
    masterwork_options=masterwork_options,
  )
