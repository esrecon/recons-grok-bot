#!/usr/bin/env bash
# Idempotent VPS setup for the Recons Grok Bot stack (Ubuntu 24.04).
# Safe to re-run: every step checks before it changes anything.
#
#   sudo ./scripts/vps-bootstrap.sh          # system prep (firewall, packages)
#   ./scripts/vps-bootstrap.sh --user        # user-level (hermes, uv, units)
#
# Read docs/10-vps.md alongside this — the runbook explains each step, this
# script just performs them.
set -euo pipefail

RECONS_ROOT="${RECONS_ROOT:-/opt/recons}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-}"

log()  { printf '\033[1m==\033[0m %s\n' "$1"; }
skip() { printf '   (already done: %s)\n' "$1"; }

system_prep() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "system prep needs root: sudo $0" >&2
    exit 1
  fi

  log "Packages"
  apt-get update -q

  # libatomic1: Hermes ships its own Node build, which links against
  # libatomic.so.1. Minimal VPS images often omit it, and without it every node
  # invocation dies with "cannot open shared object file".
  pkgs=(curl git ufw unattended-upgrades ca-certificates libatomic1)
  # Many VPS images ship Docker CE from Docker's own repository. Installing
  # docker.io on top of that makes apt unresolvable ("pkgProblemResolver::Resolve
  # generated breaks"), so only add it when no docker is present at all.
  if command -v docker >/dev/null; then
    skip "docker already installed ($(docker --version 2>/dev/null || echo 'version unknown'))"
  else
    pkgs+=(docker.io)
  fi

  if ! apt-get install -y -q "${pkgs[@]}"; then
    cat >&2 <<EOF

   apt could not install: ${pkgs[*]}
   Re-run it without -q to see which package is the problem:
       apt-get install ${pkgs[*]}
   Common causes: a half-finished upgrade (fix: apt-get -f install), or held
   packages (list them with: apt-mark showhold).
EOF
    exit 1
  fi

  log "Firewall (default deny inbound; SSH + tailscale only)"
  ufw allow OpenSSH >/dev/null
  ufw allow in on tailscale0 >/dev/null 2>&1 || true
  ufw --force default deny incoming >/dev/null
  ufw --force default allow outgoing >/dev/null
  ufw --force enable >/dev/null
  ufw status verbose | sed 's/^/   /'

  log "SSH hardening (key-only)"
  if grep -q '^PasswordAuthentication no' /etc/ssh/sshd_config; then
    skip "PasswordAuthentication already off"
  else
    sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
    systemctl reload ssh || systemctl reload sshd || true
  fi

  log "Tailscale"
  if command -v tailscale >/dev/null; then
    skip "tailscale installed"
  else
    curl -fsSL https://tailscale.com/install.sh | sh
  fi
  echo "   Next: sudo tailscale up   (then see docs/15-tailscale.md)"

  log "Directories"
  install -d -m 755 "$RECONS_ROOT" "$RECONS_ROOT/agents" "$RECONS_ROOT/shared"
  install -d -m 700 "$RECONS_ROOT/shared/skills"
  chown -R "${SUDO_USER:-root}:${SUDO_USER:-root}" "$RECONS_ROOT"
}

user_setup() {
  # Create the directories this mode needs, so `--user` works whether or not
  # the sudo system-prep step ran first (and so a failed sudo step can't leave
  # you with a half-set-up box). $RECONS_ROOT usually lives under /opt, which
  # needs root to create the first time — say so plainly instead of failing with
  # a bare "permission denied" three steps later.
  log "Directories"
  if ! install -d -m 755 "$RECONS_ROOT" 2>/dev/null; then
    cat >&2 <<EOF
   Cannot create $RECONS_ROOT as $(whoami).
   Run the system prep first (it creates and hands you the directory):
       sudo $0
   or create it yourself:
       sudo install -d -o "$(whoami)" -g "$(whoami)" "$RECONS_ROOT"
EOF
    exit 1
  fi
  install -d -m 755 "$RECONS_ROOT/agents" "$RECONS_ROOT/shared"
  install -d -m 700 "$RECONS_ROOT/shared/skills"

  log "uv (Python runner)"
  if command -v uv >/dev/null; then
    skip "uv installed"
  else
    curl -LsSf https://astral.sh/uv/install.sh | sh
  fi

  log "Hermes Agent"
  if command -v hermes >/dev/null; then
    skip "hermes installed ($(hermes --version 2>/dev/null || echo 'version unknown'))"
  else
    # VERIFY the current install command against hermes-agent.nousresearch.com
    curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
  fi

  # Hermes bundles its own Node. Prove it actually runs before we build anything
  # on top of it — a missing shared library here surfaces much more clearly than
  # it does three steps later.
  if ! hermes --version >/dev/null 2>&1; then
    hermes_err="$(hermes --version 2>&1 || true)"
    cat >&2 <<EOF

   Hermes is installed but will not run:
     ${hermes_err}
EOF
    case "$hermes_err" in
      *"cannot open shared object file"*)
        missing="$(printf '%s' "$hermes_err" | sed -n 's/.*loading shared libraries: \([^:]*\):.*/\1/p' | head -1)"
        cat >&2 <<EOF
   A system library is missing${missing:+ (${missing})}. Install it and re-run:
       sudo apt-get install -y libatomic1 ca-certificates
