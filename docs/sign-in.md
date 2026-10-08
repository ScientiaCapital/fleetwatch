# Sign-in

```bash
uv run fleetwatch login
```

`login` prints a sign-in link and opens it when there is a browser. Sign in with your Epiphan Edge account and pick
the team to watch. Fleetwatch sees only what your account sees in that team.

- **Headless machine (a Pi over SSH):** open the link on any device. If the final `127.0.0.1` page can't load,
  copy its URL from the address bar and paste it into the terminal.
- **Europe or Australia:** set `FLEETWATCH_EPIPHAN_MCP_URL` to `https://eu.epiphan.cloud/mcp` or
  `https://au.epiphan.cloud/mcp` in `.env` before you sign in.
- The token refreshes itself. On a Mac it lives in the login Keychain; on Linux with systemd 256 or later it is
  encrypted with `systemd-creds`; anywhere else (Docker included) it is `~/.fleetwatch/epiphan-oauth.json` with mode
  `600`. Set `FLEETWATCH_TOKEN_STORE` to `file`, `keychain` or `systemd-creds` to choose; `fleetwatch doctor` shows
  which one is in use. A Mac that runs Fleetwatch while nobody is logged in can't open the login Keychain;
  use `FLEETWATCH_TOKEN_STORE=file` there.
- `uv run fleetwatch status` shows whether you are signed in. `uv run fleetwatch logout` deletes the token.
- The sign-in redirect goes to `http://127.0.0.1:8765/callback` (the loopback address, not `localhost`). If a
  version before this change signed you in and `login` now fails with a redirect error, run `fleetwatch logout`
  first so Fleetwatch registers again with the new address. A running agent keeps refreshing its token either way.

Epiphan's MCP server reports an expired or missing sign-in inside the tool result rather than as an HTTP 401, so
`login` starts the OAuth flow itself.

The token refreshes before it runs out, across restarts too: its expiry is saved next to it, and
`fleetwatch doctor` shows it (never the token). If Epiphan still says the sign-in expired, Fleetwatch refreshes
once and retries the read once. If that fails too, run `fleetwatch login` again.
