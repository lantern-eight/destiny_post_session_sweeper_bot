'''Tests for apply, next, and done against a fake account that enforces the rules.'''

from __future__ import annotations

from collections import Counter
from datetime import timedelta

import pytest
import requests

from src.account import Account, DryRunActions
from src.bungie_api import BungieApiError
from src.game_data import VAULT_BUCKET_HASH
from src.reports import format_finish_report, format_round_report
from src.session import (
  SessionError,
  finish_session,
  is_stale,
  run_round,
  start_session,
)
from src.state import Session, StateFile, load_state
from tests.account_fixtures import (
  DEFINITIONS,
  ENERGY_GUN,
  HUNTER,
  HUNTER_MASK,
  KINETIC_GUN,
  NOW,
  WARLOCK,
  WARLOCK_HOOD,
  FakeBungie,
  FakeItem,
  make_plan,
  open_account,
)

# The Warlock was played last, so the session runs there.
ACTIONS = {
  '11': 'keep',  # carried and locked: comes back at done
  '12': 'keep',  # carried and unlocked: a new keeper, goes to the vault for good
  '13': 'shard',  # carried and unlocked: a new shard, stays for round 1
  '14': 'shard',  # equipped: never moves
  '21': 'shard',  # in the vault
  '22': 'shard',  # Warlock armor in the vault
  '23': 'shard',  # Hunter armor: waits for a Hunter session
  '24': 'shard',  # your lock, placed after planning: kept
  '31': 'shard',  # a weapon shard on the Hunter: comes over through the vault
  '32': 'keep',  # Hunter armor you locked, on the Hunter
}


def make_world() -> FakeBungie:
  return FakeBungie(
    [
      FakeItem('11', KINETIC_GUN, 'inventory', WARLOCK, locked=True),
      FakeItem('12', KINETIC_GUN, 'inventory', WARLOCK),
      FakeItem('13', KINETIC_GUN, 'inventory', WARLOCK),
      FakeItem('14', ENERGY_GUN, 'equipped', WARLOCK),
      FakeItem('21', KINETIC_GUN),
      FakeItem('22', WARLOCK_HOOD),
      FakeItem('23', HUNTER_MASK),
      FakeItem('24', ENERGY_GUN, locked=True),
      FakeItem('31', ENERGY_GUN, 'inventory', HUNTER),
      FakeItem('32', HUNTER_MASK, 'inventory', HUNTER, locked=True),
    ]
  )


@pytest.fixture
def state_path(tmp_path):
  return tmp_path / 'state.json'


def start(fake: FakeBungie, state_file: StateFile, actions=ACTIONS):
  return start_session(open_account(fake), make_plan(actions), state_file, NOW)


def test_apply_locks_new_keepers_and_leaves_your_locks_alone(state_path):
  fake = make_world()
  report = start(fake, StateFile(state_path))
  assert (report.locked, report.unlocked) == (1, [])
  assert fake.items['12'].locked
  assert fake.items['24'].locked  # your lock beats the plan's shard
  assert load_state(state_path).tool_locked_ids == {'12'}


def test_apply_clears_keepers_out_and_stages_this_class_shards(state_path):
  fake = make_world()
  report = start(fake, StateFile(state_path))
  assert fake.where('11') == ('vault', None)
  assert fake.where('12') == ('vault', None)
  for shard in ('13', '21', '22', '31'):
    assert fake.where(shard) == ('inventory', WARLOCK)
  assert fake.where('23') == ('vault', None)
  assert fake.where('24') == ('vault', None)
  assert fake.where('32') == ('inventory', HUNTER)
  assert fake.where('14') == ('equipped', WARLOCK)
  assert '14' not in fake.moved_ids()
  assert report.waiting == Counter({'Kinetic': 2, 'Energy': 1, 'Helmet': 1})
  assert report.other_classes == Counter({'Hunter': 1})
  assert report.equipped_shards == ['Energy Gun (Warlock)']
  session = load_state(state_path).session
  assert session.carried == ['11']
  assert sorted(session.staged) == ['13', '21', '22', '31']