EOF
        ;;
    esac
    exit 1
  fi
  echo "   Pin >= v0.20.1 and keep it current — see docs/60-security-hardening.md"

  log "Shared secrets file"
  if [ -f "$RECONS_ROOT/shared/secrets.env" ]; then
    skip "secrets.env exists"
  else
    install -m 600 "$REPO_DIR/config/shared-secrets.env.example" \
      "$RECONS_ROOT/shared/secrets.env"
    echo "   Created $RECONS_ROOT/shared/secrets.env (chmod 600)."
    echo "   Nothing to edit here — the orchestrator generates the audit signing"
    echo "   secret on first boot, and you connect providers in the dashboard."
  fi

  # Agents normally run as *user* services owned by an unprivileged account.
  # root has no user-level systemd instance on most VPS images, though —
  # `systemctl --user` there fails with "Failed to connect to bus: No medium
  # found" — so fall back to system scope when user scope isn't usable.
  # RECONS_SYSTEMD_SCOPE=user|system overrides the detection.
  scope="${RECONS_SYSTEMD_SCOPE:-}"
  if [ -z "$scope" ]; then
    if [ "$(id -u)" -eq 0 ]; then
      # root always gets system scope. An interactive root session often has a
      # user bus (so the probe below would say "user"), but system services —
      # including the orchestrator, which drives these units — do not. Probing
      # here once chose user scope for root and produced two rival installs.
      scope=system
    elif systemctl --user show-environment >/dev/null 2>&1; then
      scope=user
    else
      scope=system
    fi
  fi

  log "systemd units (${scope} scope)"
  units="hermes-gateway@.service recons-orchestrator.service claude-wrapper.service"

  # Resolve the real binary locations rather than trusting the paths written in
  # the unit files: uv lands in ~/.local/bin or /usr/local/bin depending on how
  # it was installed, hermes likewise, and systemd's own PATH includes neither
  # home directory. Guessing wrong fails at service start, far from the cause.
  uv_bin="$(command -v uv || true)"
  hermes_bin="$(command -v hermes || true)"
  [ -n "$uv_bin" ] || { echo "   uv not found on PATH" >&2; exit 1; }
  [ -n "$hermes_bin" ] || { echo "   hermes not found on PATH" >&2; exit 1; }
  echo "   uv:     $uv_bin"
  echo "   hermes: $hermes_bin"

  # Rewrite a unit for this machine: real binary paths, and the right install
  # target for the scope (default.target is user scope's multi-user.target).
  if [ "$scope" = "system" ]; then
    target_fix='s/^WantedBy=default.target$/WantedBy=multi-user.target/'
  else
    target_fix=''   # user scope keeps default.target
  fi

  render_unit() {
    sed -e "s|^ExecStart=/usr/bin/env hermes|ExecStart=${hermes_bin}|" \
        -e "s|^ExecStart=/usr/local/bin/uv|ExecStart=${uv_bin}|" \
        ${target_fix:+-e "$target_fix"} \
        "$1"
  }

  if [ "$scope" = "user" ]; then
    install -d "$HOME/.config/systemd/user"
    for unit in $units; do
      render_unit "$REPO_DIR/config/systemd/$unit" > "$HOME/.config/systemd/user/$unit"
      chmod 644 "$HOME/.config/systemd/user/$unit"
    done
    systemctl --user daemon-reload
    echo "   Installed. Enable linger so agents survive logout:"
    echo "     sudo loginctl enable-linger $USER"
  else
    if [ "$(id -u)" -ne 0 ]; then
      echo "   Cannot use system scope as $(whoami) — re-run this step as root," >&2
      echo "   or set RECONS_SYSTEMD_SCOPE=user if your account has a systemd session." >&2
      exit 1
    fi
    for unit in $units; do
      render_unit "$REPO_DIR/config/systemd/$unit" > "/etc/systemd/system/$unit"
      chmod 644 "/etc/systemd/system/$unit"
    done
    systemctl daemon-reload
    echo "   Installed as system services (no linger needed — they start at boot)."
  fi

  # Record the scope actually used. Detection depends on the environment it runs
  # in — a root SSH session has a user bus, the same account under `sudo` does
  # not — so anything else that drives these units must read this rather than
  # detect for itself and risk disagreeing.
  printf '%s\n' "$scope" > "$RECONS_ROOT/systemd-scope"

  log "Dashboard build"
  if [ -d "$REPO_DIR/apps/dashboard/dist" ]; then
    skip "dist present (rebuild with: cd apps/dashboard && npm ci && npm run build)"
  else
    echo "   Build it with: cd apps/dashboard && npm ci && npm run build"
  fi
  echo "   Then point RECONS_DASHBOARD_DIST at apps/dashboard/dist (or copy it to"
  echo "   $RECONS_ROOT/dashboard)."

  log "Done"
  echo "   Next: docs/15-tailscale.md to publish the dashboard to your tailnet,"
  echo "   then ./scripts/vps-verify.sh to check nothing is exposed."
}

case "$MODE" in
  --user) user_setup ;;
  "")     system_prep ;;
  *)      echo "usage: $0 [--user]" >&2; exit 2 ;;
esac
