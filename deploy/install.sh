#!/usr/bin/env bash
# Install proav-agent as an always-on service for the current user.
#   macOS (Apple Silicon or Intel): a launchd agent, starts at login, restarts on failure.
#   Linux (Raspberry Pi 5, any systemd distro): a user systemd unit, starts at boot with lingering.
# Run from the repo folder after `uv sync`, `cp .env.example .env` and `uv run proav-agent login`.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
uv_bin="$(command -v uv || true)"
[ -n "$uv_bin" ] || { echo "uv is not installed. See https://docs.astral.sh/uv/getting-started/installation/"; exit 1; }
[ -f "$here/.env" ] || echo "note: no .env yet; the agent will print to the console instead of Slack."
if ! "$uv_bin" run --frozen proav-agent status 2>/dev/null | grep -q "Signed in: yes"; then
  echo "Not signed in yet. Run:  uv run proav-agent login   then run this script again."; exit 1
fi

case "$(uname -s)" in
  Darwin)
    plist="$HOME/Library/LaunchAgents/com.epiphan.proav-agent.plist"
    mkdir -p "$HOME/Library/LaunchAgents" "$HOME/.proav-agent"
    sed -e "s#__UV__#$uv_bin#g" -e "s#__DIR__#$here#g" -e "s#__HOME__#$HOME#g" "$here/deploy/com.epiphan.proav-agent.plist" > "$plist"
    launchctl bootout "gui/$(id -u)/com.epiphan.proav-agent" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$plist"
    echo "Installed. Logs: ~/.proav-agent/agent.log   Stop: launchctl bootout gui/$(id -u)/com.epiphan.proav-agent"
    ;;
  Linux)
    unit_dir="$HOME/.config/systemd/user"; mkdir -p "$unit_dir"
    sed -e "s#%h/proav-agent#$here#g" -e "s#%h/.local/bin/uv#$uv_bin#g" -e "s#User=%i##" -e "s#%h#$HOME#g" \
      "$here/deploy/proav-agent.service" > "$unit_dir/proav-agent.service"
    systemctl --user daemon-reload
    systemctl --user enable --now proav-agent.service
    loginctl enable-linger "$USER" 2>/dev/null || echo "note: run 'sudo loginctl enable-linger $USER' so it starts at boot without a login."
    echo "Installed. Logs: journalctl --user -u proav-agent -f   Stop: systemctl --user stop proav-agent"
    ;;
  *) echo "Unsupported OS: $(uname -s)"; exit 1 ;;
esac
