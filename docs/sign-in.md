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
- `uv run fleetwatch status` shows whether you are signed in. `uv run fleetwatch logout` deletes the token.
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
