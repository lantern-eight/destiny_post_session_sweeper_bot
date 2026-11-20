'''Destiny 2 constants the rules rely on. Hashes are stable across manifests.'''

from __future__ import annotations

STAT_NAMES_BY_HASH = {
  2996146975: 'Weapons',
  392767087: 'Health',
  1943323491: 'Class',
  1735777505: 'Grenade',
  144602215: 'Super',
  4244567218: 'Melee',
}
STAT_NAMES = tuple(STAT_NAMES_BY_HASH.values())
# The order the game lists armor stats in. Everything the tool prints uses it,
# so the plan reads like the game when you compare pieces.
STAT_DISPLAY_ORDER = ('Health', 'Melee', 'Grenade', 'Super', 'Class', 'Weapons')

# Armor 3.0 archetypes as (primary stat, secondary stat). Verified against all
# 53 new-system pieces in the user's inventory on 2026-09-27.
ARCHETYPE_STATS = {
  'Gunner': ('Weapons', 'Grenade'),
  'Powerhouse': ('Weapons', 'Super'),
  'Bulwark': ('Health', 'Class'),
  'Siegebreaker': ('Health', 'Grenade'),
  'Specialist': ('Class', 'Weapons'),
  'Reaver': ('Class', 'Melee'),
  'Grenadier': ('Grenade', 'Super'),
  'Demolitionist': ('Grenade', 'Class'),
  'Paragon': ('Super', 'Melee'),
  'Colossus': ('Super', 'Health'),
  'Brawler': ('Melee', 'Health'),
  'Skirmisher': ('Melee', 'Weapons'),
}

CLASS_NAMES = {0: 'Titan', 1: 'Hunter', 2: 'Warlock'}

ARMOR_SLOTS_BY_BUCKET_HASH = {
  3448274439: 'Helmet',
  3551918588: 'Gauntlets',
  14239492: 'Chest',
  20886954: 'Legs',
  1585787867: 'Class Item',
}

VAULT_BUCKET_HASH = 138197802
POSTMASTER_BUCKET_HASH = 215593132
# Each character bucket holds 10 items: 1 equipped plus 9 unequipped.
CHARACTER_BUCKET_CAPACITY = 9

WEAPON_SLOTS_BY_BUCKET_HASH = {
  1498876634: 'Kinetic',
  2465295065: 'Energy',
  953998645: 'Power',
}
# Weapons first, then armor, in the game's order.
SLOTS_BY_BUCKET_HASH = {**WEAPON_SLOTS_BY_BUCKET_HASH, **ARMOR_SLOTS_BY_BUCKET_HASH}

# DestinyItemComponent.transferStatus bit flags.
TRANSFER_STATUS_NOT_TRANSFERRABLE = 2
# The socket category holding barrel, magazine, both traits, origin, and the
# kill tracker. The tracker is filtered out by its plug category.
WEAPON_PERKS_SOCKET_CATEGORY_HASH = 4241085061
INTRINSIC_TRAITS_SOCKET_CATEGORY_HASH = 3956125808
FRAME_PLUG_CATEGORY = 'frames'
# Exotic weapons that roll a random frame and a random exotic trait (in the
# intrinsic socket). Like exotic class items and their perk pairs, every
# frame + trait pair keeps a copy.
PAIRED_EXOTIC_WEAPONS = frozenset({'Ergo Sum'})
NON_PERK_PLUG_CATEGORY_FRAGMENTS = ('tracker', 'memento')
# Plug categories of trait perks (both trait columns) and origin traits. The tie
# step groups Voltron's rolls by these.
TRAIT_PLUG_CATEGORY = 'frames'
ORIGIN_TRAIT_PLUG_CATEGORY = 'origins'
TRAIT_PLUG_CATEGORIES = (TRAIT_PLUG_CATEGORY, ORIGIN_TRAIT_PLUG_CATEGORY)
# The primary columns of your own wishlist rolls: a copy must have what a roll
# lists from them. Everything else a roll lists (origin trait, barrel,
# magazine, and the like) is secondary: optional, and the copy with the most of
# it wins among those with the primary perks.
PRIMARY_PERK_PLUG_CATEGORIES = (TRAIT_PLUG_CATEGORY,)
# The columns a wishlist roll lists its perks under, named as the game names
# them, and the plug categories each one covers. The traits are perk_1,
# perk_2, and so on.
TRAIT_COLUMN_PREFIX = 'perk_'
COLUMN_PLUG_CATEGORIES = {
  'origin_trait': (ORIGIN_TRAIT_PLUG_CATEGORY,),
  'barrel': ('barrels',),
  'launcher_barrel': ('tubes',),
  'scope': ('scopes',),
  'sight': ('scopes',),
  'magazine': ('magazines', 'magazines_gl'),
  'battery': ('batteries',),
  'blade': ('blades',),
  'guard': ('guards',),
  'bowstring': ('bowstrings',),
  'arrow': ('arrows',),
  'haft': ('hafts',),
  'stock': ('stocks',),
  'grip': ('grips',),
  'rail': ('rails',),
  'bolt': ('bolts',),
}
# The column for the masterwork stat a roll wants.
MASTERWORK_COLUMN = 'masterwork'
# Masterwork plugs are named "Tier 3: Stability" or "Masterworked: Handling",
# in categories such as v400.plugs.weapons.masterworks.stat.stability (the kill
# tracker's category shares the prefix). A random roll's socket offers only
# higher tiers of its stat; a crafted or enhanced copy holds an unnamed plug
# and is offered every stat.
MASTERWORK_PLUG_CATEGORY_FRAGMENT = '.plugs.weapons.masterworks'
EMPTY_PLUG_PREFIX = 'Empty '
# The modes a roll can be for, in Voltron's notes and your own wishlist.
MODE_NAMES = {'pve': 'PvE', 'pvp': 'PvP'}
# Name suffixes for versions of the same weapon, folded into one weapon.
WEAPON_VARIANT_SUFFIXES = ('Adept', 'Timelost', 'Harrowed', 'Baroque')
ENHANCED_PERK_SUFFIX = ' Enhanced'

