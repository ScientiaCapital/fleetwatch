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
uv run fleetwatch connect     # guided first run: region, sign-in to Epiphan Edge, device check; see Sign-in
uv run fleetwatch digest      # one heartbeat, prints or posts the digest
deploy/install.sh             # run it as a service
```

`deploy/install.sh --dry-run` renders and checks the service file without loading anything.

## Updating

Run the install line again. It moves `~/fleetwatch` to the newest release, installs its dependencies, and restarts
the service. Your `.env`, sign-in, and history stay put: the SQLite state lives in `~/.fleetwatch`, and the sign-in
token in the macOS Keychain or that same folder, all outside the code folder.

```bash
curl -fsSL https://raw.githubusercontent.com/ScientiaCapital/fleetwatch/main/install.sh | bash
```

To pick a version, add `-s -- --ref v0.1.0` (any release tag, or `main` for the development branch). If you
cloned the repo yourself, as in the steps above, run `git pull && uv sync` in its folder, then `deploy/install.sh`
to restart the service.

On Docker, see [Updating the image](docker.md#updating-the-image).

Afterwards, `fleetwatch doctor` shows the version on its first line. Each release on
[GitHub Releases](https://github.com/ScientiaCapital/fleetwatch/releases) lists what changed.

## Slack

Create a Slack app with the `chat:write` scope, install it to your workspace, invite it to the channel, and put
its bot token in `.env` as `FLEETWATCH_SLACK_BOT_TOKEN`. Set `FLEETWATCH_SLACK_CHANNEL` (default `#av-ops`).
Pick a channel whose members may see room and device names.

To ask about a room from Slack (`/fleetwatch check Main Stage`), see [Slack commands](slack-commands.md).

## Microsoft Teams

Fleetwatch can post the same digest to a Teams channel, alongside Slack or instead of it. It uses a Power
Automate Workflows webhook (the replacement for the retired Office 365 connectors):

1. In Teams, open the channel, choose ⋯ → Workflows, and pick the template "Post to a channel when a webhook
   request is received".
2. Name it (for example "Fleetwatch"), choose the team and channel, and finish. Teams shows a URL; copy it.
3. Put it in `.env` as `FLEETWATCH_TEAMS_WEBHOOK_URL` and restart the service. `fleetwatch doctor` shows
   `Teams  configured`.

Each post arrives as a card from the Workflows bot, with the same bold headings and bullets as in Slack.

The URL is a password: anyone who has it can post to the channel. Keep it in `.env` only, never in a ticket or a
chat. Fleetwatch never logs or prints it, and `doctor` only says whether it's set. If it leaks, delete the
workflow in Power Automate and create a new one.

With both Slack and Teams set, every post goes to both. If one is down the other still gets it, and the missed
post isn't repeated later. With neither set, Fleetwatch prints to the console.
