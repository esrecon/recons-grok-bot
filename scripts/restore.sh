#!/usr/bin/env bash
# Restore a backup made by backup.sh. Restores into a scratch directory by
# default so you can diff before overwriting anything real — pass --in-place
# only when you mean it.
#
#   ./scripts/restore.sh backup.tar.gz.age            # -> ./restore-<stamp>/
#   ./scripts/restore.sh backup.tar.gz.age --in-place # -> $RECONS_ROOT (agents stopped first)
set -euo pipefail

ARCHIVE="${1:?usage: restore.sh <archive> [--in-place]}"
MODE="${2:-}"
RECONS_ROOT="${RECONS_ROOT:-/opt/recons}"

[ -f "$ARCHIVE" ] || { echo "no such archive: $ARCHIVE" >&2; exit 1; }

# systemd scope: read what bootstrap recorded — never re-detect (see update.sh
# for why probing disagrees with itself). Works run as the operator or as root.
systemd_scope() {
  local scope
  scope="$(cat "$RECONS_ROOT/systemd-scope" 2>/dev/null || true)"
  case "$scope" in
    user|system) printf '%s' "$scope" ;;
    *) printf '%s' "${RECONS_SYSTEMD_SCOPE:-system}" ;;
  esac
}
SCOPE_FLAG=""
if [ "$(systemd_scope)" = "user" ]; then
  SCOPE_FLAG="--user "
fi
TARGET_USER="$(stat -c '%U' "$RECONS_ROOT" 2>/dev/null || echo "${USER:-root}")"
TARGET_UID="$(id -u "$TARGET_USER" 2>/dev/null || id -u)"

sctl() {  # systemctl in the recorded scope, reaching the owning user's manager
  if [ "$(systemd_scope)" = "user" ]; then
    if [ "$(id -u)" -eq 0 ] && [ "$TARGET_USER" != "root" ]; then
      sudo -u "$TARGET_USER" -H \
        env "XDG_RUNTIME_DIR=/run/user/$TARGET_UID" \
            "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$TARGET_UID/bus" \
        systemctl --user "$@"
    else
      systemctl --user "$@"
    fi
  else
    systemctl "$@"
  fi
}

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
PLAIN="$WORK/archive.tar.gz"

case "$ARCHIVE" in
  *.age) age -d -o "$PLAIN" "$ARCHIVE" ;;
  *.gpg) gpg -o "$PLAIN" -d "$ARCHIVE" ;;
  *)     cp "$ARCHIVE" "$PLAIN" ;;
esac

if [ "$MODE" = "--in-place" ]; then
  echo "== Stopping agents =="
  echo "   scope: $(systemd_scope) (from $RECONS_ROOT/systemd-scope; owner: $TARGET_USER)"
  sctl list-units 'hermes-gateway@*' --no-legend --plain \
    | awk '{print $1}' | while read -r unit; do
        if [ -n "$unit" ]; then
          sctl stop "$unit" || echo "   WARNING: stop of $unit failed" >&2
        fi
      done || echo "   WARNING: could not list gateway units" >&2
  sctl stop recons-orchestrator.service \
    || echo "   WARNING: orchestrator stop failed (was it running?)" >&2

  echo "== Restoring into $RECONS_ROOT =="
  mkdir -p "$RECONS_ROOT"
  tar -C "$RECONS_ROOT" -xzf "$PLAIN"
  chmod 600 "$RECONS_ROOT/shared/secrets.env" 2>/dev/null || true
  chmod 600 "$RECONS_ROOT/shared/a2a-tokens.json" 2>/dev/null || true
  chmod 600 "$RECONS_ROOT/shared/telegram-tokens.json" 2>/dev/null || true
  find "$RECONS_ROOT/agents" -name 'service.env' -exec chmod 600 {} + 2>/dev/null || true
  find "$RECONS_ROOT/agents" -name '.migrate-hermes.json' -exec chmod 600 {} + 2>/dev/null || true

  echo "== Restarting =="
  if ! sctl start recons-orchestrator.service; then
    echo "FAILED to start the orchestrator after the restore." >&2
    echo "Check: systemctl ${SCOPE_FLAG}status recons-orchestrator" >&2
    exit 1
  fi
  echo "Start each agent from the dashboard, or:"
  echo "  systemctl ${SCOPE_FLAG}start hermes-gateway@<agent>"
else
  OUT="./restore-$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir -p "$OUT"
  tar -C "$OUT" -xzf "$PLAIN"
  echo "== Extracted to $OUT =="
  echo "Compare before committing to it, e.g.:"
  echo "  diff -r --brief $OUT $RECONS_ROOT | head"
fi
