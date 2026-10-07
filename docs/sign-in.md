# Sign-in

```bash
uv run fleetwatch login
```

`login` prints a sign-in link and opens it when there is a browser. Sign in with your Epiphan Edge account and pick
the team to watch. Fleetwatch sees only what your account sees in that team.

- **Headless machine (a Pi over SSH):** open the link on any device. If the final `localhost` page can't load,
  copy its URL from the address bar and paste it into the terminal.
- **Europe or Australia:** set `FLEETWATCH_EPIPHAN_MCP_URL` to `https://eu.epiphan.cloud/mcp` or
  `https://au.epiphan.cloud/mcp` in `.env` before you sign in.
- The token lives in `~/.fleetwatch/epiphan-oauth.json` with mode `600` and refreshes itself.
- `uv run fleetwatch status` shows whether you are signed in. `uv run fleetwatch logout` deletes the token.

Epiphan's MCP server reports an expired or missing sign-in inside the tool result rather than as an HTTP 401, so
`login` starts the OAuth flow itself.