def test_next_counts_what_you_dismantled_and_done_puts_gear_back(state_path):
  fake = make_world()
  state_file = StateFile(state_path)
  start(fake, state_file)
  fake.dismantle('13', '21', '22', '31')
  report = run_round(open_account(fake), make_plan(ACTIONS), state_file)
  assert report.dismantled == 4
  assert not report.waiting
  assert not report.still_to_stage
  finish = finish_session(open_account(fake), make_plan(ACTIONS), state_file)
  assert finish.restored == 1
  assert fake.where('11') == ('inventory', WARLOCK)
  assert fake.where('12') == ('vault', None)  # new keepers stay in the vault
  saved = load_state(state_path)
  assert saved.session is None
  assert saved.tool_locked_ids == {'12'}


def test_rounds_fill_each_slot_up_to_nine(state_path):
  shard_ids = [str(100 + number) for number in range(12)]
  fake = FakeBungie([FakeItem(shard_id, KINETIC_GUN) for shard_id in shard_ids])
  plan = make_plan(dict.fromkeys(shard_ids, 'shard'))
  state_file = StateFile(state_path)
  report = start_session(open_account(fake), plan, state_file, NOW)
  assert report.staged_now == Counter({'Kinetic': 9})
  assert report.still_to_stage == Counter({'Kinetic': 3})
  fake.dismantle(*state_file.state.session.staged)
  report = run_round(open_account(fake), plan, state_file)
  assert report.dismantled == 9
  assert report.staged_now == Counter({'Kinetic': 3})
  assert not report.still_to_stage


def test_a_staged_shard_you_lock_is_kept_and_moved_out(state_path):
  fake = make_world()
  state_file = StateFile(state_path)
  start(fake, state_file)
  fake.items['21'].locked = True  # you lock it in-game instead of dismantling
  report = run_round(open_account(fake), make_plan(ACTIONS), state_file)
  assert report.kept_by_you == ['Kinetic Gun']
  assert fake.where('21') == ('vault', None)
  assert '21' not in state_file.state.tool_locked_ids


def test_done_makes_room_for_what_you_were_carrying(state_path):
  shard_ids = [str(100 + number) for number in range(9)]
  fake = FakeBungie(
    [
      FakeItem('11', KINETIC_GUN, 'inventory', WARLOCK, locked=True),
      *(FakeItem(shard_id, KINETIC_GUN) for shard_id in shard_ids),
    ]
  )
  plan = make_plan({'11': 'keep', **dict.fromkeys(shard_ids, 'shard')})
  state_file = StateFile(state_path)
  start_session(open_account(fake), plan, state_file, NOW)
  finish = finish_session(open_account(fake), plan, state_file)
  assert (finish.restored, finish.made_room, finish.shards_left) == (1, 1, 8)
  assert fake.where('11') == ('inventory', WARLOCK)


def test_the_tool_unlocks_only_its_own_locks(state_path):
  fake = FakeBungie(
    [FakeItem('51', KINETIC_GUN, locked=True), FakeItem('52', KINETIC_GUN, locked=True)]
  )
  state_file = StateFile(state_path)
  state_file.state.tool_locked_ids = {'51'}
  report = start_session(
    open_account(fake), make_plan({'51': 'shard', '52': 'shard'}), state_file, NOW
  )
  assert report.unlocked == ['Kinetic Gun']
  assert not fake.items['51'].locked
  assert fake.items['52'].locked
  assert fake.where('51') == ('inventory', WARLOCK)
  assert fake.where('52') == ('vault', None)


def test_removing_the_lock_that_kept_an_item_makes_it_a_shard(state_path):
  fake = FakeBungie([FakeItem('61', KINETIC_GUN)])
  plan = make_plan({'61': 'keep'}, kept_for_your_lock=('61',))
  start_session(open_account(fake), plan, StateFile(state_path), NOW)
  assert not fake.items['61'].locked
  assert fake.where('61') == ('inventory', WARLOCK)


def test_everything_in_the_postmaster_goes_to_the_vault_first(state_path):
  fake = FakeBungie(
    [
      FakeItem('41', KINETIC_GUN, 'postmaster', WARLOCK),
      FakeItem('42', ENERGY_GUN, 'postmaster', WARLOCK),
      FakeItem('43', ENERGY_GUN, 'postmaster', WARLOCK),  # dropped after planning
      FakeItem('44', HUNTER_MASK, 'postmaster', WARLOCK),  # can't come to a Warlock
    ]
  )
  state_file = StateFile(state_path)
  report = start_session(
    open_account(fake), make_plan({'41': 'keep', '42': 'shard'}), state_file, NOW
  )
  assert report.pulled == 3
  for item_id in ('41', '42', '43'):
    first_two = [call[0] for call in fake.calls if call[1] == item_id][:2]
    assert first_two == ['pull', 'to_vault']  # straight on to the vault
  assert fake.where('41') == ('vault', None)
  assert fake.items['41'].locked  # a keeper, locked like any other
  assert fake.where('42') == ('inventory', WARLOCK)  # a shard, back for round 1
  assert fake.where('43') == ('vault', None)  # not in the plan: just kept safe
  assert not fake.items['43'].locked
  assert fake.where('44') == ('postmaster', WARLOCK)
  assert state_file.state.tool_locked_ids == {'41'}


