---
name: sweeper
description: Run the Destiny 2 post-session sweeper for the player by conversation, so they never touch the CLI. Use when they say they're done playing, want to clean up or sort their vault, decide what to keep or shard, start a sweep, say they've dismantled the staged items ("done dismantling", "next round"), want to answer a tie, want their gear put back, ask why an item is being kept or sharded, or want to add a roll to their wishlist.
---

# Post-session sweeper

The player talks; you run `uv run sweeper <command>` and tell them what
happened in plain language. They should never need to type a command.

Run every command from the sweeper's checkout: the folder with its
`README.md`, `pyproject.toml`, `config.yaml`, and `wishlist.yaml`. If the
conversation isn't in it, ask where it is. Read its `README.md` ("End of a
session", "How each item is decided", and the flowcharts) when you need the
rules in detail.

## Running commands

- Run one command at a time; the tool refuses a second one anyway.
- `apply`, `next`, `ties`, and `done` can take several minutes (the first
  `apply` locks every keeper). Give them a 600000 ms timeout, or run them in
  the background and wait for the notice.
- Set `NO_COLOR=1` so the output has no color codes.
- Output names gear, never secrets. Never print or read `.env` or
  `.bungie_token_cache`.
- If a command stops partway (Bungie down, a timeout), run the same command
  again: it picks up where it stopped.
- If it says sign-in is needed, run `uv run sweeper login` (it opens their
  browser) and tell them to finish signing in there, then retry.

## The session, as a conversation

### 1. "I'm done playing" → plan

Run `plan`. Summarize the output for them briefly: how many weapons and armor
pieces are kept and sharded, then anything that needs them:

- `warnings`
- `locked_but_outdated`: items they locked that the rules would shard
- `loadouts_affected`: in-game loadouts that use a shard (name the loadout
  and the pieces going away)
- `ties`: weapons that will need a pick later
- items marked review

The full plan is the newest `data/runs/*-plan.yaml` (not `what-if/`). Read it
to answer questions; each entry under `items` has `name`, `action`, and
`reasons`. Never quote account names or item IDs from it back into any
committed file.

Before `apply`, remind them once: anything they're carrying and want back
afterwards must be locked in-game now, and they shouldn't lock or unlock in
game while a command runs. Then ask whether to go ahead. `apply` only starts
from a plan under 2 hours old; if it's older, run `plan` again first.

### 2. "Go" → apply

Run `apply`. Tell them how many shards are staged and on which character, and
anything listed as stuck, equipped, or for another class. Then: "Dismantle
those in-game and tell me when you're done."

If they want to preview first, use `--dry-run` on any of `apply`, `next`,
`ties`, or `done`.

### 3. "Done dismantling" → next

Run `next` and report the same way. Repeat on each "done" until it says no
shards are left. If they decide to keep something that was staged, tell them
to lock it in-game before saying done; it moves to the vault next round.

### 4. Ties

When no shards are left and the output mentions tied weapons, run `ties`. It
brings the copies in and, since there's no terminal prompt, prints the
lettered copies and the numbered trait pairs, then stops. Show them the
weapon, the copies, and the pairs in a readable list, and ask which pairs they
want. Map their answer ("the Chill Clip ones", "2 and 18", "skip it") to:

- `uv run sweeper ties --pick 2,18`
- `uv run sweeper ties --skip`

The numbers stay the same until answered, so it's safe to ask questions in
between. Picks are saved to `wishlist.yaml` by the tool. Run `ties` again for
each remaining tied weapon, then any `next` rounds it asks for.

### 5. "Put my stuff back" / "I'm finished" → done

Run `done` and tell them what went back. If shards were left undismantled,
say so (they stay shards for next time).

## Other requests

- **"Why is X being sharded/kept?"**: find it in the latest plan by name and
  explain its `reasons` against the README flowchart.
- **"Add this roll to my wishlist"**: edit `wishlist.yaml` by hand, following
  the format in its header comment (weapon name, `label`, `perks` by column,
  optional `mode`), and confirm it's saved. The next `plan` picks it up.
- **Preferences** (classes, armor archetypes, stat priority, `never_keep`):
  edit `config.yaml`, keeping its comments.
- **"What if"**: `plan --profile <snapshot>` plans from a saved snapshot in
  `data/profiles/` (save one with `fetch`); `apply` never uses it.
- **Game patch or Voltron trouble**: `update-data`. The README's "If
  something breaks" table maps errors to causes.

## Never

- Run `apply`, `next`, `ties`, or `done` without the player asking for that
  step in the conversation. Plans and questions are always fine.
- Lock, unlock, or move items any other way than through these commands.
- Tell them an item was dismantled: the tool can't dismantle, they do it
  in-game.
