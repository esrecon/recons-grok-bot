#!/usr/bin/env bash
# One command: clone-to-running-dashboard.
#
#   sudo ./scripts/vps-quickstart.sh
#
# Does the whole server-side install — packages, firewall, Tailscale, Hermes,
# the orchestrator, the dashboard build, and the tailnet HTTPS address — and
# then prints the URL to open.
#
# Everything after that (API keys, ChatGPT sign-in, creating agents) happens in
# the app. You should not need to come back to this terminal.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RECONS_ROOT="${RECONS_ROOT:-/opt/recons}"
TARGET_USER="${SUDO_USER:-$USER}"

step()  { printf '\n\033[1m[%s/7] %s\033[0m\n' "$1" "$2"; }
note()  { printf '   %s\n' "$1"; }
die()   { printf '\n\033[31m%s\033[0m\n' "$1" >&2; exit 1; }

# `set -e` can abort mid-script with nothing printed — the worst possible
# failure mode when someone is following a runbook. Always say where it stopped.
on_error() {
  local rc=$1 line=$2
  printf '\n\033[31mvps-quickstart.sh stopped at line %s (exit %s).\033[0m\n' "$line" "$rc" >&2
  printf 'Nothing is half-finished — fix the cause and re-run; every step is idempotent.\n' >&2
}
trap 'on_error "$?" "$LINENO"' ERR

[ "$(id -u)" -eq 0 ] || die "Run with sudo: sudo $0"
[ "$TARGET_USER" != "root" ] || note "Running as root — services will be root-owned."

as_user() { sudo -u "$TARGET_USER" -H bash -lc "$*"; }

# ---------------------------------------------------------------------------
step 1 "System packages, firewall and Tailscale"
# Show output AND keep a log. Redirecting to a file alone hides two things that
# matter: progress on slow steps, and any prompt an upstream installer decides
# to ask — which then looks exactly like a hang. `set -o pipefail` makes the
# `if !` see the real exit status rather than tee's.
BOOTSTRAP_LOG="${TMPDIR:-/tmp}/recons-bootstrap.log"
if ! "$REPO_DIR/scripts/vps-bootstrap.sh" 2>&1 | tee "$BOOTSTRAP_LOG"; then
  die "System prep failed. Full log: $BOOTSTRAP_LOG"
fi
note "done (ufw default-deny, SSH key-only, docker, tailscale installed)"

# ---------------------------------------------------------------------------
step 2 "Joining your tailnet"
if tailscale status >/dev/null 2>&1; then
  note "already connected"
else
  note "A sign-in link will appear — open it and approve this machine."
  tailscale up
