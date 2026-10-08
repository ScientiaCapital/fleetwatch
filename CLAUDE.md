# Fleetwatch for Epiphan Edge

@AGENTS.md

AGENTS.md (imported above) has the commands, hard rules, style, and map. This file adds what Claude Code needs.

## Rules for Claude Code

1. Epiphan sign-in needs the maintainer's OK in this session: `fleetwatch login`, `run`, `digest` without
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

Fleetwatch has only run in replay mode; it hasn't run against a real Edge team yet. No release is tagged, so the
installer installs `main` and Docker builds locally.

Open in the Sprint 2 milestone: #13 first live run, #14 release v0.1.0, #15 installer on a real Pi 5 and Mac mini,
#25 voice (not built). Sprint 3: #59 and #62 (sign-in security). The sprint plan and event notes are kept on the
maintainer's machine, not in this public repo; `planning/` is gitignored.
