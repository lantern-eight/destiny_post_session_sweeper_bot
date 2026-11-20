# Agent Guidelines

Guidance for AI assistants working in this codebase. This file is committed and
holds no private details.

Read `README.md` before changing behavior. Its sections "How each item is
decided" and "What `apply`, `next`, and `done` do with it" say how the tool
behaves. Keep them true: a change to that behavior updates them in the same
change.


## Style

- Indentation: Use 2 spaces per indent level (no tabs).
- Variable naming: Avoid shorthand. Prefer expanded, descriptive variable names over
  abbreviations.
- Line length: 88 characters.
- Single quote for strings, `'''` for docstrings. Config yaml files can use double
  quotes to support escape sequences.
- Python 3.13 with `uv` (`pyproject.toml` + `uv.lock`). Run the CLI with
  `uv run sweeper <command>` and tests with `uv run python -m pytest`.


## Secrets and personal data

- Bungie credentials live in `.env` (gitignored). `.env.example` is the template.
- Load secrets with `python-dotenv` / `os.getenv()`. Never hardcode them.
- Never print or log token values, the API key, or the client secret.
- Gitignored personal data: `.bungie_token_cache` (OAuth tokens), `data/` (lock
  state, inventory snapshots, plans, raw profiles), `.cache/` (manifest, Voltron).
  `data/` is kept private (0700), and the CLI creates files only the user can
  read.
- Write files through `src/files.write_atomically`, so an interrupted write or a
  full disk never leaves a half-written file.


## Committed files are public

Write everything committed as if the repository were public, because it may
become so, and a detail in Git history is hard to take back out. None of it may
contain:

- the player's Bungie Name, membership ID, character IDs, item instance IDs,
  clan name, or anything else that points at their account
- names, usernames, or emails of people, beyond the Git identity on a commit
- local paths (use paths relative to the repo) or the user's home folder
- network details: IP addresses, hostnames, machine names
- API keys, tokens, client secrets, or the contents of `.env` or the token cache
- anything else about who runs this tool or how their machines are set up

That covers code, comments, docstrings, docs, test fixtures, config files,
commit messages, and branch names. In particular:

- Never paste a real API response, CLI output, or a file from `data/` into a
  fixture or a doc: they hold the Bungie Name and account IDs. Build fixtures
  by hand, trimmed to what the test needs.
- Use only these placeholders: the Bungie Name `Guardian#0042` (any 4-digit
  code), and 19-digit IDs with a run of at least six zeros, such as
  `2305843009000000001`.
- The `privacy-check` pre-commit hook (`scripts/privacy_check.py`) blocks
  commits and commit messages with real-looking IDs, Bungie Names, local paths,
  private IP addresses, or emails, and `tests/test_privacy.py` runs it over
  every file Git would commit. Fix what it finds; never skip it with
  `--no-verify`. If it flags something harmless, change the check, not the
  rule.


## Private notes stay out of Git

Working notes (code reviews, plans, research, task lists, logs) live in
`notes/`, which is gitignored.

- Never commit anything from `notes/`, and never copy its text into a committed
  file. Write a clean doc or code comment instead.
- Commit messages, code comments, and docs never point at those notes. A commit
  explains its own change and nothing else.


## Safety rules

- `plan` must stay side-effect free. Only `apply`, `next`, `ties`, and `done`
  change the user's account (locks and moves only), and only as the latest
  plan says, with the user's live locks winning. The one exception, which the
  user asked for: everything in the session character's Postmaster goes to the
  vault first, planned or not, so nothing is lost to a full Postmaster.
- Never unlock an item the tool did not lock itself. The state file records
  each lock the tool places, and only those are ever undone (see "What `apply`,
  `next`, and `done` do with it" in `README.md`).
- Never move equipped items. `Account` refuses to, as a second line of defense.
- Save the state file before any move that depends on it, so an interrupted run
  can be run again.
- Test account-changing code against `tests/account_fixtures.FakeBungie`, and try
  changes on the real account with `--dry-run` before a live run.
- Tests must never read or write the real `data/` or `.cache/` folders:
  `tests/conftest.py` points the CLI's plan folders and command lock at a
  temporary folder, and anything else that writes takes a path to point there.
