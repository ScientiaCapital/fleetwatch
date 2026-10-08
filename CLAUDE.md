# Fleetwatch for Epiphan Edge

Always-on, **read-only** watcher for an Epiphan Edge fleet (Pearl encoders, EC20 cameras). Each heartbeat reads
the fleet through Epiphan's MCP server, diffs against SQLite, and posts a calm Slack digest only when something
changed; before each event it posts Ready / Not ready. [AGENTS.md](AGENTS.md) holds the same rules for every AI
assistant; this file adds Claude Code specifics and project status.

## Stack

Python 3.12 · uv · ruff · pytest (+ pytest-asyncio) · `mcp` SDK (no AI model in v0.1) (streamable HTTP, OAuth) · pydantic-settings ·
slack-sdk · SQLite · launchd / systemd / Docker · Zensical (pinned) on GitHub Pages.

## Directory Structure

```
policy.yaml              behaviour: heartbeat, quiet hours, scope, thresholds (autonomy forced to observe)
tool_policy.yaml         the read list; anything else is refused by the guard
src/fleetwatch/
  cli.py                 login | digest | run | status | doctor | logout | ask | sweep | history
  sweep.py               nightly sweep (offline, behind on firmware, changes) and the history view
  ask.py, ask_page.py    typed questions answered from the state DB; the local page (stdlib http.server)
  doctor.py              read-only health checks for a new machine (no sign-in, no tool calls)
  config.py              FLEETWATCH_* settings from env / .env
  epiphan/               auth.py (OAuth, 127.0.0.1 callback), mcp.py (guard + redact), parse.py, replay.py
  agents/                scanner/ (what needs attention), readiness/ (before an event), room_state/
  heartbeat.py           one tick: read, diff, post once
  state.py               SQLite: open items, posts, audit log
  notify/                digest.py templates, slack.py (console when no token)
  redact.py              stream keys, passwords, credentialed URLs -> [redacted]
deploy/                  launchd plist, systemd unit, install.sh (--dry-run)
install.sh               curl | bash one-liner
Dockerfile, compose.yaml hardened image (non-root uid 10001, read-only, no ports)
docs/                    docs site (Zensical); planning/next-sprint.md is the current plan
tests/                   pytest suite; fixtures/ is the replay demo (real device list renamed, rest synthetic)
.github/                 CI, issue forms, PR template, CODEOWNERS, dependabot, release.yml
LICENSE, NOTICE          Apache-2.0; copyright Epiphan Systems Inc.; NOTICE credits the MIT kit and trademarks
```

## Key Commands

```bash
uv sync
uv run python -m pytest -q
uv run fleetwatch digest --replay tests/fixtures      # full heartbeat, no sign-in
uv run ruff check . && uv run ruff format --check .
uvx pre-commit run --all-files                         # same checks as CI lint
deploy/install.sh --dry-run                            # render + validate the service file
uv run --group docs zensical serve                     # docs at localhost:8000
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
| `FLEETWATCH_ASK_PORT` | `8766` | `fleetwatch ask --serve` page on 127.0.0.1 |
| `FLEETWATCH_STATE_DB`, `FLEETWATCH_TOKEN_FILE` | `~/.fleetwatch/...` | State and token paths |
| `FLEETWATCH_TOKEN_STORE` | `auto` | `auto`, `file`, `keychain`, `systemd-creds` (auto: Keychain on macOS, systemd-creds on systemd 256+, else file) |

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
7. **Ownership:** copyright Epiphan Systems Inc. (NOTICE). Keep the trademark line and "not an officially
   supported Epiphan product" wherever the README or docs describe licensing.
8. **Name:** Fleetwatch (capital F only). Display name "Fleetwatch for Epiphan Edge" until Epiphan adopts it.
9. Before claiming something works, run the tests and the replay digest and show the output.

## Status (2026-10-07, end of day)

- Sprint 1 (repo) done: public repo, protected main, CI, security files, Docker, installer, docs site, README.
- Sprint 2: [planning/next-sprint.md](planning/next-sprint.md) has what's done and tomorrow's plan with specs;
  milestone "Sprint 2" on GitHub. Closed today: #12 (Zensical), #21 (sweep), #34 (README). In: `ask` (#25 part 1),
  release workflow (#14 prep), vertical wording, Slack escaping.
- Left: #13 + #15 (need the maintainer and the Pi), #14 tag, #23, #20, #19, #18, #25 voice.
