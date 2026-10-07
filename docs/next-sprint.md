# Sprint 2: plan

Written 2026-10-07 at the end of Sprint 1. Tracked as the **Sprint 2** milestone on GitHub
(https://github.com/ScientiaCapital/fleetwatch/milestone/1).

## Where things stand

Sprint 1 finished steps 1 to 5 of [next-sprint-repo-brief.md](next-sprint-repo-brief.md):

| Area | State |
|---|---|
| Repo | Public, `main` protected: PR required, 15 required checks, squash-only, auto-merge |
| Security settings | Secret scanning, push protection, private vulnerability reporting, Dependabot |
| CI | Lint, tests (macOS, Linux x86 and ARM), redaction suite, replay, install dry runs, Docker build, docs build, CodeQL, pip-audit, dependency review, actionlint, zizmor |
| Files | SECURITY, CONTRIBUTING, CODE_OF_CONDUCT, AGENTS, CLAUDE, issue forms, PR template, CODEOWNERS, pre-commit |
| Distribution | `install.sh` one-liner, Docker image + compose (GHCR on tags, SBOM, provenance), docs on Pages |
| Not yet | A live run against a real team; any release |

## Order

1. **#13 First live run** (blocked: ask the maintainer which team and when; Edge Showcase is shared).
   Save the missing tool results as redacted fixtures.
2. **#14 Release v0.1.0**: reserve the PyPI name, tag, GHCR image public, generated notes.
3. **#12 Move the docs off MkDocs before 2.0.** Must land this sprint. Evaluate Zensical vs pinned MkDocs 1.6.
4. **#15 Verify the installer** on a real Pi 5 and Mac mini (CI only dry-runs it).
5. Housekeeping: **#17** unused `anthropic` dependency, **#16** Docker HEALTHCHECK, **#18** Keychain / systemd-creds.
6. Features, after the release: **#19** Teams, **#20** two-way `check`, **#21** nightly sweep, **#22** `doctor`,
   **#23** per-room notes.

## Ground rules

- Read-only stays read-only. Writes come later through a Slack approval bound to the exact call, designed in an
  issue first.
- Everything goes through a PR with an Evidence section. See [CONTRIBUTING.md](../CONTRIBUTING.md).
- No real fleet data anywhere in the repo.
