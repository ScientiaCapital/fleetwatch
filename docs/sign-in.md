# Sign-in

```bash
uv run fleetwatch login
```

`login` prints a sign-in link and opens it when there is a browser. Sign in with your Epiphan Edge account and pick
the team to watch. Fleetwatch sees only what your account sees in that team.

- Headless machine (a Pi over SSH): sign in from a laptop through an SSH tunnel, so the last page reaches the
  machine directly: `ssh -L 8765:127.0.0.1:8765 user@machine`, then run `fleetwatch login` in that session and
  open the link in the laptop's browser. Without a tunnel, open the link on any device and, when the final
  `127.0.0.1` page can't load, copy its URL from the address bar and paste it into the terminal.
- Europe or Australia: set `FLEETWATCH_EPIPHAN_MCP_URL` to `https://eu.epiphan.cloud/mcp` or
  `https://au.epiphan.cloud/mcp` in `.env` before you sign in.
- The token refreshes itself. On a Mac it lives in the login Keychain; on Linux with systemd 256 or later it's
  encrypted with `systemd-creds`; anywhere else (Docker included) it's `~/.fleetwatch/epiphan-oauth.json` with mode
  `600`. Set `FLEETWATCH_TOKEN_STORE` to `file`, `keychain`, or `systemd-creds` to choose; `fleetwatch doctor` shows
  which one is in use. A Mac that runs Fleetwatch while nobody is logged in can't open the login Keychain;
  use `FLEETWATCH_TOKEN_STORE=file` there. Whichever store is in use, `~/.fleetwatch/epiphan-oauth.lock` is an
  empty file the processes on one machine (`run`, a cron `digest`) take turns on, so only one refreshes at a time.
- `uv run fleetwatch status` shows whether you are signed in. `uv run fleetwatch logout` asks Epiphan to revoke
  the token (RFC 7009) when Epiphan offers that, then deletes it from this machine either way. It tells you which
  happened: "Epiphan revoked the token", or "Signed out on this machine" when Epiphan doesn't offer revocation or
  didn't confirm it. In that case a copy of the token taken earlier keeps working until it expires.
- The sign-in redirect goes to `http://127.0.0.1:8765/callback` (the loopback address, not `localhost`). If a
  version before this change signed you in and `login` now fails with a redirect error, run `fleetwatch logout`
  first so Fleetwatch registers again with the new address. A running agent keeps refreshing its token either way.

Epiphan's MCP server reports an expired or missing sign-in inside the tool result rather than as an HTTP 401, so
`login` starts the OAuth flow itself.

The token refreshes before it runs out, across restarts too: its expiry is saved next to it, and
`fleetwatch doctor` shows it (never the token). If Epiphan still says the sign-in expired, Fleetwatch refreshes
once and retries the read once. If that fails too, run `fleetwatch login` again.

`login` also saves where Epiphan's sign-in endpoints are (no secrets), so a refresh after a restart goes to the
right place. A token saved by an older version has no endpoints yet: the log says so once, and the next
`fleetwatch login` saves them.

## Use a least-access account

Epiphan Edge has no read-only sign-in. The token Fleetwatch keeps can do whatever your Edge account can in the team
you picked, including reboots and firmware updates on plans that allow them. Fleetwatch never calls those tools,
but anyone who copies the token could. So:

- Sign in with an account made for Fleetwatch, with the least access that still sees the rooms you watch, in a
  team that holds only those devices.
- Treat the token store like a password: run Fleetwatch as its own user, and don't copy `~/.fleetwatch` around.
- `fleetwatch doctor` shows an INFO line, "Sign-in can write", as a reminder, with the granted scope when Epiphan
  reports one.

## Sandbox sign-in (for v0.2)

```bash
uv run fleetwatch login --sandbox
```

This is for v0.2, the assistant that proposes changes a person approves
([design](design/approved-writes.md)). It needs a sandbox team: an Edge team that holds only test devices. Don't
use it with the team you watch.

- It's the same sign-in flow as `fleetwatch login`. Pick the sandbox team when Epiphan asks.
- The token is kept apart from the normal one: `FLEETWATCH_SANDBOX_TOKEN_FILE` (default
  `~/.fleetwatch/epiphan-sandbox-oauth.json`), or its own Keychain or `systemd-creds` entry. It must be a different
  file from `FLEETWATCH_TOKEN_FILE`. `FLEETWATCH_EPIPHAN_TOKEN` is never used for it.
- Only the write executor loads it. The heartbeat, `digest`, `ask` and Slack commands keep using the normal sign-in.
- Before an approved change runs, Fleetwatch reads the sandbox team's device list with this sign-in and refuses
  the change if any target isn't on it. It also refuses any target that isn't in `FLEETWATCH_WRITE_DEVICE_IDS`
  (the sandbox devices you list, comma-separated). If `FLEETWATCH_WRITE_TEAM_ID` is set, it also refuses when
  Epiphan reports a different team, or no team ID at all. With neither set, every change is refused, so a sign-in
  that lands on the wrong team can't write to it.
- List only devices that exist on the sandbox team, as master device IDs (8 to 32 characters, 0-9 and a-f). The check
  proves a device is on the signed-in team's list, not that the team is the sandbox, so a device that also exists
  on a real team would pass. `fleetwatch doctor` flags entries that aren't device IDs, and warns when only a team ID
  is set.
- `fleetwatch login --sandbox` forgets the sign-in and stops if the sandbox lists no devices, or can see a device
  the normal sign-in already watches, because that means both reach the same team. If the normal sign-in has no
  saved devices yet, it warns that the two can't be compared: run one `fleetwatch digest` first.
- With no sandbox sign-in, no change can run. `fleetwatch doctor` shows "Sandbox sign-in: none, so no change can
  run".
- `uv run fleetwatch logout --sandbox` signs out of the sandbox only. The normal sign-in stays.

This version has no approval page yet, so nothing uses the sandbox sign-in.

## When the sign-in can't be refreshed

If Epiphan refuses the refresh token (it was revoked, or the account changed), restarting won't help. Fleetwatch
logs `Sign-in expired: run fleetwatch login`, remembers it, and `fleetwatch run` exits with code 78.
`fleetwatch doctor` shows "sign in again". Run `fleetwatch login`, then start the service again.

- Linux (systemd): the unit has `RestartPreventExitStatus=78`, so it stays stopped until you sign in and run
  `systemctl --user restart fleetwatch`.
- Mac (launchd): launchd can't skip one exit code. `KeepAlive` → `SuccessfulExit = false` restarts on any
  non-zero exit, and `Crashed` only covers crashes. The agent is started again every 30 seconds, sees the saved
  "sign in again" mark, logs the line, and exits at once, without contacting Epiphan. After `fleetwatch login`
  the next start works on its own.
- Docker: `restart: unless-stopped` behaves like launchd. Sign in again with
  `docker compose run --rm fleetwatch login`, or `docker compose stop` until you can.
