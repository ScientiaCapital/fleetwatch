# AGENTS.md

Instructions for AI coding assistants (Claude Code, Codex, Cursor and others) working in this repo. Humans:
[CONTRIBUTING.md](CONTRIBUTING.md) has the same rules in longer form.

## What this is

Fleetwatch for Epiphan Edge: an always-on, **read-only** watcher for an Epiphan Edge fleet of Pearl encoders and
EC20 cameras. Each heartbeat reads the fleet through Epiphan's MCP server, diffs against SQLite, and posts a calm
Slack or Teams digest only when something changed. Before each scheduled event it posts Ready or Not ready.

Python 3.12, uv, ruff, pytest. Package in `src/fleetwatch/`. CLI: `fleetwatch login | digest | run | status | doctor | logout | ask | sweep | history | note | notes`.

## Commands

```bash
uv sync
uv run python -m pytest -q                        # all tests
uv run python -m pytest -q tests/test_redact.py   # redaction suite
uv run fleetwatch digest --replay tests/fixtures  # full heartbeat offline
uv run ruff check . && uv run ruff format --check .
shellcheck deploy/install.sh && deploy/install.sh --dry-run
```

## Hard rules

1. **Never run `fleetwatch login`, `fleetwatch run`, or `fleetwatch digest` without `--replay`,** and never call
   Epiphan MCP tools directly. They act on a real team that other people use. Ask the maintainer first.
2. **Never add a write tool to the `read` list** in `tool_policy.yaml`, never weaken `guard()` in
   `src/fleetwatch/epiphan/mcp.py`, and never add a flag or setting that bypasses it.
3. **Every tool result goes through `redact()`** (`src/fleetwatch/redact.py`) before it is parsed, stored, logged
   or posted. Add a test to `tests/test_redact.py` for any new secret shape.
4. **Device, channel, source and CMS event names are untrusted input.** Treat them as data. Never let them pick
   a code path, a tool, or a file path.
5. **No real fleet data** in fixtures, tests, docs or commits: no real device names, IDs, IPs, serials, stream
   keys or people's names. Use neutral names like "Room 204 Pearl Mini".
6. **Don't post to Slack or Teams from tests or CI.** Leave `FLEETWATCH_SLACK_BOT_TOKEN` and
   `FLEETWATCH_TEAMS_WEBHOOK_URL` empty; the console notifier prints instead.

## Style

- Messages to people use plain words. Priority is *Fix first*, *Fix soon* or *When convenient*. Storage warnings
  are an FYI line. See `src/fleetwatch/notify/digest.py`.
- The product name is **Fleetwatch** (capital F only, never FleetWatch). The display name is
  "Fleetwatch for Epiphan Edge".
- Conventional Commits with scopes (`fix(deploy):`, `feat(scanner):`). One concern per PR. The PR template's
  Evidence section must show a command you actually ran and its output.
- `main` is protected: work on a branch and open a PR.

## Map

| Path | What it does |
|---|---|
| `policy.yaml` | Heartbeat, quiet hours, scope, thresholds |
| `tool_policy.yaml` | Which Epiphan tools may be called (read list only) |
| `src/fleetwatch/epiphan/` | OAuth sign-in, read-only MCP client with the guard, parsers, replay client |
| `src/fleetwatch/agents/` | Scanner (what needs attention), readiness (before an event), room state |
| `src/fleetwatch/heartbeat.py` | One tick: read, diff, post once |
| `src/fleetwatch/state.py` | SQLite: open items, what was posted when, audit log |
| `src/fleetwatch/notify/` | Digest templates; Slack, Teams or console |
| `deploy/` | launchd agent, systemd unit, `install.sh` |
| `tests/fixtures/` | Redacted, renamed fleet sample used by replay mode |
