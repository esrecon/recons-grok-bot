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

# `sudo` drops the variables `systemctl --user` needs to find its bus, so a
# user-scope command run this way fails with "Failed to connect to bus" even on
# an account that has a perfectly good session. Supply them explicitly.
TARGET_UID="$(id -u "$TARGET_USER")"
as_user() {
  sudo -u "$TARGET_USER" -H \
    env "XDG_RUNTIME_DIR=/run/user/$TARGET_UID" \
        "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$TARGET_UID/bus" \
    bash -lc "$*"
}

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
# Which scope the units actually landed in. Read what bootstrap recorded rather
# than detecting again: this script's environment (an interactive root session,
# which has a user bus) differs from the one bootstrap ran in (`sudo`, which
# does not), so a second detection can and did disagree with the first.
SCOPE_FILE="$RECONS_ROOT/systemd-scope"
if [ -r "$SCOPE_FILE" ]; then
  SYSTEMD_SCOPE="$(cat "$SCOPE_FILE")"
else
  SYSTEMD_SCOPE="${RECONS_SYSTEMD_SCOPE:-system}"
fi
systemctl_do() {
  if [ "$SYSTEMD_SCOPE" = "user" ]; then
    as_user "systemctl --user $*"
  else
    systemctl "$@"
  fi
}
if [ "$SYSTEMD_SCOPE" = "user" ]; then
  loginctl enable-linger "$TARGET_USER" 2>/dev/null || true
  note "installed as user services; agents will survive logout and reboot"
else
  note "installed as system services; agents start at boot"
fi

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
# Vendored skills ship in the repo; agents read the shared library. Install
# them there so the first agent created already has them (update.sh keeps
# them fresh on every update; operator-taught slugs are never touched).
if [ -d "$REPO_DIR/skills" ]; then
  for skill_dir in "$REPO_DIR"/skills/*/; do
    [ -f "${skill_dir}SKILL.md" ] || continue
    slug="$(basename "$skill_dir")"
    install -d -m 700 -o "$TARGET_USER" -g "$TARGET_USER" "$RECONS_ROOT/shared/skills/$slug"
    cp -a "${skill_dir}." "$RECONS_ROOT/shared/skills/$slug/"
    chown -R "$TARGET_USER:$TARGET_USER" "$RECONS_ROOT/shared/skills/$slug"
  done
  note "vendored skills installed into shared/skills"
fi
as_user "cd '$RECONS_ROOT/app/apps/orchestrator' && uv sync --frozen >/dev/null"

# The dashboard and its API are locked until an operator login exists. Set it
# now, while we are still in the terminal — the password is hashed on this
# machine and never stored; RECONS_OPERATOR_PASSWORD makes this non-interactive.
OPERATOR_USER="${RECONS_OPERATOR_USER:-$TARGET_USER}"
[ "$OPERATOR_USER" != "root" ] || OPERATOR_USER="operator"
if grep -qE '^RECONS_OPERATOR_PASSWORD_HASH=[$]scrypt[$]' "$RECONS_ROOT/shared/secrets.env" 2>/dev/null; then
  note "operator login already set (change it: python -m recons_orchestrator.security set-operator)"
else
  printf '\n'
  note "Choose the password you will sign into the dashboard with (user: $OPERATOR_USER)."
  note "At least 12 characters. It is hashed here and never written down."
  SET_OP="cd '$RECONS_ROOT/app/apps/orchestrator' && RECONS_ROOT='$RECONS_ROOT' uv run --frozen python -m recons_orchestrator.security set-operator --user '$OPERATOR_USER'"
  if [ -n "${RECONS_OPERATOR_PASSWORD:-}" ]; then
    printf '%s\n' "$RECONS_OPERATOR_PASSWORD" | as_user "$SET_OP --password-stdin" \
      || die "Could not set the operator login."
  else
    as_user "$SET_OP" || die "Could not set the operator login (re-run with RECONS_OPERATOR_PASSWORD=… to script it)."
  fi
fi
systemctl_do daemon-reload
if ! systemctl_do enable --now recons-orchestrator.service; then
  scope_flag=""
  [ "$SYSTEMD_SCOPE" = "user" ] && scope_flag="--user "
  die "The orchestrator did not start. Check: systemctl ${scope_flag}status recons-orchestrator"
fi

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
cat <<EOF
Sign in with the operator login you just set (user: ${OPERATOR_USER}).
The app takes it from here:
  • connect a model provider (paste a key, or sign in with ChatGPT)
  • create your first agent — a name and a one-line job

On your phone: install the Tailscale app, open the same link in Chrome,
then tap "Install app" for a home-screen icon.
EOF
