# Security

Fleetwatch watches classroom and studio video gear for a whole campus or company, so a bug that leaks a stream
key or lets it change a device matters. Thank you for reporting one privately.

## Report a vulnerability

Please **don't open a public issue**. Report it through GitHub:
**Security → Report a vulnerability** on this repo
([direct link](https://github.com/ScientiaCapital/fleetwatch/security/advisories/new)).

- You get a first reply within 5 business days.
- We aim to ship a fix within 90 days and publish an advisory when it lands. If a fix takes longer, we agree a
  date with you. Please hold public disclosure until then, or 90 days after your report, whichever comes first.
- We credit you in the advisory unless you ask us not to.

Problems in Epiphan products, Epiphan Edge or the Epiphan MCP server belong to Epiphan. Contact
[Epiphan support](https://www.epiphan.com/support/).

## Trust model

Fleetwatch runs as one process on a machine you control, as your user, with one Epiphan Edge sign-in for one
team. It trusts that machine, its user account, `policy.yaml`, `tool_policy.yaml` and your Slack workspace. It
does **not** trust anything that comes back from Epiphan: device names, channel and source names, CMS event
titles and on-screen text can be typed by anyone with access to a room or a CMS, so they are treated as data,
never as instructions. In Slack they are escaped, so a device named `<!channel>` can't ping anyone or post a link. It listens on no network port except `127.0.0.1`: for the few seconds of `fleetwatch
login`, and while you run `fleetwatch ask --serve` (opt-in, answers only requests addressed to localhost, reads
the local state and never calls Epiphan). It talks only outward: to your Epiphan region and, if configured, to
Slack. Questions typed into `ask` are untrusted text too: they pick a fixed answer and a known room, nothing more.

**Where the real boundary is.** The read-only guard and redaction run inside the Fleetwatch process. They stop
Fleetwatch's own code from writing or leaking, but they don't contain someone who controls that process or its
files. On an Epiphan Edge paid plan, the stored OAuth token can make changes to devices, just like
the account it belongs to. The boundaries that hold against an attacker are the operating-system user that runs
Fleetwatch and the permissions of the Edge account you signed in with. So run Fleetwatch as its own user on a
machine you trust, and sign in with an Edge account that has the least access that still sees the rooms you
watch.

## How that is enforced in v0.1

- **Read-only by construction.** The client guard (`src/fleetwatch/epiphan/mcp.py`) refuses any tool not on the
  `read` list in `tool_policy.yaml` before a request leaves the machine, including tools Epiphan adds later.
  `policy.yaml` is forced to `autonomy: observe` and `dry_run: true`; any other value fails at start-up.
- **Redaction first.** Every tool result passes through `src/fleetwatch/redact.py` before it is parsed, stored,
  logged or posted. Stream keys, passwords, tokens and credentialed or ingest URLs become `[redacted]`.
- **No model call.** v0.1 builds every message from fixed templates and has no AI model dependency. A later
  feature that uses a model will get its own section here first.
- **Token at rest.** The Epiphan OAuth token refreshes itself, so it lives in a store Fleetwatch can write
  (`FLEETWATCH_TOKEN_STORE`, default `auto`):
  - **macOS:** the login Keychain, service `fleetwatch-epiphan`. Fleetwatch writes it with `security -i`, sending
    the token hex-encoded on stdin, so it never appears in a process's arguments (`ps`). `security -i` reads at
    most 4095 characters a line, so the token is split across a few items (`part-0`, `part-1`, ...).
  - **Linux with systemd 256 or later:** `~/.fleetwatch/epiphan-oauth.cred`, encrypted with
    `systemd-creds encrypt --user`, so only this user on this machine can decrypt it. Plain text goes in and out on
    stdin and stdout. (`LoadCredential` is read-only, and the token has to be rewritten when it refreshes.)
  - **Everywhere else, and in Docker:** `~/.fleetwatch/epiphan-oauth.json` with mode `600`.

  A token already in the file moves to the Keychain or systemd-creds the first time it is used, and the file is
  deleted. `fleetwatch logout` deletes the token from whichever store holds it. `fleetwatch doctor` says which
  store is in use.
- **Service hardening.** The systemd unit sets `NoNewPrivileges`, `ProtectSystem=strict` and `PrivateTmp`, with
  write access only to its own state folder and virtual environment.
- **CI.** The redaction and guard suites run as their own job on every PR, alongside CodeQL, pip-audit,
  dependency review, actionlint and zizmor.

## In scope

- A write tool, or any tool not on the `read` list, being called.
- A secret reaching a log, the SQLite state file, Slack or the console.
- Fleetwatch accepting a network connection beyond the login callback, or the callback accepting more than the
  OAuth redirect.
- The token file being created readable by other users.
- Untrusted device or CMS text changing what Fleetwatch does (rather than just what a message says).

## Out of scope

- Attacks that need control of the machine or user account Fleetwatch runs as.
- Deployments that expose the host or its login callback to the internet against the docs.
- Prompt-injection-only chains with no effect beyond the wording of a message: v0.1 sends nothing to a model.
- Odd text in a digest because a device or event was given an odd name.
- Vulnerabilities in Epiphan's services or in Slack.

## Known limits

- Redaction recognises the secret field names Epiphan uses today and common shapes in text. A secret written
  some other way may not be caught. Add a case to `tests/test_redact.py` if you find one.
- On the file store (Docker, Linux before systemd 256), the token is plain JSON protected only by file
  permissions.
- Slack messages name rooms and devices. Pick a channel whose members may see that.
