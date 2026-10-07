# Replay mode

```bash
uv run fleetwatch digest --replay tests/fixtures
```

Replay runs a full heartbeat against saved, redacted tool results and prints the digest. Nothing is signed in,
nothing is posted, and nothing is remembered: state lives in memory for that one run.

The repo ships one sample fleet in `tests/fixtures/`. To replay your own, save each tool's JSON result as
`<tool name>.json` in a folder (for example `get_devices_in_my_team.json`). Tools with no saved file are reported
and skipped. **Rename rooms and remove IDs, IPs, serials and people's names before you share a sample.**
