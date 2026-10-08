#!/usr/bin/env bash
# Updating an existing install with install.sh: main -> tag, tag -> newer tag, tag -> main, main -> main
# (fast-forward), and that local changes stop the update loudly instead of being thrown away.
#
#   tests/install_update.sh
#
# Builds a local bare "upstream" from this checkout (so `uv sync --frozen` works in the clone), adds commits
# and tags v0.1.0, v0.2.0, v0.3.0, and points install.sh at it with FLEETWATCH_REPO. Nothing leaves the machine.
set -euo pipefail

here="$(cd "$(dirname "$0")/.." && pwd)"
installer="$here/install.sh"
work="$(mktemp -d "${TMPDIR:-/tmp}/fleetwatch-install-update.XXXXXX")"
trap 'rm -rf "$work"' EXIT

upstream="$work/upstream.git"
author="$work/author"
dir="$work/fleetwatch"
export FLEETWATCH_REPO="$upstream"
export GIT_AUTHOR_NAME="Fleetwatch Test" GIT_AUTHOR_EMAIL="test@example.invalid"
export GIT_COMMITTER_NAME="Fleetwatch Test" GIT_COMMITTER_EMAIL="test@example.invalid"

fails=0
pass() { printf 'ok   %s\n' "$*"; }
fail() { printf 'FAIL %s\n' "$*"; fails=$((fails + 1)); }
show_log() { sed 's/^/     /' "$work/install.log"; }

commit() {  # commit <message>: change a marker file upstream and push main
  echo "$1" >"$author/UPSTREAM_MARKER"
  git -C "$author" add UPSTREAM_MARKER
  git -C "$author" commit --quiet -m "$1"
  git -C "$author" push --quiet origin main
}
tag() {  # tag <name>: tag upstream main
  git -C "$author" tag "$1"
  git -C "$author" push --quiet origin "$1"
}
sha() { git -C "$author" rev-parse "$1^{commit}"; }

install() {  # install [args...]: run the installer offline into $dir, output to install.log
  bash "$installer" --dir "$dir" --no-login --no-service --dry-run "$@" >"$work/install.log" 2>&1
}

step() {  # step <label> <expected ref> [install args...]
  local label="$1" want="$2" got
  shift 2
  if ! install "$@"; then fail "$label: install.sh failed"; show_log; return; fi
  got="$(git -C "$dir" rev-parse HEAD)"
  if [ "$got" = "$(sha "$want")" ]; then pass "$label"; else fail "$label: HEAD is not $want"; show_log; fi
}

# Upstream starts as this checkout's HEAD on main, with no tags. Each release is tagged after the install
# already exists, as in real life: the clone has never seen the new tag.
git init --quiet --bare "$upstream"
git -C "$upstream" config receive.shallowUpdate true  # CI checks out a shallow clone
git clone --quiet --no-tags "$here" "$author"
git -C "$author" checkout --quiet -B main "$(git -C "$here" rev-parse HEAD)"
git -C "$author" remote set-url origin "$upstream"
git -C "$author" push --quiet origin main

step "fresh install of main" main --ref main
commit "release 0.1.0" && tag v0.1.0
step "main -> v0.1.0" v0.1.0 --ref v0.1.0
commit "release 0.2.0" && tag v0.2.0
step "v0.1.0 -> v0.2.0" v0.2.0 --ref v0.2.0
commit "work after 0.2.0"
step "v0.2.0 -> main" main --ref main
commit "more work on main"
step "main -> main (fast-forward)" main --ref main
commit "release 0.3.0" && tag v0.3.0
step "main -> newest release by default (v0.3.0)" v0.3.0

# A clone made at a tag has no local main branch at all.
rm -rf "$dir"
step "fresh install of v0.2.0" v0.2.0 --ref v0.2.0
commit "work after 0.3.0"
step "v0.2.0 (fresh clone) -> main" main --ref main

# Local changes in the install must stop the update, with a plain message, and must still be there after.
git -C "$dir" checkout --quiet --detach refs/tags/v0.1.0
echo "my local edit" >"$dir/UPSTREAM_MARKER"
if install --ref v0.2.0; then
  fail "local changes: the update should have stopped"
elif grep -q "Fleetwatch was not updated" "$work/install.log"; then
  pass "local changes: update stops with a plain message"
else
  fail "local changes: no plain message"; show_log
fi
if [ "$(cat "$dir/UPSTREAM_MARKER")" = "my local edit" ]; then pass "local changes: kept"; else fail "local changes: lost"; fi

if [ "$fails" -gt 0 ]; then echo "$fails failed"; exit 1; fi
echo "all passed"
