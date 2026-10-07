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

## Deadline: InfoComm LATAM booth (about 2026-10-21)

The booth demo is a Raspberry Pi on the presenter's phone hotspot, running Fleetwatch against the Edge Showcase
team (cleared with its co-owner), with the digest on screen. Items #13, #15 and #25 serve the booth and come
first. Slack stays off until a demo channel exists; the console digest is the fallback.

## Done so far (2026-10-07)

- Offline demo: the replay sample covers every tool and shows Ready and Not ready checks (#27).
- `fleetwatch doctor` (#22), `status --check` with a Docker HEALTHCHECK (#16).
- Unused Anthropic dependency removed (#17). `--version` and release metadata in place for #14.
- Booth runbook: [At a booth](booth.md). Required-check policy no longer forces every PR to rebase.

## Decisions made 2026-10-07

- The Pi is not on the hotspot yet, so blocks A and B swap: README (#34) and release (#14) first.
- Slack stays console-only for the booth.
- v0.1.0 ships on GitHub and GHCR only; no PyPI yet.
- Voice (#25): typing is the main path. A `fleetwatch ask` command answers from the state DB with no network;
  push-to-talk voice is an optional layer on top and falls back to typing. The booth needs a close-talk headset
  mic, a USB push-to-talk button and a backup network for voice.

## Next session: full-day plan

**Decisions to make at the start** (they unblock the rest):

1. Is the Pi on the phone hotspot? If yes, the morning is the live run. If not, swap blocks A and B.
2. Voice transport for #25: a Twilio phone number, or a LiveKit push-to-talk page.
3. Slack: create a demo channel and bot token now, or stay console-only for the booth.
4. Publish 0.1.0 to PyPI, or GitHub and GHCR only.

| Block | Work | Issues | Done when |
|---|---|---|---|
| A. Morning | Pi online on the hotspot, one-line install, sign in to Edge Showcase, `fleetwatch doctor`, first live digest | #15, #13 | doctor shows no FAIL; a live digest prints on the Pi |
| A2 | Save real tool results (renamed, IDs replaced) and compare them with the synthetic sample's shapes; fix the parser if needed | #13 | replay of the real capture passes in CI |
| B. Late morning | README first screen: banner, feature table, screenshot from the replay demo, troubleshooting, Spanish README | #34 | README reviewed side by side with the peers |
| C. After lunch | Release v0.1.0: tag, GHCR image public, attestation verified, generated notes | #14 | `docker pull ghcr.io/scientiacapital/fleetwatch:0.1.0` works on the Pi |
| D. Afternoon | Voice, first slice: "what needs attention" and "is <room> ready", answered from the state DB, read-only, with an offline test | #25 | the slice works end to end on replay data |
| E. If time | Docs off MkDocs before 2.0 (must land this sprint), then token in Keychain / systemd-creds | #12, #18 | docs build and deploy on the new tool |

## Order

1. **#13 First live run** on the booth Pi against Edge Showcase (cleared with its co-owner on 2026-10-07).
   Save the missing tool results as redacted fixtures.
2. **#14 Release v0.1.0**: reserve the PyPI name, tag, GHCR image public, generated notes.
3. **#12 Move the docs off MkDocs before 2.0.** Must land this sprint. Evaluate Zensical vs pinned MkDocs 1.6.
4. **#15 Verify the installer** on a real Pi 5 and Mac mini (CI only dry-runs it).
5. Housekeeping: **#18** Keychain / systemd-creds.
6. **#25 Voice**: ask Fleetwatch out loud. First slice by the booth if time allows; read-only.
7. Features, after the release: **#19** Teams, **#20** two-way `check`, **#21** nightly sweep,
   **#23** per-room notes.

## Ground rules

- Read-only stays read-only. Writes come later through a Slack approval bound to the exact call, designed in an
  issue first.
- Everything goes through a PR with an Evidence section. See [CONTRIBUTING.md](../CONTRIBUTING.md).
- No real fleet data anywhere in the repo.
