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
  systemctl --user list-units 'hermes-gateway@*' --no-legend 2>/dev/null \
    | awk '{print $1}' | while read -r unit; do
        if [ -n "$unit" ]; then
          systemctl --user stop "$unit" || true
        fi
      done
  systemctl --user stop recons-orchestrator.service 2>/dev/null || true

  echo "== Restoring into $RECONS_ROOT =="
  mkdir -p "$RECONS_ROOT"
  tar -C "$RECONS_ROOT" -xzf "$PLAIN"
  chmod 600 "$RECONS_ROOT/shared/secrets.env" 2>/dev/null || true
  chmod 600 "$RECONS_ROOT/shared/a2a-tokens.json" 2>/dev/null || true
  chmod 600 "$RECONS_ROOT/shared/telegram-tokens.json" 2>/dev/null || true
  find "$RECONS_ROOT/agents" -name 'service.env' -exec chmod 600 {} + 2>/dev/null || true
  find "$RECONS_ROOT/agents" -name '.migrate-hermes.json' -exec chmod 600 {} + 2>/dev/null || true

  echo "== Restarting =="
  systemctl --user start recons-orchestrator.service 2>/dev/null || true
  echo "Start each agent from the dashboard, or:"
  echo "  systemctl --user start hermes-gateway@<agent>"
else
  OUT="./restore-$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir -p "$OUT"
  tar -C "$OUT" -xzf "$PLAIN"
  echo "== Extracted to $OUT =="
  echo "Compare before committing to it, e.g.:"
  echo "  diff -r --brief $OUT $RECONS_ROOT | head"
fi
