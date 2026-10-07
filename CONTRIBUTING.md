# Contributing

Thanks for helping. Fleetwatch is small on purpose: a calm, read-only watcher that AV teams can trust on a Mac
mini or a Raspberry Pi. Contributions that keep it that way are the most welcome.

## What helps most, in order

1. **Bug reports with a replay.** A redacted tool result that makes the digest wrong is the best bug report.
2. **New checks** in `src/fleetwatch/agents/` that answer a question AV techs keep asking, with a test.
3. **Redaction cases** in `tests/test_redact.py`: any secret shape that slips through.
4. **Docs** for install, sign-in, and running on a Mac mini or a Pi.
5. Everything else. Please open an issue first for anything large, so nobody builds the same thing twice.

## Set up

```bash
uv sync
uv run python -m pytest -q
uv run fleetwatch digest --replay tests/fixtures   # full heartbeat, no sign-in needed
uv run ruff check . && uv run ruff format --check .
```

You don't need an Epiphan account to work on most of the code. Replay mode and the tests cover the heartbeat.

## Rules

- **Read-only stays read-only.** Don't add write tools to the `read` list in `tool_policy.yaml`, don't weaken the
  guard in `src/fleetwatch/epiphan/mcp.py`, and don't add a flag that skips it. Writes will come later through an
  approval flow, designed in an issue first.
- **Untrusted text is data.** Device, channel, source and event names come from users of the fleet. Never use
  them to decide what code runs.
- **Redact before anything else.** New tool results go through `redact()` before they are parsed, stored, logged
  or posted.
- **Plain words in messages.** Priority is *Fix first*, *Fix soon* or *When convenient*. Storage warnings are an
  FYI line, not a problem. No codes or jargon a tech wouldn't use.
- **No real fleet data.** No real device names, IDs, IPs, serial numbers, stream keys, emails or screenshots in
  fixtures, tests, issues or PRs. Rename rooms to things like "Room 204" before you share a replay.

## Commits and pull requests

- [Conventional Commits](https://www.conventionalcommits.org/) with a scope when it helps:
  `fix(deploy): ...`, `feat(scanner): ...`, `docs(readme): ...`, `ci: ...`, `test(redact): ...`.
- One concern per PR. A bug fix and a refactor are two PRs.
- Fill in the **Evidence** section of the PR template: the command you ran and what it printed.
- PRs are squash-merged, so the PR title becomes the commit message. Release notes are generated from PR
  titles; there is no hand-edited CHANGELOG.
- CI must be green: lint, tests on macOS and Linux (x86 and ARM), the redaction suite, the replay run, the
  install dry run and the security scans.

## Using an AI assistant

Fine, and welcome. Read [AGENTS.md](AGENTS.md) and point your assistant at it. You are responsible for every
line you submit, and the Evidence section has to come from a run you did.

## Security issues

Not here. See [SECURITY.md](SECURITY.md).

By contributing you agree that your work is licensed under the [Apache-2.0](LICENSE) license and that you follow
the [Code of Conduct](CODE_OF_CONDUCT.md).
