# Fleetwatch: brief for creating the public repo (next sprint)

Written 2026-10-07 from three research passes (OpenClaw, Hermes Agent, naming). Decision taken the same day:
the project is **Fleetwatch**, display name **"Fleetwatch for Epiphan Edge"** until Epiphan adopts it
(then "Epiphan Fleetwatch", matching Edge, Connect, Unify). Anything Epiphan wants to do with it is fine.

## Name and family convention
- One word, capital F only: Fleetwatch. Never FleetWatch. "Fleet" is already Epiphan's word (Edge: "device
  management for AV fleets"; Pearl Nexus and EC20: "built for fleets").
- Repo `fleetwatch`, CLI `fleetwatch` with verb subcommands (`digest`, `run`, `check "Room 204"`, later `fix 3`),
  Python package `fleetwatch` (fallback `epiphan-fleetwatch` if taken), Slack bot "Fleetwatch" / `@fleetwatch`,
  sub-agents `fleetwatch-digest`, `fleetwatch-roomcheck`, `fleetwatch-fixer`.
- Dropped: "ProAV" (Epiphan writes "pro AV", two words), "Sentinel" (security tone, crowded: Microsoft Sentinel,
  SentinelOne), "Agent" in the name (filler). Avoid Guard, Warden, Watchdog, Shield, "Monitor" (a display, to AV
  people), and Pearl/Edge/EC20/Epiphan as the first word until adopted.
- Before publishing: confirm `fleetwatch` on pypi.org and npmjs.com (the collision check was web search only).

## The two peers, in one paragraph each
**OpenClaw** (openclaw/openclaw, ~392k stars, MIT, TypeScript, CalVer, docs.openclaw.ai). One gateway process,
heartbeat every 30 min as a full LLM turn that replies `NO_REPLY` or notifies, Markdown memory with hybrid
search, 20+ chat channels, skills in `SKILL.md` plus the ClawHub marketplace, exec approvals bound to exact
argv in SQLite. Install one-liner that installs launchd/systemd. Security history: an RCE via the Control UI,
approval-then-argv-swap, tens of thousands of exposed instances, 341 poisoned marketplace skills.

**Hermes Agent** (NousResearch/hermes-agent, ~252k stars, MIT, Python, date tags every 4-7 days). Gateway with
cron that runs full agent turns and routes output to a channel, four-layer memory, skills the agent writes
itself after a task, MCP client with per-server tool filtering, approvals smart/manual/off. SECURITY.md states
the trust model in one paragraph. Security history: unauthenticated API/webhook CVEs, path traversal, skills
guard bypass; plaintext credential file criticized.

## Repo-level checklist for Fleetwatch (copy these)
- README order: tagline, badges (CI, PyPI, Python, Apache-2.0), Install, Quick start, How it fits together
  (one diagram), Security, Documentation, Development, Contributing, Community, License.
- Install one-liner (`curl -fsSL .../install.sh | bash`) that installs uv if needed, runs `fleetwatch login`,
  then installs the launchd agent or systemd unit. Keep `deploy/install.sh` as the thing it calls.
- Platform matrix with named Tier 1 targets: macOS on Apple Silicon (launchd), Raspberry Pi 5 / Linux aarch64
  (systemd), Docker. Neither peer names the Pi or a Mac mini; we should.
- Top level: CONTRIBUTING.md (priority order, Conventional Commits with scopes, one concern per PR, "Evidence"
  section in the PR template, no hand-edited CHANGELOG), SECURITY.md (private advisory, 90-day disclosure,
  explicit trust model and out-of-scope list: prompt-injection-only chains, exposed-to-internet deployments),
  CODE_OF_CONDUCT.md (neither peer has one; universities ask), LICENSE, AGENTS.md (how AI assistants should
  work in the repo).
- .github: issue templates (bug, docs bug, feature), PR template, CODEOWNERS, dependabot, workflows: ci (ruff,
  pytest, replay run), codeql, dependency-audit, install-smoke on macOS and ubuntu-arm, zizmor/actionlint for
  Actions hardening, plus the redaction regression suite as its own job.
- Docs: a small docs site (MkDocs Material is enough), pages: Install, Sign-in, Policy, What it posts, Security
  model, Running on a Mac mini, Running on a Pi, Replay mode, Contributing.
- Releases: SemVer, one stable channel, GitHub Releases with notes generated from PR titles. Docker image and
  compose file. No marketplace at launch.
- Community: GitHub Discussions; Epiphan community forum once adopted. No Discord at launch.

## Technical parity (Have / Partial / Missing) and the Fleetwatch answer
| Capability | Status | Fleetwatch |
|---|---|---|
| Always-on daemon (launchd/systemd) | Have | `deploy/` |
| Heartbeat that stays quiet unless something changed | Have | diff against SQLite; post once, remind at 4 h, "Back to normal" |
| Scheduled proactive checks | Partial | pre-class readiness; add a nightly firmware/offline sweep and run history |
| Durable memory and search | Missing | per-room notes techs can write ("Room 204 HDMI flaky since Sept"), searchable incident history |
| Chat channels | Partial | Slack now; add Microsoft Teams (universities live on Teams) and an email digest; no consumer messengers |
| Two-way commands with allowlists | Missing | `/fleetwatch check 204` from Slack, allowlisted to the AV-tech group |
| Approvals bound to exact call | Have (stricter) | read-only by construction; when writes land, approval in Slack bound to the exact call, with expiry |
| Secret redaction before the model | Have | kit's rules, 0 of 36 leak |
| Secrets at rest | Partial | token file is 0600; offer macOS Keychain and `systemd-creds` |
| Multi-model with failover | Missing | optional LLM only phrases the digest; ordered provider list; Ollama on the Pi for offline phrasing |
| Skills format | Missing | adopt `SKILL.md` (agentskills.io) for rule packs and runbooks, human-written, tested offline |
| `doctor` command | Missing | checks bind, token, read-only guard, redaction, outdated deps |
| Install one-liner, Docker, docs site, CI | Missing | this sprint |
| Audit log | Partial | make the append-only actions table a headline feature; both peers are criticized for lacking one |

## Adopt / do not copy
Adopt: the SECURITY.md trust-model paragraph; the platform tier matrix; `SKILL.md` for rule packs; "approvals
suggest" (propose allowlist changes from history, never apply them); heartbeat delivery options (`showOk`,
active hours per campus timezone).

Do not copy: a full-access exec mode or any flag that removes confirmation (keep writes out of the binary, not
behind a flag); self-authoring skills and "nudge" memory writes (device names, CMS titles and on-screen text are
untrusted input, so that is a prompt-injection persistence path); the heartbeat as a free-form LLM turn (ours is
deterministic: fixed checks, SQLite diff, the model only phrases); consumer chat surfaces and an open skills
marketplace (FERPA and procurement); plaintext credential files and a network-bound API server (loopback only,
Slack outbound only).

## Suggested order for next sprint
1. Create `ScientiaCapital/fleetwatch` (private first), push, protect `main`.
2. CI: ruff, pytest, replay run, redaction suite; install-smoke on macOS and ubuntu-arm.
3. SECURITY.md, CONTRIBUTING.md, CODE_OF_CONDUCT.md, AGENTS.md, issue and PR templates, CODEOWNERS, dependabot.
4. README to the order above, with one architecture diagram.
5. Install one-liner and Docker image; docs site skeleton.
6. First live run against a real team (ask before using the shared demo team), then v0.1.0 release.
7. Then features: Teams channel, two-way `check`, nightly sweep, `doctor`, per-room notes.
