# Replay mode

```bash
uv run fleetwatch digest --replay tests/fixtures
```

Replay runs a full heartbeat against saved, redacted tool results and prints the digest. Nothing is signed in,
nothing is posted, and nothing is remembered: state lives in memory for that one run.

The bundled sample is also the offline demo. It covers every tool a heartbeat reads, so one run shows the whole
product: rooms offline, a channel with no picture, a unit working hard, one running warm, a recent restart, the
storage FYI, and two checks before class, one *Ready* and one *Not ready*. The device list is a real fleet, renamed
and with IDs replaced; the recorder, system and schedule files are synthetic.

## Your own sample

Save each tool's JSON result as `<tool name>.json` in a folder, for example `get_devices_in_my_team.json`. Tools
with no saved file are reported and skipped.

Times can be written relative to when the replay runs, so a sample stays current:

| Token | Becomes |
|---|---|
| `{{now}}` | the current time, as `2026-10-07T15:00:00Z` |
| `{{now+25m}}` | 25 minutes from now |
| `{{now-2h}}` | 2 hours ago |

**Rename rooms and remove IDs, IPs, serials and people's names before you share a sample.**