def test_an_interrupted_run_picks_up_where_it_stopped(state_path):
  fake = make_world()
  fake.failures['21'] = 'SystemDisabled'  # Bungie goes down mid-run
  with pytest.raises(BungieApiError):
    start(fake, StateFile(state_path))
  saved = load_state(state_path)
  assert saved.session.carried == ['11']
  assert saved.tool_locked_ids == {'12'}

  resumed = StateFile(state_path)
  run_round(open_account(fake), make_plan(ACTIONS), resumed)
  assert fake.where('21') == ('inventory', WARLOCK)
  finish_session(open_account(fake), make_plan(ACTIONS), resumed)
  assert fake.where('11') == ('inventory', WARLOCK)


def test_one_item_failing_does_not_stop_the_round(state_path):
  fake = make_world()
  fake.failures['21'] = 'DestinyItemNotFound'
  report = start(fake, StateFile(state_path))
  assert any('Kinetic Gun' in problem for problem in report.problems)
  assert fake.where('22') == ('inventory', WARLOCK)


def test_apply_refuses_an_old_plan(state_path):
  state_file = StateFile(state_path)
  old_plan = make_plan(ACTIONS, generated=NOW - timedelta(hours=3))
  with pytest.raises(SessionError, match='3.0 hours old'):
    start_session(open_account(make_world()), old_plan, state_file, NOW)
  assert state_file.state.session is None


def test_old_sessions_and_other_characters_are_stale():
  fake = make_world()
  started = NOW.isoformat()
  session = Session(WARLOCK, 'Warlock', started=started, plan='plan.yaml')
  assert not is_stale(session, open_account(fake), NOW + timedelta(minutes=30))
  assert is_stale(session, open_account(fake), NOW + timedelta(hours=13))
  fake.latest_character = HUNTER
  assert is_stale(session, open_account(fake), NOW + timedelta(minutes=30))


def test_dry_run_changes_nothing(state_path):
  fake = make_world()
  actions = DryRunActions()
  account = Account.from_profile(fake.profile(), DEFINITIONS, actions, 3)
  report = start_session(account, make_plan(ACTIONS), StateFile(state_path, True), NOW)
  assert fake.calls == []
  assert not state_path.exists()
  assert report.staged_now == Counter({'Kinetic': 1, 'Energy': 1, 'Helmet': 1})
  assert len(actions.calls) == 7  # 1 lock, 2 moves out, 3 stagings (one via the vault)


def test_tool_locks_on_dismantled_items_are_forgotten(state_path):
  fake = FakeBungie([FakeItem('51', KINETIC_GUN, locked=True)])
  state_file = StateFile(state_path)
  state_file.state.tool_locked_ids = {'51', '99'}  # 99 was dismantled
  start_session(open_account(fake), make_plan({'51': 'keep'}), state_file, NOW)
  assert load_state(state_path).tool_locked_ids == {'51'}


def test_tool_locks_on_items_the_tool_does_not_track_are_kept(state_path):
  fake = FakeBungie([FakeItem('51', KINETIC_GUN, locked=True)])
  profile = fake.profile()
  # An item the cached manifest does not know yet: not dismantled, just untracked.
  profile['profileInventory']['data']['items'].append(
    {'itemHash': 999, 'itemInstanceId': '98', 'bucketHash': VAULT_BUCKET_HASH}
  )
  account = Account.from_profile(profile, DEFINITIONS, fake, 3)
  state_file = StateFile(state_path)
  state_file.state.tool_locked_ids = {'51', '98', '99'}  # 99 was dismantled
  start_session(account, make_plan({'51': 'keep'}), state_file, NOW)
  assert load_state(state_path).tool_locked_ids == {'51', '98'}


