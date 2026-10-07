#!/usr/bin/env bash
# Fleetwatch for Epiphan Edge: one-line install for macOS (launchd) and Linux (systemd).
#
#   curl -fsSL https://raw.githubusercontent.com/ScientiaCapital/fleetwatch/main/install.sh | bash
#
# What it does, in order:
#   1. installs uv if it is missing (from astral.sh)
#   2. clones or updates Fleetwatch in ~/fleetwatch (change with --dir)
#   3. installs the locked dependencies and creates .env from .env.example
#   4. runs `fleetwatch login` to sign in to Epiphan Edge
#   5. installs the always-on service with deploy/install.sh
#
# Options: --dir PATH   --ref BRANCH_OR_TAG   --no-login   --no-service   --dry-run (service file only, nothing loaded)
# Read it first if you like: https://github.com/ScientiaCapital/fleetwatch/blob/main/install.sh
set -euo pipefail

repo="${FLEETWATCH_REPO:-https://github.com/ScientiaCapital/fleetwatch.git}"
dir="${FLEETWATCH_DIR:-$HOME/fleetwatch}"
ref="main"
do_login=true
do_service=true
dry_run=false

while [ $# -gt 0 ]; do
  case "$1" in
    --dir) dir="$2"; shift 2 ;;
    --ref) ref="$2"; shift 2 ;;
    --no-login) do_login=false; shift ;;
    --no-service) do_service=false; shift ;;
    --dry-run) dry_run=true; shift ;;
    -h|--help) sed -n '2,15p' "$0" 2>/dev/null || true; exit 0 ;;
    *) echo "unknown option: $1"; exit 2 ;;
  esac
done

say() { printf '\n==> %s\n' "$*"; }

case "$(uname -s)" in
  Darwin|Linux) ;;
  *) echo "Fleetwatch installs on macOS or Linux. On other systems, use Docker: see the README."; exit 1 ;;
esac
command -v git >/dev/null || { echo "git is required. Install it and run this again."; exit 1; }

if ! command -v uv >/dev/null; then
  say "Installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

if [ -d "$dir/.git" ]; then
  say "Updating $dir"
  git -C "$dir" fetch --quiet origin "$ref"
  git -C "$dir" checkout --quiet "$ref"
  git -C "$dir" merge --quiet --ff-only FETCH_HEAD
else
  say "Downloading Fleetwatch to $dir"
  git clone --quiet --branch "$ref" "$repo" "$dir"
fi
cd "$dir"

say "Installing dependencies"
uv sync --frozen --no-dev
[ -f .env ] || { cp .env.example .env; chmod 600 .env; echo "Created .env. Add your Slack bot token there, or leave it empty to print to the console."; }

if $do_login && ! $dry_run; then
  if uv run --frozen fleetwatch status 2>/dev/null | grep -q "Signed in: yes"; then
    say "Already signed in to Epiphan Edge"
  elif [ -r /dev/tty ]; then
    say "Signing in to Epiphan Edge"
    # Piped from curl, stdin is this script: read the pasted redirect URL from the terminal instead.
    uv run --frozen fleetwatch login < /dev/tty
  else
    echo "No terminal to sign in from. Run:  cd $dir && uv run fleetwatch login"
    do_service=false
  fi
fi

if $dry_run; then
  say "Checking the service file (dry run)"
  deploy/install.sh --dry-run
elif $do_service; then
  say "Installing the service"
  deploy/install.sh
else
  say "Done. To run it as a service later:  cd $dir && deploy/install.sh"
fi
