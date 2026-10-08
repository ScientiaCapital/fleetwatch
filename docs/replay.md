# Replay mode

```bash
uv run fleetwatch digest --replay tests/fixtures
```

Replay runs a full heartbeat against saved, redacted tool results and prints the digest. Nothing is signed in,
nothing is posted, and nothing is remembered: state lives in memory for that one run.
Quiet hours don't apply, so the digest looks the same at any time of day.

The bundled sample is also the offline demo. It covers every tool a heartbeat reads, so one run shows the whole
product: rooms offline, a channel with no picture, a unit working hard, one running warm, a recent restart, the
storage FYI, and two checks before an event, one "Ready" and one "Not ready". The device list is a real fleet, renamed
and with IDs replaced; the recorder, system, and schedule files are synthetic.

There is also a calm sample for a screen in a quiet room: `uv run fleetwatch digest --replay tests/fixtures/calm`
shows "All clear" and one event coming up, "Ready". Four rooms, nothing to fix.

## Your own sample

Save each tool's JSON result as `<tool name>.json` in a folder, for example `get_devices_in_my_team.json`. Tools
with no saved file are reported and skipped.

Or let a signed-in Fleetwatch write them: `fleetwatch digest --capture ~/fleetwatch-capture` runs one heartbeat
and saves every tool result it read, redacted, as replay files in that folder (private, with a note saying it's
not for commit). Keep the folder outside the repo: the files come from your real fleet. `--replay DIR --capture DIR2`
works too, and is what the tests use.

To turn a capture into something you can share, anonymize it:

```
uv run python -m fleetwatch.epiphan.anonymize ~/fleetwatch-capture ~/fleetwatch-capture-shareable
```

It keeps only the four tools Fleetwatch reads for a digest, and only the fields it needs. Every device name, ID, group,
address, serial and event title is replaced with a neutral one, the same everywhere, so the files still agree with
each other. Times become the relative tokens below. Before it writes anything it checks that no original name, ID,
address, serial or title survives, and it refuses if one does. Read the result yourself before you commit it: it
can't know that a firmware build is unreleased, or that the size and mix of a fleet is itself private.

Times can be written relative to when the replay runs, so a sample stays current:

| Token | Becomes |
|---|---|
| `{{now}}` | the current time, as `2026-10-07T15:00:00Z` |
| `{{now+25m}}` | 25 minutes from now |
| `{{now-2h}}` | 2 hours ago |

Rename rooms and remove IDs, IPs, serials, and people's names before you share a sample.
