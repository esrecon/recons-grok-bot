#!/usr/bin/env bash
# Update the stack safely: back up first, update Hermes, run its own health
# check, rebuild the dashboard, restart services, then verify nothing became
# exposed. Keeping Hermes current is a security control (docs/60).
#
# Runnable as the operator account or as root: the systemd scope is read from
# what bootstrap recorded, and commands reach the units' owning user manager.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RECONS_ROOT="${RECONS_ROOT:-/opt/recons}"

# systemd scope: read what bootstrap recorded — never re-detect (a root SSH
# session has a user bus while the same account under sudo does not, so a
# second detection can and did disagree with the first). Same helper as
# telegram-cutover.sh / migrate-hermes.sh; contract mirrored in services.py.
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

# The account that owns the install (bootstrap chowns $RECONS_ROOT to it).
# Needed when this script runs as root but the units live in that user's
# manager: sudo drops the variables `systemctl --user` needs to find its bus,
# so supply them explicitly (same bridge as vps-quickstart's as_user).
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

as_owner() {  # run a build/sync command as the install's owner when we're root,
  # so files (node_modules, .venv, .git) keep the owner the services expect.
  # Login shell, as quickstart's as_user: sudo resets PATH otherwise.
  if [ "$(id -u)" -eq 0 ] && [ "$TARGET_USER" != "root" ]; then
    sudo -u "$TARGET_USER" -H bash -lc "$1"
  else
    bash -lc "$1"
  fi
}

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
SELF="$REPO_DIR/scripts/update.sh"
SELF_BEFORE="$(sha256sum "$SELF" | cut -d' ' -f1)"
as_owner "git -C '$REPO_DIR' pull --ff-only" || echo "   (skipped: not a fast-forward)"
# A running script keeps executing the version it started with, so a fix to
# update.sh would not apply to the very run that fetched it (observed: a
# restart fix rode along in a pull but the old step 5 still ran). Re-exec the
# new script once; the guard prevents loops.
if [ "$(sha256sum "$SELF" | cut -d' ' -f1)" != "$SELF_BEFORE" ] \
   && [ "${RECONS_UPDATE_REEXEC:-}" != "1" ]; then
  echo "   update.sh itself changed in this pull — re-running the new version"
  RECONS_UPDATE_REEXEC=1 exec bash "$SELF"
fi

# The orchestrator unit runs from $RECONS_ROOT/app (quickstart copies the repo
# there when the checkout lives elsewhere). Keep that copy in step and refresh
# its Python deps — a correct restart still boots old code otherwise.
APP_DIR="$REPO_DIR"
if [ "$REPO_DIR" != "$RECONS_ROOT/app" ] && [ -d "$RECONS_ROOT/app" ]; then
  echo "   syncing $RECONS_ROOT/app from $REPO_DIR"
  cp -a "$REPO_DIR/." "$RECONS_ROOT/app/"
  APP_DIR="$RECONS_ROOT/app"
fi
if command -v uv >/dev/null && [ -f "$APP_DIR/apps/orchestrator/pyproject.toml" ]; then
  as_owner "cd '$APP_DIR/apps/orchestrator' && uv sync --frozen"
fi

# Vendored skills ship in the repo, but agents only read the shared library
# (skills.external_dirs). Install/refresh each repo-owned skill there.
# Operator-taught skills live under their own slugs and are never touched.
if [ -d "$REPO_DIR/skills" ]; then
  echo "   installing vendored skills into $RECONS_ROOT/shared/skills"
  for skill_dir in "$REPO_DIR"/skills/*/; do
    [ -f "${skill_dir}SKILL.md" ] || continue
    slug="$(basename "$skill_dir")"
    install -d -m 700 "$RECONS_ROOT/shared/skills/$slug"
    cp -a "${skill_dir}." "$RECONS_ROOT/shared/skills/$slug/"
    if [ "$(id -u)" -eq 0 ] && [ "$TARGET_USER" != "root" ]; then
      chown -R "$TARGET_USER:$TARGET_USER" "$RECONS_ROOT/shared/skills/$slug"
    fi
  done
fi

echo "== 4/6 Rebuild dashboard =="
if [ -f "$REPO_DIR/apps/dashboard/package.json" ]; then
  as_owner "cd '$REPO_DIR/apps/dashboard' && npm ci --no-audit --no-fund && npm run build"
  # Deploy the fresh build where the orchestrator actually serves it from
  # (RECONS_DASHBOARD_DIST in the unit; quickstart installs the same path).
  # Rebuilding without this copy left the served app permanently stale.
  DASH_DIST="${RECONS_DASHBOARD_DIST:-$RECONS_ROOT/dashboard}"
  mkdir -p "$DASH_DIST"
  cp -r "$REPO_DIR/apps/dashboard/dist/." "$DASH_DIST/"
  echo "   deployed to $DASH_DIST"
fi

echo "== 5/6 Restart services =="
echo "   scope: $(systemd_scope) (from $RECONS_ROOT/systemd-scope; owner: $TARGET_USER)"
sctl daemon-reload
# An update that does not restart the orchestrator keeps serving the OLD
# dashboard and API while claiming success — fail loudly instead.
if ! sctl restart recons-orchestrator.service; then
  echo "FAILED to restart the orchestrator — the old code keeps serving until it restarts." >&2
  echo "Check: systemctl ${SCOPE_FLAG}status recons-orchestrator" >&2
  exit 1
fi
sctl list-units 'hermes-gateway@*' --no-legend --plain \
  | awk '{print $1}' | while read -r unit; do
      if [ -n "$unit" ]; then
        sctl restart "$unit" || echo "   WARNING: restart of $unit failed" >&2
      fi
    done || echo "   WARNING: could not list gateway units" >&2

echo "== 6/6 Verify =="
"$REPO_DIR/scripts/vps-verify.sh"
