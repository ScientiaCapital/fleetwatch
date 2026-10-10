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

Fleetwatch has made one live, read-only run against a real Edge team (the normal sign-in; it found two Epiphan shape
quirks, now handled). The 24-hour soak is done: about 28 hours of steady beats, no failed beat, no sign-in error, and
the token refreshed from its saved endpoint across restarts. The agent exited twice when the Mac slept; that is fixed
(#129, #130). The agent is stopped and signed out (`fleetwatch logout` revoked the token), so the next live run needs
the maintainer's OK and a new sign-in. `fleetwatch connect` is the guided first run for a person's own copy (region, sign-in,
device check); it is tested only against fakes and has not been run against a real team. The v0.2 assistant, approval page and write executor are merged but tested
only against fakes and mocks: no sandbox sign-in, no write and no model API call has been made. No release is
tagged, so the installer installs `main` and Docker builds locally.

Open: #13 (what `iss` the sign-in reports), #14 release v0.1.0, #15 installer on a real Pi 5 and Mac mini, #25 voice
(not built), #82 local model. Sprint 3: #59 and #62 (sign-in security) and #119 to #124 (what a read-only review found
before the soak: the sign-in refresh, restarts, and the in-band 401 check). The sprint plan and event notes are kept on
the maintainer's machine, not in this public repo; `planning/` is gitignored.
