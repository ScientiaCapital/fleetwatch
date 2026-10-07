# Fleetwatch for Epiphan Edge

An always-on, read-only watcher for an Epiphan Edge fleet. It checks every room on a heartbeat, posts a calm
Slack digest when something changes, and says **Ready** or **Not ready** 30 minutes before each scheduled class.

v0.1 is **observe-only**: it can't change a device. Write tools are refused inside the client before any
request leaves the machine (`tool_policy.yaml`), and `policy.yaml` is forced to `autonomy: observe`.

## Quick start

Runs on an Apple Silicon Mac or Mac mini, a Raspberry Pi 5, or any Linux box. Needs [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env            # Slack token + channel; leave the token empty to print to the console
uv run fleetwatch login        # one-time sign-in to Epiphan Edge; pick the team to watch
uv run fleetwatch digest         # one heartbeat, prints or posts the digest
uv run fleetwatch run          # keep going, every 3 minutes (policy.yaml)
uv run fleetwatch status       # signed in? open items?
deploy/install.sh               # run it as a service: launchd on macOS, systemd on Linux
```

`login` prints a sign-in link and opens it when there is a browser. On a headless Pi, open the link on any
device; if the final localhost page can't load, paste its URL back into the terminal. The token lives in
`~/.fleetwatch/epiphan-oauth.json` (mode 600) and refreshes itself. Europe or Australia accounts set
`FLEETWATCH_EPIPHAN_MCP_URL` to `eu.` or `au.epiphan.cloud`.

No account handy? `uv run fleetwatch digest --replay tests/fixtures` runs a full heartbeat against a saved
(redacted) fleet sample and prints the digest. Nothing is signed in and nothing is remembered.

## What it posts

- **Needs attention**, with priority in words: *Fix first* (a class won't record: unit offline, no picture on a
  channel), *Fix soon* (firmware behind its family, restarted recently, running hot), *When convenient*.
- The same problem is posted once, reminded at most every 4 hours, and closed with **Back to normal**.
- Storage warnings are an FYI line, never a problem: Pearls on a CMS record locally and upload after class.
- Quiet hours (22:00 to 06:30 by default) only let *Fix first* items through.
- Before each class: `Room 204 · BIO 101 at 2:00 PM: Ready, with notes`.

## Layout

```
policy.yaml            how it behaves (heartbeat, quiet hours, scope, thresholds)
tool_policy.yaml       which Epiphan tools may be called; only `read` is ever used
src/fleetwatch/
  epiphan/             sign-in (auth.py), read-only MCP client (mcp.py), parsers (parse.py)
  agents/scanner/      what needs attention (ported from the Edge Claude Kit's /find-problems)
  agents/readiness/    Ready / Not ready before class (ported from /check-room)
  agents/room_state/   offline / live / pre-class / idle
  state.py             SQLite: open items, what was posted when, audit log
  notify/              digest templates in plain language; Slack or console
  heartbeat.py         one tick: read, diff, post once
  redact.py            stream keys and credentialed URLs never reach a log or a model
  epiphan/replay.py    run the heartbeat from saved tool results, for demos and tests
deploy/                launchd agent (macOS), systemd unit (Linux), install.sh picks the right one
tests/                 48 tests, including the kit's redaction cases and a real (redacted) fleet sample
```

## Status

v0.1. Unit and replay tests pass. The first live run against a real team is still to do (the shared test
team was in use). Known: Epiphan's MCP server reports an expired or missing sign-in inside the tool result
rather than as an HTTP 401, so `login` starts the OAuth flow itself.

Most heartbeats are plain code with no LLM call. Later versions add proposals with Slack approval, then
routine fixes on their own; the guard, the dry run and the redaction stay.

Built from the [Epiphan Edge Claude Kit](https://github.com/ScientiaCapital/epiphan-edge-claude-kit). Apache-2.0.
