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

[ "$(id -u)" -eq 0 ] || die "Run with sudo: sudo $0"
[ "$TARGET_USER" != "root" ] || note "Running as root — services will be root-owned."

as_user() { sudo -u "$TARGET_USER" -H bash -lc "$*"; }

# ---------------------------------------------------------------------------
step 1 "System packages, firewall and Tailscale"
"$REPO_DIR/scripts/vps-bootstrap.sh" >/dev/null
note "done (ufw default-deny, SSH key-only, docker, tailscale installed)"

# ---------------------------------------------------------------------------
step 2 "Joining your tailnet"
if tailscale status >/dev/null 2>&1; then
  note "already connected"
else
  note "A sign-in link will appear — open it and approve this machine."
  tailscale up
fi
MAGIC_DNS="$(tailscale status --json 2>/dev/null | grep -o '"DNSName":"[^"]*"' | head -1 | cut -d'"' -f4 | sed 's/\.$//')"
[ -n "$MAGIC_DNS" ] || note "Could not read your MagicDNS name; you can find it with: tailscale status"

# ---------------------------------------------------------------------------
step 3 "Hermes, uv and the service units"
as_user "cd '$REPO_DIR' && RECONS_ROOT='$RECONS_ROOT' ./scripts/vps-bootstrap.sh --user" >/dev/null
loginctl enable-linger "$TARGET_USER" 2>/dev/null || true
note "installed; agents will survive logout and reboot"

# ---------------------------------------------------------------------------
step 4 "Building the dashboard"
if ! command -v npm >/dev/null; then
  note "installing Node.js 22"
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash - >/dev/null 2>&1
  apt-get install -y -q nodejs >/dev/null
fi
as_user "cd '$REPO_DIR/apps/dashboard' && npm ci --no-audit --no-fund >/dev/null && npm run build >/dev/null"
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
