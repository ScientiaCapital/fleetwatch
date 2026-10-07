#!/usr/bin/env bash
# Install fleetwatch as an always-on service for the current user.
#   macOS (Apple Silicon or Intel): a launchd agent, starts at login, restarts on failure.
#   Linux (Raspberry Pi 5, any systemd distro): a user systemd unit, starts at boot with lingering.
# Run from the repo folder after `uv sync`, `cp .env.example .env` and `uv run fleetwatch login`.
#
#   deploy/install.sh             install and start the service
#   deploy/install.sh --dry-run   render and validate the service file only: no sign-in check, nothing loaded
set -euo pipefail

dry_run=false
case "${1:-}" in
  --dry-run) dry_run=true ;;
  "") ;;
  *) echo "usage: deploy/install.sh [--dry-run]"; exit 2 ;;
esac

label="dev.fleetwatch.agent"
here="$(cd "$(dirname "$0")/.." && pwd)"
uv_bin="$(command -v uv || true)"
[ -n "$uv_bin" ] || { echo "uv is not installed. See https://docs.astral.sh/uv/getting-started/installation/"; exit 1; }

if $dry_run; then
  out_dir="$(mktemp -d)"
  echo "Dry run: rendering into $out_dir, nothing is loaded."
else
  [ -f "$here/.env" ] || echo "note: no .env yet; the agent will print to the console instead of Slack."
  if ! "$uv_bin" run --frozen fleetwatch status 2>/dev/null | grep -q "Signed in: yes"; then
    echo "Not signed in yet. Run:  uv run fleetwatch login   then run this script again."; exit 1
  fi
fi

case "$(uname -s)" in
  Darwin)
    if $dry_run; then plist="$out_dir/$label.plist"; else
      mkdir -p "$HOME/Library/LaunchAgents" "$HOME/.fleetwatch"
      plist="$HOME/Library/LaunchAgents/$label.plist"
    fi
    sed -e "s#__UV__#$uv_bin#g" -e "s#__DIR__#$here#g" -e "s#__HOME__#$HOME#g" "$here/deploy/$label.plist" > "$plist"
    plutil -lint "$plist"
    if grep -q "__[A-Z]*__" "$plist"; then echo "unrendered placeholder in $plist"; exit 1; fi
    if $dry_run; then echo "Dry run OK: $plist"; exit 0; fi
    # Remove the agent from before the rename, if present.
    launchctl bootout "gui/$(id -u)/com.epiphan.fleetwatch" 2>/dev/null || true
    rm -f "$HOME/Library/LaunchAgents/com.epiphan.fleetwatch.plist"
    launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$plist"
    echo "Installed. Logs: ~/.fleetwatch/agent.log   Stop: launchctl bootout gui/$(id -u)/$label"
    ;;
  Linux)
    if $dry_run; then unit_dir="$out_dir"; else unit_dir="$HOME/.config/systemd/user"; mkdir -p "$unit_dir" "$HOME/.fleetwatch"; fi
    sed -e "s#%h/fleetwatch#$here#g" -e "s#%h/.local/bin/uv#$uv_bin#g" -e "/^User=%i$/d" -e "s#%h#$HOME#g" \
      "$here/deploy/fleetwatch.service" > "$unit_dir/fleetwatch.service"
    if grep -q "%[hi]" "$unit_dir/fleetwatch.service"; then echo "unrendered specifier in the unit"; exit 1; fi
    if command -v systemd-analyze >/dev/null; then
      # verify checks ExecStart exists and the unit parses; it can't check ReadWritePaths without a namespace.
      systemd-analyze --user verify "$unit_dir/fleetwatch.service"
    fi
    if $dry_run; then echo "Dry run OK: $unit_dir/fleetwatch.service"; exit 0; fi
    systemctl --user daemon-reload
    systemctl --user enable fleetwatch.service
    # restart, not start: after an update the running unit must pick up the new code.
    systemctl --user restart fleetwatch.service
    loginctl enable-linger "$USER" 2>/dev/null || echo "note: run 'sudo loginctl enable-linger $USER' so it starts at boot without a login."
    echo "Installed. Logs: journalctl --user -u fleetwatch -f   Stop: systemctl --user stop fleetwatch"
    ;;
  *) echo "Unsupported OS: $(uname -s)"; exit 1 ;;
esac
