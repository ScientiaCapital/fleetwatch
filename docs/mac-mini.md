# Running on a Mac mini

Any Apple Silicon Mac works. A Mac mini on the AV office network is the usual choice.

```bash
uv sync && uv run fleetwatch login
deploy/install.sh
```

The installer writes a launchd agent to `~/Library/LaunchAgents/dev.fleetwatch.agent.plist`. It starts at login
and restarts after a failure.

| Task | Command |
|---|---|
| Logs | `tail -f ~/.fleetwatch/agent.log` |
| Stop | `launchctl bootout gui/$(id -u)/dev.fleetwatch.agent` |
| Start again | `deploy/install.sh` |

Something not working? Run `uv run fleetwatch doctor` in the Fleetwatch folder first. It checks the policy, the
read-only guard, redaction, the sign-in, the network route to Epiphan and the launchd agent.

A launchd agent runs while that user is logged in. For an unattended Mac mini, turn on automatic login for the
account that runs Fleetwatch, and stop the Mac from sleeping.