fi
# Read the machine's MagicDNS name. Not finding it is a cosmetic problem (we
# just can't print the URL), never a reason to abort — so the pipeline is
# explicitly allowed to fail despite `set -e -o pipefail`. `head` closing the
# pipe early also SIGPIPEs `grep`, which pipefail would otherwise treat as fatal.
MAGIC_DNS=""
if ts_json="$(tailscale status --json 2>/dev/null)"; then
  MAGIC_DNS="$(printf '%s' "$ts_json" \
    | grep -o '"DNSName":"[^"]*"' \
    | head -1 | cut -d'"' -f4 | sed 's/\.$//')" || MAGIC_DNS=""
fi
[ -n "$MAGIC_DNS" ] || note "Could not read your MagicDNS name; find it with: tailscale status"

# ---------------------------------------------------------------------------
step 3 "Hermes, uv and the service units"
note "installs uv and Hermes Agent — downloads ~200MB (a browser for your agents),"
note "so give it a few minutes."
printf '\n'
note "Hermes will ask how you want to set it up. Choose \033[1m1 (Quick Setup)\033[0m."
note "It signs you into Nous Portal for free and takes seconds. Do NOT pick 2 —"
note "that walks you through every provider by hand, which is the job this"
note "dashboard exists to do, so you would be doing it twice."
printf '\n'
USER_LOG="${TMPDIR:-/tmp}/recons-user-setup.log"
if ! as_user "cd '$REPO_DIR' && RECONS_ROOT='$RECONS_ROOT' ./scripts/vps-bootstrap.sh --user" \
     2>&1 | tee "$USER_LOG"; then
  die "Hermes/uv setup failed. Full log: $USER_LOG"
fi
loginctl enable-linger "$TARGET_USER" 2>/dev/null || true
note "installed; agents will survive logout and reboot"

# ---------------------------------------------------------------------------
step 4 "Building the dashboard"
BUILD_LOG="${TMPDIR:-/tmp}/recons-dashboard-build.log"
if ! command -v npm >/dev/null; then
  note "installing Node.js 22"
  if ! { curl -fsSL https://deb.nodesource.com/setup_22.x | bash -; } 2>&1 | tee "$BUILD_LOG"; then
    die "Could not add the Node.js repository. Full log: $BUILD_LOG"
  fi
  apt-get install -y -q nodejs 2>&1 | tee -a "$BUILD_LOG"
fi
note "npm ci + build — the longest step, usually 2-5 minutes"
if ! as_user "cd '$REPO_DIR/apps/dashboard' && npm ci --no-audit --no-fund && npm run build" \
     2>&1 | tee -a "$BUILD_LOG"; then
  die "Dashboard build failed. Full log: $BUILD_LOG"
fi
install -d -o "$TARGET_USER" -g "$TARGET_USER" "$RECONS_ROOT/dashboard"
cp -r "$REPO_DIR/apps/dashboard/dist/." "$RECONS_ROOT/dashboard/"
chown -R "$TARGET_USER:$TARGET_USER" "$RECONS_ROOT/dashboard"
note "built"

# ---------------------------------------------------------------------------
step 5 "Starting the orchestrator"
if [ "$REPO_DIR" != "$RECONS_ROOT/app" ]; then
  install -d -o "$TARGET_USER" -g "$TARGET_USER" "$RECONS_ROOT/app"
  cp -a "$REPO_DIR/." "$RECONS_ROOT/app/"
  chown -R "$TARGET_USER:$TARGET_USER" "$RECONS_ROOT/app"
fi
as_user "cd '$RECONS_ROOT/app/apps/orchestrator' && uv sync --frozen >/dev/null"
as_user "systemctl --user daemon-reload && systemctl --user enable --now recons-orchestrator.service" || \
  die "The orchestrator did not start. Check: systemctl --user status recons-orchestrator"

for _ in $(seq 1 20); do
  if curl -fsS http://127.0.0.1:8330/api/health >/dev/null 2>&1; then break; fi
  sleep 1
done
curl -fsS http://127.0.0.1:8330/api/health >/dev/null 2>&1 \
  || die "Orchestrator is not answering on 127.0.0.1:8330."
note "running (and it generated the audit signing secret for you)"

# ---------------------------------------------------------------------------
step 6 "Publishing to your tailnet (private — never the public internet)"
tailscale serve --bg --https=443 http://127.0.0.1:8330 >/dev/null 2>&1 \
  || tailscale serve --bg 8330 >/dev/null 2>&1 \
  || note "Could not configure serve automatically — see docs/15-tailscale.md"
note "done"

# ---------------------------------------------------------------------------
step 7 "Checking nothing is exposed"
as_user "cd '$REPO_DIR' && RECONS_ROOT='$RECONS_ROOT' ./scripts/vps-verify.sh" || \
  note "Review the failures above before using this with real accounts."

# ---------------------------------------------------------------------------
printf '\n\033[32m─────────────────────────────────────────────\033[0m\n'
printf '\033[1mReady.\033[0m Open this on any device on your tailnet:\n\n'
if [ -n "$MAGIC_DNS" ]; then
  printf '    \033[1mhttps://%s/\033[0m\n\n' "$MAGIC_DNS"
else
  printf '    https://<your-machine>.<your-tailnet>.ts.net/\n'
  printf '    (find the name with: tailscale status)\n\n'
fi
cat <<'EOF'
The app takes it from here:
  • connect a model provider (paste a key, or sign in with ChatGPT)
  • create your first agent — a name and a one-line job

On your phone: install the Tailscale app, open the same link in Chrome,
then tap "Install app" for a home-screen icon.
EOF
