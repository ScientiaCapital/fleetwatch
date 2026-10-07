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

## Done so far

**2026-10-07, first session**
- Offline demo: the replay sample covers every tool and shows Ready and Not ready checks (#27).
- `fleetwatch doctor` (#22), `status --check` with a Docker HEALTHCHECK (#16).
- Unused Anthropic dependency removed (#17). `--version` and release metadata in place for #14.
- Booth runbook: [At a booth](../docs/booth.md). Required-check policy no longer forces every PR to rebase.

**2026-10-07, second session (PRs #37 to #46)**
- README first screen: banner, badges, quick links, "What you get", demo card, troubleshooting, Spanish README (#34).
- CI no longer fails every night: the replay digest ignores quiet hours (#38).
- `vertical:` setting picks the words (event, class, meeting, hearing, service). Default is `events` (#40).
- State DB remembers devices, readiness details, and each finding's impact and fix; old DBs upgrade in place (#41).
- `fleetwatch ask`: typed questions (also in Spanish) answered from the state DB, no model, no network.
  `ask --serve` is the booth page on 127.0.0.1:8766 (#42, first slice of #25).
- Release workflow: a `v*` tag pushes `:latest` too and creates the GitHub Release with update steps. The Linux
  installer now restarts the service on update (#43).
- Docs build with Zensical 0.0.68; sprint plans moved to `planning/` (#44, closes #12).
- Nightly sweep and `fleetwatch history` (#45, closes #21).
- Slack posts escape `&`, `<`, `>`, so a device name can't ping a channel or post a link (#46).

## Decisions made 2026-10-07

- Slack stays console-only for the booth. v0.1.0 ships on GitHub and GHCR only; no PyPI yet.
- Docs: Zensical, pinned exactly (decision written in #12). Fallback: MkDocs 1.6.1 + Material 9.7.7 pinned.
- Voice (#25): typing is the main path (`fleetwatch ask`). Push-to-talk voice is an optional layer on the same
  page and falls back to typing. Stack: Deepgram (speech to text), Cartesia (speech), no OpenAI. Booth hardware:
  close-talk headset mic, USB push-to-talk button or pedal, USB speaker, Pi 5 with a wired display, second hotspot.
- Default wording is live events (`vertical: events`); schools set `vertical: education`.
- All four "after the release" features (#19, #20, #21, #23) are in scope for Sprint 2.

## Next session: what's left (booth about 2026-10-21)

Start by asking the maintainer one thing: **is the Pi on the hotspot?** Blocks A and B need them present.
Everything else can run without them, in this order.

| Block | Work | Issue | Done when |
|---|---|---|---|
| A. With the maintainer | Pi 5 on the hotspot: real one-line install, reboot, `systemctl --user status fleetwatch`, `fleetwatch doctor`, `fleetwatch login` (Edge Showcase only), first live `digest` | #15, #13 | doctor shows no FAIL; a live digest prints; the service survives a reboot |
| A2 | Capture redacted tool results, rename devices, replace IDs, add as a second fixture set; fix `epiphan/parse.py` where shapes differ | #13 | replay of the capture passes in CI |
| A3 | Same install on the Mac mini; `launchctl print gui/$UID/dev.fleetwatch.agent`; reboot check | #15 | agent running after reboot |
| B. Release | `git tag -a v0.1.0 -m "Fleetwatch 0.1.0" && git push origin v0.1.0` on main; make the GHCR package public; on the Pi: `docker pull ghcr.io/scientiacapital/fleetwatch:0.1.0` and `gh attestation verify oci://ghcr.io/scientiacapital/fleetwatch:0.1.0 --owner ScientiaCapital` | #14 | release page exists, pull and verify work. Tag even if A slips |
| C | Per-room notes | #23 | see spec below |
| D | Slack `/fleetwatch check <room>` | #20 | see spec below |
| E | Microsoft Teams channel | #19 | see spec below |
| F | Token in Keychain / systemd-creds | #18 | see spec below |
| G | Push-to-talk voice on the `ask` page | #25 | see spec below; only after C to F are green |

### Specs for C to G

- **#23 Room notes.** `notes(id, device_id, note, author, at)` table; `fleetwatch note "<room>" "<text>"` resolves
  the room with `State.find_devices` and refuses 0 or >1 matches (lists candidates); `fleetwatch notes [room |
  --search text]`. Notes are untrusted: `redact()`, strip control characters, cap 280 chars. Shown as a quoted
  line under that device's finding in the digest, and in `ask` answers for that room. Tests: stream key redacted,
  ambiguous room refused, note shows under the right device.
- **#20 Slack check.** Socket Mode (`slack_sdk.socket_mode.builtin.SocketModeClient`, no inbound port), started
  inside `fleetwatch run` only when `FLEETWATCH_SLACK_APP_TOKEN` (`xapp-`, `connections:write`) is set; the bot
  token also needs `commands`. Allowlist in `policy.yaml` (`slack.allowed_user_ids`, `slack.allowed_usergroup`);
  empty means nobody. Handler is a pure function `(text, user_id, allowlist, state, policy, fleet) -> reply`
  that calls `ask.answer`; reply ephemeral. `docs/slack-commands.md` with the app manifest. `doctor` row.
- **#19 Teams.** Power Automate "Workflows" webhook (`FLEETWATCH_TEAMS_WEBHOOK_URL`), Adaptive Card with one
  TextBlock; convert `*x*` to `**x**`. `Notifier` fans out to every configured channel; console when none.
  `urllib.request` with a 10 s timeout, errors log and return False. `doctor` row, `.env.example`, install docs.
- **#18 Token store.** `TokenStore` protocol plus a factory (`FLEETWATCH_TOKEN_STORE=auto|file|keychain|
  systemd-creds`). `FileTokenStorage` is built in `cli.py` (3 places) and `doctor.py`, and named in type hints in
  `epiphan/auth.py` and `epiphan/mcp.py`. Keychain via `security` with the JSON on stdin, never in argv.
  systemd-creds encrypts at rest (`encrypt`/`decrypt`, needs systemd >= 256 for `--user`), because the OAuth
  token refreshes itself and `LoadCredential` is read-only. Docker stays on the file store. Update SECURITY.md.
- **#25 Voice, second slice.** Hold-to-talk button on the `ask --serve` page (`MediaRecorder`); the local server
  sends the clip to Deepgram, the transcript to `ask.answer`, the answer to Cartesia. Transcript is untrusted
  text and shown before the answer. Any error or 4 s timeout: "Type it instead". Button only appears when both
  API keys are set. Add the hardware list to `docs/booth.md`.

### Watch-outs

- The repo is public. Fixtures from the live run must use only generally available models and firmware
  (Pearl-2, Pearl Mini, Pearl Nano, Pearl Nexus, EC20; firmware up to 4.24.6, EC20 3.3.x). Check before committing.
- Never sign in (`login`, `run`, `digest` without `--replay`) or use the claude.ai Epiphan connectors without the
  maintainer saying so in that session (CLAUDE.md rule 1).
- Required checks include "Docs build", "Replay heartbeat" and "Docker image"; keep those job names.

## Ground rules

- Read-only stays read-only. Writes come later through a Slack approval bound to the exact call, designed in an
  issue first.
- Everything goes through a PR with an Evidence section. See [CONTRIBUTING.md](../CONTRIBUTING.md).
- No real fleet data anywhere in the repo.
