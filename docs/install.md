# Install

Fleetwatch runs on an Apple Silicon Mac or Mac mini, a Raspberry Pi 5, any Linux box with systemd, or Docker.
It needs [uv](https://docs.astral.sh/uv/) and an Epiphan Edge account that can see the team you want to watch.

## Platforms

| Tier | Platform | Runs as |
|---|---|---|
| 1 | macOS on Apple Silicon | launchd agent |
| 1 | Raspberry Pi 5 / Linux aarch64 | systemd user unit |
| 1 | Docker (amd64, arm64) | container |
| 2 | Linux x86_64, macOS on Intel | systemd / launchd |

Tier 1 targets are tested in CI on every pull request.

## Steps

```bash
git clone https://github.com/ScientiaCapital/fleetwatch.git
cd fleetwatch
uv sync
cp .env.example .env          # Slack token and channel; leave the token empty to print to the console
uv run fleetwatch login       # one-time sign-in to Epiphan Edge; see Sign-in
uv run fleetwatch digest      # one heartbeat, prints or posts the digest
deploy/install.sh             # run it as a service
```

`deploy/install.sh --dry-run` renders and checks the service file without loading anything.

## Slack

Create a Slack app with the `chat:write` scope, install it to your workspace, invite it to the channel, and put
its bot token in `.env` as `FLEETWATCH_SLACK_BOT_TOKEN`. Set `FLEETWATCH_SLACK_CHANNEL` (default `#av-ops`).
Pick a channel whose members may see room and device names.