ITEM_TYPE_ARMOR = 2
ITEM_TYPE_WEAPON = 3
# DestinyInventoryItemDefinition.inventory.tierType. Below legendary, rarities
# go by color: the game calls white "Common" while the API enum calls it Basic.
RARITIES_BY_TIER_TYPE = {
  2: 'white',
  3: 'green',
  4: 'blue',
  5: 'legendary',
  6: 'exotic',
}
# White weapons are quest items and keepsakes and are always kept; everything
# else below legendary is a shard unless you lock it.
LOW_RARITIES = ('white', 'green', 'blue')

ITEM_STATE_LOCKED = 1
ITEM_STATE_MASTERWORKED = 4
ITEM_STATE_CRAFTED = 8
# Enhanced weapons (enhanced at the Enclave, from Into the Light on) carry the
# crafted bit too, but keep their own perk options: only a crafted (shaped)
# weapon can be reshaped. An enhanced one has a plug of this category, such as
# "Enhancement 1"; a weapon that could be enhanced holds an empty one.
ENHANCER_PLUG_CATEGORY = 'crafting.plugs.weapons.mods.enhancers'

# Plugs whose investment stats add up to an armor piece's base (rolled) stats:
# `armor_stats` on new-system armor, `intrinsics` on legacy armor and on the
# exotic class item perks. Masterwork, mods, and tuning are deliberately left out.
BASE_STAT_PLUG_CATEGORIES = ('armor_stats', 'intrinsics')
ARCHETYPE_PLUG_CATEGORY = 'armor_archetypes'
# Armor perks such as Iron Banner's Iron Lord's Pride. Legacy stat plugs share
# the category but have no name.
INTRINSIC_PLUG_CATEGORY = 'intrinsics'
# Raid armor has a mod socket that only its raid's mods fit. Last Wish's came
# before the raid naming: its mods arrived with Season of the Outlaw.
RAID_MOD_PLUG_CATEGORY_PREFIX = 'enhancements.raid'
LAST_WISH_MOD_PLUG_CATEGORY = 'enhancements.season_outlaw'
TUNING_PLUG_CATEGORY = 'core.gear_systems.armor_tiering.plugs.tuning.mods'
EXOTIC_CLASS_ITEM_PERK_PREFIX = 'Spirit of '
TUNING_BONUS = 5

# Bungie's "nothing here" hash, used for unset loadout names and plugs.
UNSET_HASH = 2166136261
# Plugs listed as a loadout's mods: armor mods (including artifice and raid
# mods), armor tuning, and weapon mods. Shaders, ornaments, and perks are not.
MOD_PLUG_CATEGORY_PREFIXES = ('enhancements.', 'v400.weapon.mod_', TUNING_PLUG_CATEGORY)
