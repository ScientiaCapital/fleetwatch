# Fleetwatch for Epiphan Edge

@AGENTS.md

AGENTS.md (imported above) has the commands, hard rules, style and map. This file adds what Claude Code needs.

## Rules for Claude Code

1. **Epiphan sign-in needs the maintainer's OK in this session**: `fleetwatch login`, `run`, `digest` without
   `--replay`, and the claude.ai Epiphan connectors. The Edge Showcase team is shared.
2. **Main is protected.** Branch, PR, 15 required checks green, squash-merge. Keep the job names "Docs build",
   "Replay heartbeat" and "Docker image" (they are required checks).
3. **Show it works.** Run pytest and the replay digest and paste the output. Use `set -o pipefail` so a failure
   piped through `tail` or `grep` isn't hidden.
4. **Ownership.** Copyright Epiphan Systems Inc. (NOTICE). Keep the trademark line and "not an officially
   supported Epiphan product" wherever licensing is described.
5. **Settings** live in `.env.example`. Add new `FLEETWATCH_*` variables there, not here.

## More commands

```bash
uvx pre-commit run --all-files          # same checks as CI lint
deploy/install.sh --dry-run             # render + validate the service file
uv run --group docs zensical serve      # docs at localhost:8000
```

## Now

Sprint 2; booth about 2026-10-21. What's done and what's left: [planning/next-sprint.md](planning/next-sprint.md).
