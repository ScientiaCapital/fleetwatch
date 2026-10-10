#!/usr/bin/env bash
# Fleetwatch for Epiphan Edge: one-line install for macOS (launchd) and Linux (systemd).
#
#   curl -fsSL https://raw.githubusercontent.com/ScientiaCapital/fleetwatch/main/install.sh | bash
#
# What it does, in order:
#   1. installs uv if it is missing (a pinned release of the astral.sh installer, which checks the archive's sha256)
#   2. clones or updates Fleetwatch in ~/fleetwatch (change with --dir) at the newest release tag
#   3. installs the locked dependencies and creates .env from .env.example
#   4. runs `fleetwatch connect`: asks your region, signs in to Epiphan Edge, checks the team shows devices
#   5. installs the always-on service with deploy/install.sh
#
# Options: --dir PATH   --ref BRANCH_OR_TAG (default: newest vX.Y.Z tag, else main)   --no-login   --no-service
#          --dry-run (service file only, nothing loaded)
# Read it first if you like: https://github.com/ScientiaCapital/fleetwatch/blob/main/install.sh
set -euo pipefail

repo="${FLEETWATCH_REPO:-https://github.com/ScientiaCapital/fleetwatch.git}"
dir="${FLEETWATCH_DIR:-$HOME/fleetwatch}"
ref=""  # --ref; otherwise the newest release tag
uv_version="0.12.23"  # keep in step with the uv image in the Dockerfile
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
  say "Installing uv $uv_version"
  # A pinned release, not whatever astral.sh serves today. The installer checks the archive's sha256 itself.
  curl -LsSf "https://astral.sh/uv/$uv_version/install.sh" | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

latest_release() {
  # The newest vX.Y.Z tag on the remote; pre-releases (v0.2.0-rc1) don't count. Empty until the first release.
  # Sorted numerically field by field, which BSD and GNU sort both do; -V isn't everywhere.
  git ls-remote --tags --refs "$repo" 'refs/tags/v*' 2>/dev/null | sed 's#.*refs/tags/v##' \
    | grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' | sort -t. -k1,1n -k2,2n -k3,3n | tail -n 1 | sed 's/^/v/' || true
}

if [ -z "$ref" ]; then
  ref="$(latest_release)"
  if [ -n "$ref" ]; then say "Latest release: $ref"; else ref="main"; say "No release yet: installing the main branch"; fi
fi

not_updated() {
  printf '\nFleetwatch was not updated: %s\n' "$*" >&2
  exit 1
}

update_clone() {
  # Fetch every branch and tag. `git fetch origin v0.2.0` alone writes only FETCH_HEAD, so a release tagged
  # after the clone was made never becomes a local tag and checking it out fails.
  git -C "$dir" fetch --quiet --tags origin || not_updated "could not fetch from $repo (the git error above says why)."
  if git -C "$dir" rev-parse --quiet --verify "refs/tags/$ref^{commit}" >/dev/null; then
    # A release: check out that exact commit.
    git -C "$dir" checkout --quiet --detach "refs/tags/$ref" \
      || not_updated "$dir has local changes that $ref would overwrite. Run  git -C $dir status  to see them, commit or stash them, then run this again."
  elif git -C "$dir" rev-parse --quiet --verify "refs/remotes/origin/$ref^{commit}" >/dev/null; then
    # A branch: switch to it (creating it from origin if this clone started from a tag), then fast-forward.
    if git -C "$dir" rev-parse --quiet --verify "refs/heads/$ref" >/dev/null; then
      git -C "$dir" checkout --quiet "$ref"
    else
      git -C "$dir" checkout --quiet -b "$ref" --track "origin/$ref"
    fi || not_updated "$dir has local changes that $ref would overwrite. Run  git -C $dir status  to see them, commit or stash them, then run this again."
    git -C "$dir" merge --quiet --ff-only "origin/$ref" \
      || not_updated "$dir has commits on $ref that are not upstream, so it can't fast-forward. Run  git -C $dir status  to see them."
  else
    not_updated "$repo has no branch or tag named $ref."
  fi
}

if [ -d "$dir/.git" ]; then
  say "Updating $dir"
  update_clone
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
    uv run --frozen fleetwatch connect < /dev/tty
  else
    echo "No terminal to sign in from. Run:  cd $dir && uv run fleetwatch connect"
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

if ! $dry_run; then
  say "Health check"
  uv run --frozen fleetwatch doctor || echo "Fix the FAIL lines above, then run:  cd $dir && uv run fleetwatch doctor"
fi