def test_done_counts_only_tie_copies_it_actually_moved(state_path):
  fake = FakeBungie(
    [FakeItem('71', ENERGY_GUN), FakeItem('72', ENERGY_GUN, 'inventory', HUNTER)]
  )
  plan = make_plan({'71': 'keep', '72': 'keep'})
  state_file = StateFile(state_path)
  start_session(open_account(fake), plan, state_file, NOW)
  state_file.state.session.tie_origins = {'71': 'vault', '72': HUNTER}
  finish = finish_session(open_account(fake), plan, state_file)
  assert finish.returned == 0  # both are already where they came from


def test_rounds_flag_staged_shards_that_a_loadout_uses(state_path):
  fake = FakeBungie([FakeItem('21', KINETIC_GUN), FakeItem('22', KINETIC_GUN)])
  plan = make_plan({'21': 'shard', '22': 'shard'})
  plan.loadouts_by_item['21'] = ['Warlock loadout 2 (PvE)']
  report = start_session(open_account(fake), plan, StateFile(state_path), NOW)
  assert report.in_loadouts == ['Kinetic Gun: Warlock loadout 2 (PvE)']


def test_a_pull_that_cannot_reach_the_vault_is_not_counted(state_path):
  fake = FakeBungie([FakeItem('41', KINETIC_GUN, 'postmaster', WARLOCK, movable=False)])
  report = start_session(
    open_account(fake), make_plan({'41': 'keep'}), StateFile(state_path), NOW
  )
  assert report.pulled == 0
  assert report.problems == [
    'could not move Kinetic Gun (Kinetic): it cannot be moved; it is in your '
    'inventory now'
  ]
  assert fake.where('41') == ('inventory', WARLOCK)
  assert fake.items['41'].locked  # a keeper, locked where it is


def test_locked_drops_the_plan_does_not_cover_come_back_at_done(state_path):
  fake = FakeBungie([FakeItem('15', KINETIC_GUN, 'inventory', WARLOCK, locked=True)])
  state_file = StateFile(state_path)
  start_session(open_account(fake), make_plan({}), state_file, NOW)
  assert state_file.state.session.carried == ['15']
  assert fake.where('15') == ('inventory', WARLOCK)  # not in the plan: left alone
  # The tie step plans again, and the new plan covers it.
  later_plan = make_plan({'15': 'keep'}, name='20260927T203000Z-plan.yaml')
  run_round(open_account(fake), later_plan, state_file)
  assert fake.where('15') == ('vault', None)
  finish_session(open_account(fake), later_plan, state_file)
  assert fake.where('15') == ('inventory', WARLOCK)


def without_warlock(profile: dict) -> dict:
  '''The profile Bungie sends after the Warlock is deleted.'''
  for part in ('characters', 'characterInventories', 'characterEquipment'):
    del profile[part]['data'][WARLOCK]
  return profile


def test_a_deleted_character_ends_the_session_cleanly(state_path):
  fake = FakeBungie([FakeItem('11', KINETIC_GUN, 'inventory', WARLOCK, locked=True)])
  state_file = StateFile(state_path)
  start_session(open_account(fake), make_plan({'11': 'keep'}), state_file, NOW)
  account = Account.from_profile(without_warlock(fake.profile()), DEFINITIONS, fake, 3)
  with pytest.raises(SessionError, match='no longer exists'):
    run_round(account, make_plan({'11': 'keep'}), state_file)
  finish = finish_session(account, make_plan({'11': 'keep'}), state_file)
  assert finish.character_gone
  assert fake.where('11') == ('vault', None)
  assert load_state(state_path).session is None


def test_shards_the_tool_cannot_reach_are_reported(state_path):
  fake = FakeBungie(
    [
      FakeItem('35', ENERGY_GUN, 'postmaster', HUNTER),
      FakeItem('36', KINETIC_GUN, movable=False),
    ]
  )
  report = start_session(
    open_account(fake),
    make_plan({'35': 'shard', '36': 'shard'}),
    StateFile(state_path),
    NOW,
  )
  assert report.stuck_shards == [
    'Kinetic Gun (in the vault)',
    "Energy Gun (in your Hunter's Postmaster)",
  ]
  assert 'cannot bring to you' in format_round_report(report)


