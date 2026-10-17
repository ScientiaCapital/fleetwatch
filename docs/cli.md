# Command reference

Every command is `fleetwatch <command>`. From a source checkout, put `uv run` in front.

| Command | What it does |
|---|---|
| `connect` | The guided first run: pick your region, sign in, and check a device. Add `--region na`, `eu` or `au` to skip the question. |
| `login` | Sign in to Epiphan without the guided steps. `--sandbox` signs in to the v0.2 sandbox slot instead. |
| `logout` | Ask Epiphan to revoke your sign-in and delete the saved one. `--sandbox` signs out of the sandbox only. |
| `digest` | One heartbeat: read the fleet, compare with what was seen, post only if something changed. `--replay DIR` uses saved results and needs no sign-in. `--capture DIR` also saves every redacted result to DIR (keep DIR outside the repo). |
| `run` | The always-on agent: a heartbeat on the schedule in `policy.yaml`, the daily sweep, and before-event checks. |
| `status` | Whether you are signed in and which items are open. `--check` prints one line and exits 1 unless a heartbeat read the fleet recently, so it works as a health check. |
| `doctor` | Checks your settings, sign-in, policy files, whether Epiphan and Slack are reachable, and the service, and exits non-zero if something needs fixing. |
| `ask "question"` | Answers from a fresh read, for example `fleetwatch ask "is Main Stage ready"`. `--serve` opens a local page on 127.0.0.1. `--no-ai` gives keyword answers only. `--replay DIR` uses saved results. |
| `approve --serve` | Opens the local approval page (v0.2), where a person approves one proposed change at a time. See [Approving changes](approving-changes.md). `--replay DIR` runs a demo with no sign-in and no changes. |
| `sweep` | Runs the daily sweep once: a calm summary of who is offline and who is behind on firmware, compared with the last sweep. It never opens items; the heartbeat stays the only source of alerts. `--replay DIR` uses saved results. |
| `history` | The sweep history: offline and firmware changes over time. `--days N` sets how far back (default 7). |
| `note "room" "text"` | Leaves a note on a room, for example `fleetwatch note "Room 204 Pearl Mini" "Bulb replaced"`. Notes stay on this machine and never call Epiphan. `--author NAME` sets who left it (default: your login name). |
| `notes [room]` | Lists the notes, or only one room's. `--search TEXT` shows only notes containing TEXT. |

## Options for every command

- `-v`, `--verbose` shows more logging. Secrets stay redacted.
- `--version` prints the version.

## Safe to try

`digest`, `ask`, `sweep` and `approve` with `--replay tests/fixtures` never contact Epiphan and never post. See
[Replay mode](replay.md). `connect`, `login`, `run` and `digest` without `--replay` sign in to a real team.
