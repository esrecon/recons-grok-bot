#!/usr/bin/env bash
# Update the stack safely: back up first, update Hermes, run its own health
# check, rebuild the dashboard, restart services, then verify nothing became
# exposed. Keeping Hermes current is a security control (docs/60).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "== 1/6 Backup first =="
"$REPO_DIR/scripts/backup.sh" || {
  echo "backup failed — refusing to update" >&2
  exit 1
}

echo "== 2/6 Update Hermes =="
if command -v hermes >/dev/null; then
  hermes update || echo "   (hermes update failed; check manually)"
  hermes doctor || echo "   (hermes doctor reported issues — read them)"
else
  echo "   hermes not installed; skipping"
fi

echo "== 3/6 Update this repo =="
git -C "$REPO_DIR" pull --ff-only || echo "   (skipped: not a fast-forward)"

echo "== 4/6 Rebuild dashboard =="
if [ -f "$REPO_DIR/apps/dashboard/package.json" ]; then
  (cd "$REPO_DIR/apps/dashboard" && npm ci --no-audit --no-fund && npm run build)
fi

echo "== 5/6 Restart services =="
systemctl --user daemon-reload
systemctl --user restart recons-orchestrator.service 2>/dev/null || true
systemctl --user list-units 'hermes-gateway@*' --no-legend 2>/dev/null \
  | awk '{print $1}' | while read -r unit; do
      [ -n "$unit" ] && systemctl --user restart "$unit" || true
    done

echo "== 6/6 Verify =="
"$REPO_DIR/scripts/vps-verify.sh"
