<p align="right">English · <a href="SECURITY.es.md">Español</a></p>

# Security

Fleetwatch watches classroom and studio video gear for a whole campus or company, so a bug that leaks a stream
key or lets it change a device matters. Thank you for reporting one privately.

## Report a vulnerability

Please don't open a public issue. Report it through GitHub:
Security → Report a vulnerability on this repo
([direct link](https://github.com/ScientiaCapital/fleetwatch/security/advisories/new)).

- You get a first reply within 5 business days.
- We aim to ship a fix within 90 days and publish an advisory when it lands. If a fix takes longer, we agree a
  date with you. Please hold public disclosure until then, or 90 days after your report, whichever comes first.
- We credit you in the advisory unless you ask us not to.

Problems in Epiphan products, Epiphan Edge, or the Epiphan MCP server belong to Epiphan. Contact
[Epiphan support](https://www.epiphan.com/support/).

## Trust model

Fleetwatch runs as one process on a machine you control, as your user, with one Epiphan Edge sign-in for one
team. It trusts that machine, its user account, `policy.yaml`, `tool_policy.yaml`, and your Slack workspace. It
doesn't trust anything that comes back from Epiphan: device names, channel and source names, CMS event
titles, and on-screen text can be typed by anyone with access to a room or a CMS, so they are treated as data,
never as instructions. In Slack they are escaped, so a device named `<!channel>` can't ping anyone or post a link. It listens on no network port except `127.0.0.1`: for the few seconds of `fleetwatch
login`, and while you run `fleetwatch ask --serve` (opt-in, answers only requests addressed to localhost, reads
the local state and never calls Epiphan). It talks only outward: to your Epiphan region and, if configured, to
Slack or Teams, and to the Anthropic API when `FLEETWATCH_ANTHROPIC_API_KEY` is set (see
[Data sent to the model](#data-sent-to-the-model-v02-in-progress)). Questions typed into `ask` are untrusted text too: they pick a fixed answer and a known room, nothing more.

Where the real boundary is. The read-only guard and redaction run inside the Fleetwatch process. They stop
Fleetwatch's own code from writing or leaking, but they don't contain someone who controls that process or its
files. On an Epiphan Edge paid plan, the stored OAuth token can make changes to devices, just like
the account it belongs to. The boundaries that hold against an attacker are the operating-system user that runs
Fleetwatch and the permissions of the Edge account you signed in with. So run Fleetwatch as its own user on a
machine you trust, and sign in with an Edge account that has the least access that still sees the rooms you
watch.

No read-only sign-in. Epiphan Edge's OAuth has no read-only scope. Fleetwatch asks for no scope, so it gets
what Epiphan grants the account, and the token is a plain bearer token: anyone holding it, or the refresh token
stored with it, can call write tools from anywhere until it expires or is revoked. Read-only is a promise about
Fleetwatch's code, not about the credential. `fleetwatch doctor` shows this as an INFO line on every run, with the
granted scope when Epiphan reports one. When Epiphan offers a read-only scope, Fleetwatch will ask for it and
refuse anything wider.

## How that's enforced in v0.1

- Refuses write tools in code. The client guard (`src/fleetwatch/epiphan/mcp.py`) refuses any tool not on the
  `read` list in `tool_policy.yaml` before a request leaves the machine, including tools Epiphan adds later.
  `policy.yaml` accepts `autonomy: observe` or `propose` (`propose` is for the v0.2 assistant; the guard refuses
  every write tool in both modes, and v0.2 writes use a separate executor, below), and `dry_run` is forced to `true`; any other autonomy value fails at start-up.
- Redaction first. Every tool result passes through `src/fleetwatch/redact.py` before it's parsed, stored,
  logged, or posted. Known shapes of stream keys, passwords, tokens, and credentialed or ingest URLs become
  `[redacted]`.
- No model call by default. Every digest, readiness post and keyword answer is built from fixed templates. Only
  `fleetwatch ask` with `FLEETWATCH_ANTHROPIC_API_KEY` set calls a model (v0.2, in progress); see
  [Data sent to the model](#data-sent-to-the-model-v02-in-progress).
- Token at rest. The Epiphan OAuth token refreshes itself, so it lives in a store Fleetwatch can write
  (`FLEETWATCH_TOKEN_STORE`, default `auto`):
  - macOS: the login Keychain, service `fleetwatch-epiphan`. Fleetwatch writes it with `security -i`, sending
    the token hex-encoded on stdin, so it never appears in a process's arguments (`ps`). `security -i` reads at
    most 4095 characters a line, so the token is split across a few items (`part-0`, `part-1`, ...).
  - Linux with systemd 256 or later: `~/.fleetwatch/epiphan-oauth.cred`, encrypted with
    `systemd-creds encrypt --user`, so only this user on this machine can decrypt it. Plain text goes in and out on
    stdin and stdout. (`LoadCredential` is read-only, and the token has to be rewritten when it refreshes.)
  - Everywhere else, and in Docker: `~/.fleetwatch/epiphan-oauth.json` with mode `600`.

  A token already in the file moves to the Keychain or systemd-creds the first time it's used, and the file is
  deleted. The Keychain items trust `/usr/bin/security`, so another program running as the same macOS user can
  read them without a prompt; on a shared Mac, run Fleetwatch as its own user. `fleetwatch logout` asks Epiphan to revoke the token (RFC 7009, https only) when Epiphan's sign-in settings
  list a revocation endpoint, then deletes the token from whichever store holds it, even if revocation fails. `fleetwatch doctor` says which
  store is in use, and on a Mac it adds a Keychain access line as a reminder.
- Service hardening. The systemd unit sets `NoNewPrivileges`, `ProtectSystem=strict`, and `PrivateTmp`, with
  write access only to its own state folder and virtual environment.
- CI. The redaction and guard suites run as their own job on every PR, alongside CodeQL, pip-audit,
  dependency review, actionlint, and zizmor.

## Data sent to the model (v0.2, in progress)

The v0.2 assistant (docs/design/approved-writes.md) is optional and off by default. It's tested only against a
mocked model so far, not the live Anthropic API or a real team.

- When. Only `fleetwatch ask` (not `--serve`, not `--no-ai`), and only when `FLEETWATCH_ANTHROPIC_API_KEY` is set.
  The heartbeat, digests, readiness posts and Slack commands never call a model. `fleetwatch doctor` shows
  "Assistant: off (no API key)", or "on", the model, and a masked key.
- What goes to Anthropic. The question, and the results of the read tools the model asks for, after `redact()`.
  Those results include device, channel, source and event names, models, groups, firmware, and online, recording
  and event status. Pictures from `get_channel_image` are never sent. Each string, list and result is capped, and a
  cut result says so.
- Names are data. Fleet text goes to the model inside one `<fleet_data>` block per result, as JSON with `<`, `>` and
  `&` escaped, so a name can't close the block or pose as a tag. The system prompt says the block's contents are
  data, never instructions. That's a mitigation, not the boundary: the model is untrusted.
- What the model can do. Call the read tools, through the same `guard()`, and `propose_change`. It never sees a
  write tool, and a tool name it invents is answered with an error, never called. `propose_change` only stores a
  pending proposal, and only when `policy.yaml` says `autonomy: propose`. Code refuses it unless the tool is listed
  under `propose` with a reviewed schema, the arguments pass the same `check_arguments()` the executor uses, there
  are no more targets than `max_targets`, and every target (a device ID, or a channel ID for `batch_recording`) is
  on a fresh read of the sandbox team, through the sandbox sign-in (`fleetwatch login --sandbox`). The state
  fingerprint comes from that same read, so it matches what the executor checks again before a change runs. With no
  sandbox sign-in, every proposal is refused, and questions are still answered through the normal sign-in.
  The assistant itself never approves or runs a proposal.
- Limits. At most 6 model calls and 120,000 tokens per question, and a 60-second timeout.
- If it fails. No key, an API error, a refusal or a timeout: `ask` gives the keyword answer, with a note that says
  why (for example "the API returned an error"). The audit row records the error's class name. A question that fails
  partway expires any proposal it made.
- What's kept. Prompts and responses aren't logged or written to disk. The audit table gets one row per question:
  its length, the tool names called, proposal IDs, and token counts. The Anthropic SDK's logger stays at WARNING,
  even with `-v`, because its debug lines carry request bodies.
- Turning it off. Leave `FLEETWATCH_ANTHROPIC_API_KEY` empty. Anthropic's own data handling for API traffic applies
  to what is sent.

## v0.2 trust model

This covers the optional assistant and the approval flow. They're built but not released, and tested only against
fakes and mocks: a fake Epiphan server and a mocked model. Nothing here has run against a real team or the live
Anthropic API. Version 0.1 doesn't use any of it. The design is in
[docs/design/approved-writes.md](docs/design/approved-writes.md).

What the model can see. Your question, and redacted results from the read tools it asks for. Those results include
device, channel, source and event names, models, groups, firmware, and online, recording and event status. They go to
the Anthropic API. Redaction removes known secret shapes, not every possible secret, so see
[Known limits](#known-limits).

What the model can do. Read, through the same `guard()` as everything else, and call `propose_change`. That tool
stores a pending proposal and runs nothing. The model never sees a write tool. It's untrusted: a prompt that
persuades it to propose something still meets every check below.

What a person approves. One change at a time, on the approval page (`fleetwatch approve --serve`, see
[Approving changes](docs/approving-changes.md)). Code builds the card from the stored proposal and a fresh read of
each target: the tool in plain words, each device's ID, name and state, and every argument in full. The assistant's
reason appears in a separate box marked as not checked. If an argument can't be shown in full, only Deny is offered.

What stops a change from running without that approval:

- Approval is single-use and expires after five minutes. It's bound to the exact tool, arguments and targets, so a
  changed argument or a reused approval is refused.
- Writes go only through a separate executor, never through the normal client. The executor takes only a consumed
  approval, and re-reads each target right before the call. It refuses if a read fails or the target's state changed.
  It calls the tool once and never retries, so an unknown outcome is shown as "may or may not have run".
- Sandbox fence. Writes use a second sign-in, made with `fleetwatch login --sandbox`, and every target must be on that
  sign-in's own fresh device list and on `FLEETWATCH_WRITE_DEVICE_IDS`, the sandbox devices you list. With no sandbox
  sign-in, or with neither that list nor `FLEETWATCH_WRITE_TEAM_ID` set, no write can run. `login --sandbox` is
  refused, and the sign-in forgotten, if the sandbox lists no devices or can see a device the normal sign-in already watches. The
  heartbeat and the read-only assistant never load the sandbox token.
- Disruptive tools (reboot, firmware update, team preset, stopping or deleting a stream endpoint, deleting an event,
  and tools not yet reviewed) are refused near a scheduled event or while a target is recording, even with approval.

The approval page:

- It listens on `127.0.0.1` only, on its own port, and checks the Host header.
- A page secret, never put in a URL, gates it. In a terminal it prints once. Under launchd or systemd, where the
  console is a log, it goes to a file only you can read (mode 0600) in the state folder, and only the path prints. The
  file is deleted when the page stops cleanly and replaced at the next start.
- Wrong secrets slow the form down instead of locking it: three free tries, then a wait that doubles up to a minute,
  counted per client address, plus a much higher limit across all addresses. Entries made during a wait aren't
  checked and don't lengthen it, so a guesser can't keep you out. The page and `fleetwatch doctor` say when the
  proposal queue is full, and "Deny all pending" denies everything waiting. There is no approve-all.
- Every POST needs a token bound to the proposal and a same-origin header. Nothing changes on a GET.
- Approve is never the focused button, and the assistant can't set labels, colors or focus.

Known limits:

- Browsers send cookies to every port on the same host. Another local web server on `localhost` could receive the
  page's session cookie. Use `127.0.0.1`, and don't browse other local web servers while the page is open.
- The page suggests a break after five approvals in a session. That's a warning, not a hard limit, and a person can
  still approve through it. The audit log counts approvals so a rubber-stamping pattern shows.
- Epiphan's OAuth has no read-only scope. The sandbox sign-in's token can write to the sandbox team, and the normal
  sign-in's token can write to its team. The fence is Fleetwatch's code, not the credential. If someone controls the
  process or its files, they hold a token that can write.
- Whether Epiphan returns a team ID, and whether each proposable tool takes the arguments in `tool_policy.yaml`, stay
  unconfirmed until the first live run. Today the sandbox check is the device list.
- Every local process reaches the page from the same address, 127.0.0.1, so the per-address wait can't tell you from a
  program that is guessing. It limits guessing; it doesn't stop a local program from slowing your sign-in. The wait
  never passes a minute, and restarting the page makes a new secret and clears it.
- The checks before a change run, then the write is sent. They are best effort at one instant: a person can press
  record between the last read and the write. Fleetwatch keeps that gap to the length of one call and does no other
  work in it, but can't close it.
- The model can write a misleading reason or answer. Read the card, not the reason.

## In scope

- A write tool, or any tool not on the `read` list, being called.
- A secret reaching a log, the SQLite state file, Slack, or the console.
- Fleetwatch accepting a network connection beyond the login callback, or the callback accepting more than the
  OAuth redirect.
- The token file being created readable by other users.
- Untrusted device or CMS text changing what Fleetwatch does (rather than just what a message says).

## Out of scope

- Attacks that need control of the machine or user account Fleetwatch runs as.
- Deployments that expose the host or its login callback to the internet against the docs.
- Prompt-injection-only chains with no effect beyond the wording of a message. Injection that leads to a proposal, or past an approval or the sandbox fence, is in scope.
- Odd text in a digest because a device or event was given an odd name.
- Vulnerabilities in Epiphan's services or in Slack.

## Known limits

- Redaction recognizes the secret field names Epiphan uses today and common shapes in text. A secret written
  some other way may not be caught. If you find one, add a case to `tests/redaction-cases.json` (shared
  byte-for-byte with the Epiphan Edge Claude Kit, so change both repos) and make `tests/test_redact.py` pass.
- On the file store (Docker, Linux before systemd 256), the token is plain JSON protected only by file
  permissions.
- Slack messages name rooms and devices. Pick a channel whose members may see that.
