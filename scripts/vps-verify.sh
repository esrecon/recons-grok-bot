#!/usr/bin/env bash
# Post-install assertions: prove nothing is exposed and the safety settings are
# actually on. Run after bootstrap, after any config change, and whenever you
# update Hermes. Exit code is non-zero if any check fails.
set -uo pipefail

RECONS_ROOT="${RECONS_ROOT:-/opt/recons}"
FAILED=0

pass() { printf '   \033[32m✓\033[0m %s\n' "$1"; }
fail() { printf '   \033[31m✗\033[0m %s\n' "$1"; FAILED=1; }
info() { printf '   · %s\n' "$1"; }
section() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }

section "Listening sockets"
if command -v ss >/dev/null; then
  # Anything listening on a non-loopback address is a finding, unless it is the
  # tailnet interface (Tailscale Serve terminates on loopback, so normally none).
  EXPOSED=$(ss -tlnH 2>/dev/null | awk '{print $4}' \
    | grep -Ev '^(127\.0\.0\.1|\[::1\]|localhost)' | grep -Ev '^\*?:(22)$' || true)
  if [ -z "$EXPOSED" ]; then
    pass "no services listening on non-loopback addresses"
  else
    fail "these sockets are not loopback-bound:"
    echo "$EXPOSED" | sed 's/^/       /'
  fi
else
  info "ss not available; skipping socket check"
fi

section "Firewall"
if command -v ufw >/dev/null; then
  if ufw status 2>/dev/null | grep -q '^Status: active'; then
    pass "ufw active"
    ufw status 2>/dev/null | grep -q 'deny (incoming)' \
      && pass "default deny incoming" \
      || fail "default incoming policy is not deny"
  else
    fail "ufw is not active"
  fi
else
  info "ufw not installed; skipping"
fi

section "Tailscale"
if command -v tailscale >/dev/null; then
  if tailscale status >/dev/null 2>&1; then
    pass "tailscale up"
    if tailscale serve status 2>/dev/null | grep -q .; then
      pass "tailscale serve configured (tailnet-only HTTPS)"
    else
      info "no serve config yet — see docs/15-tailscale.md"
    fi
    if tailscale funnel status 2>/dev/null | grep -qi 'funnel.*on\|https://'; then
      fail "FUNNEL IS ON — this exposes the dashboard to the public internet"
    else
      pass "funnel off (never expose this publicly)"
    fi
  else
    fail "tailscale installed but not connected"
  fi
else
  fail "tailscale not installed"
fi

section "Secrets"
SEC="$RECONS_ROOT/shared/secrets.env"
if [ -f "$SEC" ]; then
  MODE=$(stat -c '%a' "$SEC" 2>/dev/null || echo "?")
  [ "$MODE" = "600" ] && pass "secrets.env is chmod 600" \
                      || fail "secrets.env is $MODE (must be 600)"
  grep -q '^RECONS_WEBHOOK_SECRET=.\+' "$SEC" \
    && pass "webhook signing secret set" \
    || fail "RECONS_WEBHOOK_SECRET is empty — the audit feed cannot be verified"
else
  fail "$SEC missing"
fi

TOK="$RECONS_ROOT/shared/a2a-tokens.json"
if [ -f "$TOK" ]; then
  MODE=$(stat -c '%a' "$TOK" 2>/dev/null || echo "?")
  [ "$MODE" = "600" ] && pass "a2a-tokens.json is chmod 600" \
                      || fail "a2a-tokens.json is $MODE (must be 600)"
fi

section "Agent safety settings"
shopt -s nullglob
FOUND=0
for cfg in "$RECONS_ROOT"/agents/*/home/config.yaml; do
  FOUND=1
  NAME=$(basename "$(dirname "$(dirname "$cfg")")")
  grep -q 'mode: smart' "$cfg" && pass "$NAME: approvals on (smart)" \
                               || fail "$NAME: approvals are not set to smart"
  grep -q 'write_approval: true' "$cfg" && pass "$NAME: agent-written skills need approval" \
                                        || fail "$NAME: skills.write_approval is not true"
  grep -q 'backend: docker' "$cfg" && pass "$NAME: tool execution sandboxed in docker" \
                                   || fail "$NAME: terminal backend is not docker"
  grep -qE 'host:\s*127\.0\.0\.1' "$cfg" && pass "$NAME: A2A bound to loopback" \
                                         || fail "$NAME: A2A host is not loopback"
done
[ "$FOUND" -eq 0 ] && info "no agents provisioned yet"

for env in "$RECONS_ROOT"/agents/*/service.env; do
  NAME=$(basename "$(dirname "$env")")
  grep -q '^A2A_PEER_TOKENS=.\+' "$env" \
    && pass "$NAME: A2A peer tokens present" \
    || info "$NAME: no peers yet (single-agent install)"
done

section "Hermes version"
if command -v hermes >/dev/null; then
  V=$(hermes --version 2>/dev/null | head -1 || echo "unknown")
  info "hermes $V — keep current; 2026 CVEs are fixed in recent releases"
  info "run 'hermes doctor' for its own health + advisory check"
else
  fail "hermes not installed"
fi

if [ "$FAILED" -ne 0 ]; then
  printf '\n\033[31mvps-verify: FAILED — fix the items above before use\033[0m\n'
  exit 1
fi
printf '\n\033[32mvps-verify: all checks passed\033[0m\n'
