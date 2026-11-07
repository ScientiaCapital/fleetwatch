# Fleetwatch for Epiphan Edge

@AGENTS.md

AGENTS.md (imported above) has the commands, hard rules, style, and map. This file adds what Claude Code needs.

## Rules for Claude Code

1. Epiphan sign-in needs the maintainer's OK in this session: `fleetwatch connect`, `login`, `run`, `digest` without
   `--replay`, and the claude.ai Epiphan connectors. The Edge team you sign in to may be shared.
2. Main is protected. Branch, PR, 15 required checks green, squash-merge. Keep the job names "Docs build",
   "Replay heartbeat", and "Docker image" (they're required checks).
3. Show it works. Run pytest and the replay digest and paste the output. Use `set -o pipefail` so a failure
   piped through `tail` or `grep` isn't hidden.
4. Ownership. Copyright Epiphan Systems Inc. (NOTICE). Keep the trademark line and "not an officially
   supported Epiphan product" wherever licensing is described.
5. Settings live in `.env.example`. Add new `FLEETWATCH_*` variables there, not here.

## More commands

```bash
uvx pre-commit run --all-files          # ruff, shellcheck, actionlint and zizmor as in CI, plus file checks
deploy/install.sh --dry-run             # render + validate the service file
uv run --group docs zensical serve      # docs at localhost:8000
```

## Now

v0.1.0 is released: the tag, the GitHub Release, and the `ghcr.io/scientiacapital/fleetwatch` image (amd64 and arm64).
The installer installs the newest release tag and Docker pulls it.

Fleetwatch has run live and read-only against a real Edge team: a first run, a 28-hour soak, and a later unattended
run from the launchd agent. That run ended when two processes raced for one token refresh (#162). The fix and the rest
of what the soak review found (#119 to #124, #126, #127, #163) are merged but not yet proven live. The agent is disabled
under launchd and the stored sign-in is dead, so the next live run needs the maintainer's OK, `fleetwatch logout`, and
a new sign-in. `fleetwatch connect` is the guided first run for a person's own copy (region, sign-in, device check); it
is tested only against fakes and has not been run against a real team.

The v0.2 assistant, approval page and write executor ship in 0.1.0, off by default, and are tested only against fakes
and mocks: no sandbox sign-in, no write and no model API call has been made. Device-group tools are on the read and
write lists but none can be proposed; the fence they need is designed in `docs/design/group-writes.md` and not built.

`language: es` in `policy.yaml` puts findings, the digest, the Ready lines, `ask` and its page, the sweep, history,
notes, `status` and `doctor` in Spanish (`src/fleetwatch/i18n.py`). Still English: the `connect`, `login` and `logout`
lines, the one-line notes from `digest`, `sweep` and `approve`, CLI help and errors, and the licensing footer. The
approve page follows the browser's language.
`scripts/redact_loop/` is a local, human-gated loop that scores `redact.py`; its dev set has gaps it hasn't closed yet.

Open: #13 (what `iss` the sign-in reports), #15 installer on a real Pi 5 and Mac mini, #25 voice (not built), #82 local
model, #59 (a read-only sign-in scope, waiting on Epiphan), #174 (a signed Keychain helper; until then Fleetwatch runs
as its own macOS user, `docs/mac-mini.md`). The sprint plan and event notes are kept on the maintainer's machine, not in
this public repo; `planning/` is gitignored.