def test_a_lock_whose_reply_is_lost_still_counts_as_the_tools(state_path):
  fake = FakeBungie([FakeItem('12', KINETIC_GUN)])
  fake.lost_replies.add('12')
  state_file = StateFile(state_path)
  with pytest.raises(requests.ConnectionError):
    start_session(open_account(fake), make_plan({'12': 'keep'}), state_file, NOW)
  assert fake.items['12'].locked  # Bungie did lock it
  assert load_state(state_path).pending_lock_ids == {'12'}

  # A better copy dropped: 12 is a shard now, and the tool may unlock its lock.
  report = run_round(open_account(fake), make_plan({'12': 'shard'}), state_file)
  assert report.unlocked == ['Kinetic Gun']
  assert not fake.items['12'].locked
  saved = load_state(state_path)
  assert (saved.pending_lock_ids, saved.tool_locked_ids) == (set(), set())


def test_done_gives_the_real_reason_something_stayed_out(state_path):
  fake = FakeBungie([FakeItem('11', KINETIC_GUN, 'inventory', WARLOCK, locked=True)])
  state_file = StateFile(state_path)
  start_session(open_account(fake), make_plan({'11': 'keep'}), state_file, NOW)
  fake.failures['11'] = 'DestinyItemNotFound'
  finish = finish_session(open_account(fake), make_plan({'11': 'keep'}), state_file)
  assert finish.not_restored == [
    'Kinetic Gun (Kinetic), in the vault: it is not where Bungie last saw it '
    '(moved or dismantled?)'
  ]
  assert 'Could not put these back' in format_finish_report(finish)


def test_a_tie_copy_whose_home_is_full_goes_to_the_vault(state_path):
  hunter_items = [
    FakeItem(str(300 + number), ENERGY_GUN, 'inventory', HUNTER) for number in range(9)
  ]
  fake = FakeBungie([FakeItem('72', ENERGY_GUN, 'inventory', WARLOCK), *hunter_items])
  plan = make_plan({'72': 'keep'})
  state_file = StateFile(state_path)
  start_session(open_account(fake), plan, state_file, NOW)
  state_file.state.session.tie_origins = {'72': HUNTER}
  finish = finish_session(open_account(fake), plan, state_file)
  assert finish.returned == 0
  assert finish.problems == [
    'could not move Energy Gun (Energy): no room in your Hunter Energy, so it went '
    'to the vault instead'
  ]
  assert fake.where('72') == ('vault', None)


def test_a_staged_shard_a_newer_plan_keeps_is_not_called_yours(state_path):
  fake = FakeBungie([FakeItem('21', KINETIC_GUN)])
  state_file = StateFile(state_path)
  start_session(open_account(fake), make_plan({'21': 'shard'}), state_file, NOW)
  newer_plan = make_plan({'21': 'keep'}, name='20260927T203000Z-plan.yaml')
  report = run_round(open_account(fake), newer_plan, state_file)
  assert report.kept_by_new_plan == ['Kinetic Gun']
  assert report.kept_by_you == []
  assert fake.where('21') == ('vault', None)


def test_a_keeper_you_unlock_is_flagged_when_a_loadout_uses_it(state_path):
  fake = FakeBungie([FakeItem('61', KINETIC_GUN)])
  plan = make_plan({'61': 'keep'}, kept_for_your_lock=('61',))
  plan.loadouts_by_item['61'] = ['Warlock loadout 1 (PvE)']
  report = start_session(open_account(fake), plan, StateFile(state_path), NOW)
  assert report.in_loadouts == ['Kinetic Gun: Warlock loadout 1 (PvE)']


def test_kept_copies_come_in_next_to_their_shards_to_compare(state_path):
  fake = FakeBungie(
    [
      FakeItem('41', KINETIC_GUN),
      FakeItem('42', KINETIC_GUN),
      FakeItem('43', KINETIC_GUN),  # the copy the plan keeps
    ]
  )
  plan = make_plan(
    {'41': 'shard', '42': 'shard', '43': 'keep'},
    compare_with={'41': ('43',), '42': ('43',)},
  )
  state_file = StateFile(state_path)
  report = start_session(open_account(fake), plan, state_file, NOW)
  assert fake.where('43') == ('inventory', WARLOCK)
  assert fake.items['43'].locked  # so it can't be dismantled by mistake
  assert report.staged_now == Counter({'Kinetic': 2})
  assert report.comparing == ['Kinetic Gun']
  assert 'Kept copies next to them, to compare' in format_round_report(report)
  assert load_state(state_path).session.compared == ['43']

  # While a shard of its weapon is left, it stays next to it.
  fake.dismantle('41')
  run_round(open_account(fake), plan, state_file)
  assert fake.where('43') == ('inventory', WARLOCK)

  # Once they are gone, it goes back to the vault.
  fake.dismantle('42')
  report = run_round(open_account(fake), plan, state_file)
  assert fake.where('43') == ('vault', None)
  assert report.comparing == []
  assert state_file.state.session.compared == []


