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
  apt-get install -y -q curl git ufw unattended-upgrades docker.io

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
  echo "   Pin >= v0.20.1 and keep it current — see docs/60-security-hardening.md"

  log "Shared secrets file"
  if [ -f "$RECONS_ROOT/shared/secrets.env" ]; then
    skip "secrets.env exists"
  else
    install -m 600 "$REPO_DIR/config/shared-secrets.env.example" \
      "$RECONS_ROOT/shared/secrets.env"
    echo "   Created $RECONS_ROOT/shared/secrets.env — fill it in (chmod 600)."
    echo "   Generate the webhook secret with: openssl rand -hex 32"
  fi

  log "systemd user units"
  install -d "$HOME/.config/systemd/user"
  for unit in hermes-gateway@.service recons-orchestrator.service claude-wrapper.service; do
    install -m 644 "$REPO_DIR/config/systemd/$unit" "$HOME/.config/systemd/user/$unit"
  done
  systemctl --user daemon-reload
  echo "   Installed. Enable linger so agents survive logout:"
  echo "     sudo loginctl enable-linger $USER"

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
