#!/usr/bin/env bash
# Install fleetwatch as an always-on service for the current user.
#   macOS (Apple Silicon or Intel): a launchd agent, starts at login, restarts on failure.
#   Linux (Raspberry Pi 5, any systemd distro): a user systemd unit, starts at boot with lingering.
# Run from the repo folder after `uv sync`, `cp .env.example .env` and `uv run fleetwatch login`.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
uv_bin="$(command -v uv || true)"
[ -n "$uv_bin" ] || { echo "uv is not installed. See https://docs.astral.sh/uv/getting-started/installation/"; exit 1; }
[ -f "$here/.env" ] || echo "note: no .env yet; the agent will print to the console instead of Slack."
if ! "$uv_bin" run --frozen fleetwatch status 2>/dev/null | grep -q "Signed in: yes"; then
  echo "Not signed in yet. Run:  uv run fleetwatch login   then run this script again."; exit 1
fi

case "$(uname -s)" in
  Darwin)
    plist="$HOME/Library/LaunchAgents/com.epiphan.fleetwatch.plist"
    mkdir -p "$HOME/Library/LaunchAgents" "$HOME/.fleetwatch"
    sed -e "s#__UV__#$uv_bin#g" -e "s#__DIR__#$here#g" -e "s#__HOME__#$HOME#g" "$here/deploy/com.epiphan.fleetwatch.plist" > "$plist"
    launchctl bootout "gui/$(id -u)/com.epiphan.fleetwatch" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$plist"
    echo "Installed. Logs: ~/.fleetwatch/agent.log   Stop: launchctl bootout gui/$(id -u)/com.epiphan.fleetwatch"
    ;;
  Linux)
    unit_dir="$HOME/.config/systemd/user"; mkdir -p "$unit_dir"
    sed -e "s#%h/fleetwatch#$here#g" -e "s#%h/.local/bin/uv#$uv_bin#g" -e "s#User=%i##" -e "s#%h#$HOME#g" \
      "$here/deploy/fleetwatch.service" > "$unit_dir/fleetwatch.service"
    systemctl --user daemon-reload
    systemctl --user enable --now fleetwatch.service
    loginctl enable-linger "$USER" 2>/dev/null || echo "note: run 'sudo loginctl enable-linger $USER' so it starts at boot without a login."
    echo "Installed. Logs: journalctl --user -u fleetwatch -f   Stop: systemctl --user stop fleetwatch"
    ;;
  *) echo "Unsupported OS: $(uname -s)"; exit 1 ;;
esac
