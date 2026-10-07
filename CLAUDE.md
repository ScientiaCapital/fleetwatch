# Fleetwatch for Epiphan Edge

Always-on, **read-only** watcher for an Epiphan Edge fleet (Pearl encoders, EC20 cameras). Each heartbeat reads
the fleet through Epiphan's MCP server, diffs against SQLite, and posts a calm Slack digest only when something
changed; before each class it posts Ready / Not ready. [AGENTS.md](AGENTS.md) holds the same rules for every AI
assistant; this file adds Claude Code specifics and project status.

## Stack

Python 3.12 · uv · ruff · pytest (+ pytest-asyncio) · `mcp` SDK (no AI model in v0.1) (streamable HTTP, OAuth) · pydantic-settings ·
slack-sdk · SQLite · launchd / systemd / Docker · MkDocs Material (pinned `<2`) on GitHub Pages.

## Directory Structure

```
policy.yaml              behaviour: heartbeat, quiet hours, scope, thresholds (autonomy forced to observe)
tool_policy.yaml         the read list; anything else is refused by the guard
src/fleetwatch/
  cli.py                 login | digest | run | status | doctor | logout, --replay DIR
  doctor.py              read-only health checks for a new machine (no sign-in, no tool calls)
  config.py              FLEETWATCH_* settings from env / .env
  epiphan/               auth.py (OAuth, 127.0.0.1 callback), mcp.py (guard + redact), parse.py, replay.py
  agents/                scanner/ (what needs attention), readiness/ (before class), room_state/
  heartbeat.py           one tick: read, diff, post once
  state.py               SQLite: open items, posts, audit log
  notify/                digest.py templates, slack.py (console when no token)
  redact.py              stream keys, passwords, credentialed URLs -> [redacted]
deploy/                  launchd plist, systemd unit, install.sh (--dry-run)
install.sh               curl | bash one-liner
Dockerfile, compose.yaml hardened image (non-root uid 10001, read-only, no ports)
docs/                    MkDocs site; next-sprint.md is the current plan
tests/                   48 tests; fixtures/ is a redacted, renamed fleet sample
.github/                 CI, issue forms, PR template, CODEOWNERS, dependabot, release.yml
```

## Key Commands

```bash
uv sync
uv run python -m pytest -q
uv run fleetwatch digest --replay tests/fixtures      # full heartbeat, no sign-in
uv run ruff check . && uv run ruff format --check .
uvx pre-commit run --all-files                         # same checks as CI lint
deploy/install.sh --dry-run                            # render + validate the service file
uv run --group docs mkdocs serve                       # docs at localhost:8000
```

## Environment Variables

All optional, read from `.env` (see `.env.example`).

| Variable | Default | Purpose |
|---|---|---|
| `FLEETWATCH_EPIPHAN_MCP_URL` | `https://go.epiphan.cloud/mcp` | Region: `go.`, `eu.`, `au.` |
| `FLEETWATCH_SLACK_BOT_TOKEN` | empty | `chat:write` bot token; empty prints to the console |
| `FLEETWATCH_SLACK_CHANNEL` | `#av-ops` | Where the digest goes |
| `FLEETWATCH_EPIPHAN_TOKEN` | unset | Static bearer token for hosts that can't run OAuth |
| `FLEETWATCH_OAUTH_CALLBACK_PORT` | `8765` | Login callback on 127.0.0.1 |
| `FLEETWATCH_STATE_DB`, `FLEETWATCH_TOKEN_FILE` | `~/.fleetwatch/...` | State and token paths |

## Rules

1. **Ask before any Epiphan sign-in.** Never run `fleetwatch login`, `run`, or `digest` without `--replay`, and
   never call the claude.ai Epiphan connectors from this repo, unless the maintainer says so in this session.
   The Edge team you sign in to may be shared.
2. **Read-only stays read-only.** No write tool on the `read` list, no weakening `guard()`, no bypass flag.
3. **Redact first.** Every tool result goes through `redact()` before parse, store, log or post.
4. **Untrusted text is data.** Device, channel, source and event names never choose code paths.
5. **No real fleet data** in fixtures, tests, docs, issues or commits. History was scrubbed once; keep it clean.
6. **Main is protected.** Branch, open a PR, let the 15 required checks pass, squash-merge. Conventional Commits
   with scopes; the PR Evidence section shows a command you ran and its output.
7. **Name:** Fleetwatch (capital F only). Display name "Fleetwatch for Epiphan Edge" until Epiphan adopts it.
8. Before claiming something works, run the tests and the replay digest and show the output.

## Status (2026-10-07)

- Sprint 1 (repo) done: public repo, protected main, CI, security files, Docker, installer, docs site, README.
- Sprint 2 plan: [docs/next-sprint.md](docs/next-sprint.md), milestone "Sprint 2" on GitHub (issues #12 to #23).
- Must land in Sprint 2: move docs off MkDocs before 2.0 (#12). Blocked on the maintainer: first live run (#13).