def test_shards_already_waiting_get_their_kept_copy_too(state_path):
  # Staged before kept copies came along (or carried in): it still gets one.
  fake = FakeBungie(
    [FakeItem('41', KINETIC_GUN, 'inventory', WARLOCK), FakeItem('43', KINETIC_GUN)]
  )
  plan = make_plan({'41': 'shard', '43': 'keep'}, compare_with={'41': ('43',)})
  report = start_session(open_account(fake), plan, StateFile(state_path), NOW)
  assert fake.where('43') == ('inventory', WARLOCK)
  assert report.comparing == ['Kinetic Gun']


def test_at_most_two_kept_copies_of_a_weapon_come_in(state_path):
  # Ergo Sum style: each shard is compared against a different kept copy.
  shard_ids, keeper_ids = ['51', '52', '53'], ['61', '62', '63']
  fake = FakeBungie(
    [FakeItem(item_id, KINETIC_GUN) for item_id in shard_ids + keeper_ids]
  )
  plan = make_plan(
    {**dict.fromkeys(shard_ids, 'shard'), **dict.fromkeys(keeper_ids, 'keep')},
    compare_with={
      shard_id: (keeper_id,)
      for shard_id, keeper_id in zip(shard_ids, keeper_ids, strict=True)
    },
  )
  report = start_session(open_account(fake), plan, StateFile(state_path), NOW)
  assert report.staged_now == Counter({'Kinetic': 3})
  assert report.comparing == ['Kinetic Gun', 'Kinetic Gun']
  assert fake.where('63') == ('vault', None)


def test_a_shard_waits_when_its_kept_copy_does_not_fit(state_path):
  shard_ids = [str(70 + number) for number in range(9)]
  fake = FakeBungie(
    [
      *(FakeItem(item_id, KINETIC_GUN) for item_id in shard_ids),
      FakeItem('80', KINETIC_GUN),
    ]
  )
  plan = make_plan(
    {**dict.fromkeys(shard_ids, 'shard'), '80': 'keep'}, compare_with={'78': ('80',)}
  )
  state_file = StateFile(state_path)
  report = start_session(open_account(fake), plan, state_file, NOW)
  assert report.staged_now == Counter({'Kinetic': 8})
  assert fake.where('78') == ('vault', None)
  assert fake.where('80') == ('vault', None)  # not brought in without its shard
  assert report.problems == []

  fake.dismantle(*shard_ids[:8])
  run_round(open_account(fake), plan, state_file)
  assert fake.where('78') == ('inventory', WARLOCK)
  assert fake.where('80') == ('inventory', WARLOCK)


def test_done_sends_compared_copies_back_to_the_vault(state_path):
  fake = FakeBungie([FakeItem('41', KINETIC_GUN), FakeItem('43', KINETIC_GUN)])
  plan = make_plan({'41': 'shard', '43': 'keep'}, compare_with={'41': ('43',)})
  state_file = StateFile(state_path)
  start_session(open_account(fake), plan, state_file, NOW)
  report = finish_session(open_account(fake), plan, state_file)
  assert fake.where('43') == ('vault', None)
  assert report.returned_compared == 1
  assert 'Returned 1 kept copy you compared against to the vault.' in (
    format_finish_report(report)
  )


def test_done_counts_the_shards_left_for_another_session(state_path):
  shard_ids = [str(100 + number) for number in range(12)]
  fake = FakeBungie([FakeItem(shard_id, KINETIC_GUN) for shard_id in shard_ids])
  plan = make_plan(dict.fromkeys(shard_ids, 'shard'))
  state_file = StateFile(state_path)
  start_session(open_account(fake), plan, state_file, NOW)
  finish = finish_session(open_account(fake), plan, state_file)
  assert (finish.shards_left, finish.shards_elsewhere) == (9, 3)
  assert '3 more shards wait in the vault' in format_finish_report(finish)
