#!/usr/bin/env bash
# migrate-hermes.sh — snapshot-copy a Hermes agent from another machine into
# this platform, keep the copy refreshed while both stay live, and preflight
# the eventual Telegram cutover.
#
# The old machine KEEPS RUNNING (and keeps any Telegram bot it serves) until
# cutover day. The copy made here is registered as an *imported* agent:
# dashboard chat and history work immediately, but no gateway unit is enabled,
# so nothing on this machine can grab the bot's single getUpdates connection.
#
#   ./scripts/migrate-hermes.sh pull --from old-vps.tailXXXX.ts.net
#   ./scripts/migrate-hermes.sh sync                 # refresh the snapshot
#   ./scripts/migrate-hermes.sh status               # health / drift report
#   ./scripts/migrate-hermes.sh sync --final         # cutover-grade final copy
#   ./scripts/migrate-hermes.sh cutover-check        # green-light the cutover
#
# Full runbook: docs/70-existing-hermes-and-buzz.md (migration) and
# docs/35-telegram-cutover.md (the cutover itself).
set -euo pipefail

RECONS_ROOT="${RECONS_ROOT:-/opt/recons}"
API="${RECONS_API:-http://127.0.0.1:8330}"

note() { printf '   %s\n' "$1"; }
warn() { printf '\033[33m   %s\033[0m\n' "$1"; }
die()  { printf '\n\033[31m%s\033[0m\n' "$1" >&2; exit 1; }

# `set -e` can abort mid-script with nothing printed — always say where.
on_error() {
  local rc=$1 line=$2
  printf '\n\033[31mmigrate-hermes.sh stopped at line %s (exit %s).\033[0m\n' "$line" "$rc" >&2
  printf 'Nothing is registered until a transfer completes — fix the cause and re-run.\n' >&2
}
trap 'on_error "$?" "$LINENO"' ERR

usage() {
  cat <<'EOF'
Usage: migrate-hermes.sh <command> [options]

Commands
  pull            First snapshot: copy the remote Hermes home here and register
                  it in the roster (as an imported agent — no gateway started)
  sync            Refresh an existing snapshot from the same source
  status          Show source reachability, versions, last sync, divergence
  cutover-check   Preflight the Telegram cutover (read-only; never cuts over)

Options
  --from <host>            SSH host / Tailscale MagicDNS name of the old machine
                           (required for pull; remembered afterwards)
  --remote-home <path>     Hermes home on the old machine     (default: ~/.hermes)
  --name <display>         Roster display name                (default: Hermes)
  --agent-id <id>          Roster id / directory name         (default: slug of --name)
  --role <text>            Roster job description
  --auth shared|copy|none  Credential handling on arrival     (default: shared)
                             shared: symlink this machine's default auth store
                             copy:   bring the remote auth.json (token-rotation risk)
                             none:   leave the copy unauthenticated
  --final                  Cutover-grade sync: requires the old gateway stopped;
                           also brings the remote auth.json across
  --assume-old-stopped     Skip the interactive stopped-gateway confirmation
                           (for the cutover script)
  --prune                  Mirror deletions from the source (rsync --delete)
  --exclude <pattern>      Extra rsync exclude (repeatable)
  --yes                    Non-interactive: accept divergence warnings
  --dry-run                Show what would transfer/register; change nothing

Environment: RECONS_ROOT (default /opt/recons), RECONS_API (default
http://127.0.0.1:8330), RECONS_SYSTEMD_SCOPE (fallback when the scope file is
missing).
EOF
}

# --------------------------------------------------------------------------
# argument parsing
# --------------------------------------------------------------------------
CMD="${1:-}"
if [ -z "$CMD" ] || [ "$CMD" = "-h" ] || [ "$CMD" = "--help" ] || [ "$CMD" = "help" ]; then
  usage
  exit 0
fi
shift
case "$CMD" in
  pull|sync|status|cutover-check) ;;
  *) usage; die "unknown command: $CMD" ;;
esac

SRC_HOST=""
# The literal tilde is deliberate: this is a path on the REMOTE machine, and
# the preflight helper expands it against the remote $HOME, not the local one.
# shellcheck disable=SC2088
SRC_HOME="~/.hermes"
NAME="Hermes"
AGENT_ID=""
ROLE="Personal Hermes agent (migrated from another machine)"
AUTH_MODE="shared"
AUTH_SET=0
FINAL=0
ASSUME_STOPPED=0
PRUNE=0
YES=0
DRY=0
EXTRA_EXCLUDES=()

while [ $# -gt 0 ]; do
  case "$1" in
    --from)            SRC_HOST="${2:?--from needs a host}"; shift 2 ;;
    --remote-home)     SRC_HOME="${2:?--remote-home needs a path}"; shift 2 ;;
    --name)            NAME="${2:?--name needs a value}"; shift 2 ;;
    --agent-id)        AGENT_ID="${2:?--agent-id needs a value}"; shift 2 ;;
    --role)            ROLE="${2:?--role needs a value}"; shift 2 ;;
    --auth)            AUTH_MODE="${2:?--auth needs shared|copy|none}"; AUTH_SET=1; shift 2 ;;
    --final)           FINAL=1; shift ;;
    --assume-old-stopped) ASSUME_STOPPED=1; shift ;;
    --prune)           PRUNE=1; shift ;;
    --exclude)         EXTRA_EXCLUDES+=(--exclude="${2:?--exclude needs a pattern}"); shift 2 ;;
    --yes)             YES=1; shift ;;
    --dry-run)         DRY=1; shift ;;
    -h|--help)         usage; exit 0 ;;
    *) die "unknown option: $1 (see --help)" ;;
  esac
done

case "$AUTH_MODE" in shared|copy|none) ;; *) die "--auth must be shared|copy|none" ;; esac
if [ "$FINAL" = 1 ] && [ "$CMD" != "sync" ]; then
  die "--final only applies to sync"
fi

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
slug_of() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//'
}

ssh_run() {
  ssh -o BatchMode=yes -o ConnectTimeout=8 "$SRC_HOST" -- "$@"
}
RSYNC_SSH=(-e "ssh -o BatchMode=yes -o ConnectTimeout=8")

# Where this install's units live. Read what bootstrap recorded — never
# re-detect (see services.py for why probing disagrees with itself).
systemd_scope() {
  local scope
  scope="$(cat "$RECONS_ROOT/systemd-scope" 2>/dev/null || true)"
  case "$scope" in
    user|system) printf '%s' "$scope" ;;
    *) printf '%s' "${RECONS_SYSTEMD_SCOPE:-system}" ;;
  esac
}

unit_is_active() {
  local unit="hermes-gateway@$AGENT_ID.service"
  if [ "$(systemd_scope)" = "user" ]; then
    systemctl --user is-active --quiet "$unit" 2>/dev/null
  else
    systemctl is-active --quiet "$unit" 2>/dev/null
  fi
}

# version_ge A B → true when A >= B (numeric dotted versions)
version_ge() {
  [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -1)" = "$2" ]
}

version_number() {
  printf '%s' "$1" | grep -oE '[0-9]+(\.[0-9]+)+' | head -1 || true
}

resolve_paths() {
  if [ -z "$AGENT_ID" ]; then
    AGENT_ID="$(slug_of "$NAME")"
  fi
  [ -n "$AGENT_ID" ] || die "could not derive an agent id from '$NAME' — pass --agent-id"
  AGENT_DIR="$RECONS_ROOT/agents/$AGENT_ID"
  DEST_HOME="$AGENT_DIR/home"
  STAMP_FILE="$AGENT_DIR/.migrate-hermes.json"
}

# For sync/status/cutover-check with no --agent-id: find the one stamp.
resolve_existing() {
  if [ -n "$AGENT_ID" ] || [ "$NAME" != "Hermes" ]; then
    resolve_paths
    return
  fi
  local stamps=()
  while IFS= read -r -d '' s; do stamps+=("$s"); done \
    < <(find "$RECONS_ROOT/agents" -maxdepth 2 -name '.migrate-hermes.json' -print0 2>/dev/null)
  case ${#stamps[@]} in
    0) resolve_paths ;;  # fall back to the default name; commands report "no stamp"
    1)
      STAMP_FILE="${stamps[0]}"
      AGENT_DIR="$(dirname "$STAMP_FILE")"
      AGENT_ID="$(basename "$AGENT_DIR")"
      DEST_HOME="$AGENT_DIR/home"
      ;;
    *) die "several migrated agents found under $RECONS_ROOT/agents — pass --agent-id <id>" ;;
  esac
}

stamp_get() {
  python3 - "$STAMP_FILE" "$1" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        print(json.load(f).get(sys.argv[2], ""))
except Exception:
    pass
PY
}

write_stamp() {  # $1 = kind (pull|sync|final), $2 = sqlite method
  MH_FILE="$STAMP_FILE" MH_SOURCE_HOST="$SRC_HOST" MH_SOURCE_HOME="$SRC_HOME_ABS" \
  MH_AGENT_ID="$AGENT_ID" MH_NAME="$NAME" MH_KIND="$1" MH_METHOD="$2" \
  MH_REMOTE_VER="${REMOTE_VERSION:-unknown}" MH_LOCAL_VER="${LOCAL_VERSION:-unknown}" \
  MH_DEST="$DEST_HOME" \
  python3 - <<'PY'
import hashlib, json, os, time
from pathlib import Path

dest = Path(os.environ["MH_DEST"])

def sha(name: str) -> str:
    f = dest / name
    return hashlib.sha256(f.read_bytes()).hexdigest() if f.is_file() else ""

state = dest / "state.db"
stamp = {
    "source_host": os.environ["MH_SOURCE_HOST"],
    "source_home": os.environ["MH_SOURCE_HOME"],
    "agent_id": os.environ["MH_AGENT_ID"],
    "name": os.environ["MH_NAME"],
    "last_sync_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "last_sync_kind": os.environ["MH_KIND"],
    "sqlite_method": os.environ["MH_METHOD"],
    "state_db_mtime_after_sync": int(state.stat().st_mtime) if state.exists() else 0,
    "soul_sha": sha("SOUL.md"),
    "config_sha": sha("config.yaml"),
    "remote_hermes_version": os.environ["MH_REMOTE_VER"],
    "local_hermes_version": os.environ["MH_LOCAL_VER"],
}
p = Path(os.environ["MH_FILE"])
p.write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")
p.chmod(0o600)
PY
}

# What the roster says about this agent: "true"/"false" (imported flag) or "absent".
roster_imported_flag() {
  local body
  if body="$(curl -sS --max-time 5 "$API/api/agents/$AGENT_ID" 2>/dev/null)"; then
    python3 - "$body" <<'PY'
import json, sys
try:
    rec = json.loads(sys.argv[1])
except Exception:
    print("absent"); raise SystemExit
if isinstance(rec, dict) and "id" in rec:
    print("true" if rec.get("imported") else "false")
else:
    print("absent")
PY
  elif [ -f "$RECONS_ROOT/roster.json" ]; then
    python3 - "$RECONS_ROOT/roster.json" "$AGENT_ID" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        recs = json.load(f)
except Exception:
    print("absent"); raise SystemExit
for r in recs:
    if r.get("id") == sys.argv[2]:
        print("true" if r.get("imported") else "false")
        break
else:
    print("absent")
PY
  else
    echo "absent"
  fi
}

# --------------------------------------------------------------------------
# remote inspection + staging
# --------------------------------------------------------------------------
preflight() {
  [ -n "$SRC_HOST" ] || die "no source host — pass --from <host> (or run pull first)"
  note "checking SSH to $SRC_HOST"
  local out
  out="$(ssh_run bash -s -- "$SRC_HOME" <<'RS'
set -eu
RH="$1"
case "$RH" in "~") RH="$HOME" ;; "~/"*) RH="$HOME/${RH#\~/}" ;; esac
if [ ! -d "$RH" ]; then
  echo "STATUS MISSING"
  echo "HOME $RH"
  exit 0
fi
ok=no
for m in config.yaml config.yml SOUL.md state.db; do
  if [ -e "$RH/$m" ]; then ok=yes; break; fi
done
echo "STATUS OK"
echo "MARKERS $ok"
echo "HOME $RH"
echo "SIZE $(du -sb "$RH" 2>/dev/null | cut -f1)"
echo "VERSION $(hermes --version 2>/dev/null | head -1 || echo unknown)"
RS
)" || die "cannot SSH to $SRC_HOST — is this machine on the same tailnet? (tailscale status)"

  local status markers
  status="$(printf '%s\n' "$out" | sed -n 's/^STATUS //p')"
  SRC_HOME_ABS="$(printf '%s\n' "$out" | sed -n 's/^HOME //p')"
  [ "$status" = "OK" ] || die "no directory at $SRC_HOME_ABS on $SRC_HOST — pass --remote-home <path>"
  markers="$(printf '%s\n' "$out" | sed -n 's/^MARKERS //p')"
  [ "$markers" = "yes" ] || die "$SRC_HOME_ABS on $SRC_HOST does not look like a Hermes home (no config.yaml/SOUL.md/state.db)"
  REMOTE_SIZE="$(printf '%s\n' "$out" | sed -n 's/^SIZE //p')"
  REMOTE_VERSION="$(printf '%s\n' "$out" | sed -n 's/^VERSION //p')"
  LOCAL_VERSION="$(hermes --version 2>/dev/null | head -1 || echo unknown)"
  note "remote home: $SRC_HOME_ABS ($(numfmt --to=iec "${REMOTE_SIZE:-0}" 2>/dev/null || echo "${REMOTE_SIZE:-?} bytes"))"
  note "hermes versions — remote: ${REMOTE_VERSION:-unknown} | local: ${LOCAL_VERSION:-unknown}"

  local rnum lnum
  rnum="$(version_number "${REMOTE_VERSION:-}")"
  lnum="$(version_number "${LOCAL_VERSION:-}")"
  if [ -n "$rnum" ] && [ -n "$lnum" ] && ! version_ge "$lnum" "$rnum"; then
    warn "local Hermes ($lnum) is OLDER than the remote ($rnum) — its state.db schema may be newer than this machine understands."
    warn "Update Hermes here first (./scripts/update.sh), or expect 'hermes -z' errors."
  fi

  # Disk headroom: remote size + 20%.
  local avail need
  avail="$(df -B1 --output=avail "$RECONS_ROOT" 2>/dev/null | tail -1 | tr -d '[:space:]' || true)"
  need=$(( ${REMOTE_SIZE:-0} + ${REMOTE_SIZE:-0} / 5 ))
  if [ -n "$avail" ] && [ "$avail" -lt "$need" ]; then
    die "not enough disk under $RECONS_ROOT: need ~$need bytes (source + 20%), have $avail"
  fi
}

stage_remote_dbs() {
  note "snapshotting live databases on $SRC_HOST"
  local out
  out="$(ssh_run bash -s -- "$SRC_HOME_ABS" <<'RS'
set -eu
RH="$1"
S="$(mktemp -d /tmp/hermes-migrate.XXXXXX)"
cd "$RH"
method=raw
if command -v sqlite3 >/dev/null 2>&1; then
  # The sqlite backup API yields a transactionally consistent snapshot of a
  # live (WAL) database — safe while the old agent keeps running.
  method=backup
  find . -name '*.db' -type f 2>/dev/null | while IFS= read -r db; do
    rel="${db#./}"
    mkdir -p "$S/$(dirname "$rel")"
    sqlite3 "$rel" ".backup \"$S/$rel\"" 2>/dev/null || cp -p "$rel" "$S/$rel"
  done
else
  # No sqlite3 CLI: copy each db with its WAL/SHM sidecars back-to-back.
  # Consistent only if no checkpoint runs mid-copy — fine for an interim
  # snapshot; the final (cutover) sync requires the old agent stopped anyway.
  find . \( -name '*.db' -o -name '*.db-wal' -o -name '*.db-shm' \) -type f 2>/dev/null \
    | while IFS= read -r db; do
        rel="${db#./}"
        mkdir -p "$S/$(dirname "$rel")"
        cp -p "$rel" "$S/$rel"
      done
fi
echo "METHOD $method"
echo "STAGING $S"
RS
)"
  SQLITE_METHOD="$(printf '%s\n' "$out" | sed -n 's/^METHOD //p')"
  STAGING_DIR="$(printf '%s\n' "$out" | sed -n 's/^STAGING //p')"
  [ -n "$STAGING_DIR" ] || die "failed to stage database snapshots on $SRC_HOST"
  if [ "$SQLITE_METHOD" = "raw" ] && [ "$FINAL" != 1 ]; then
    warn "no sqlite3 CLI on $SRC_HOST — live DBs copied raw (with WAL sidecars)."
    warn "Fine for an interim snapshot; the final sync requires the old agent stopped."
  fi
}

# --------------------------------------------------------------------------
# transfer
# --------------------------------------------------------------------------
transfer() {  # $1 = "pull" | "sync"
  local mode="$1"
  local extra_args=()
  BK_DIR=""

  if [ "$mode" = "sync" ]; then
    BK_DIR="$AGENT_DIR/sync-backups/$(date -u +%Y%m%dT%H%M%SZ)"
    extra_args+=(--backup --backup-dir="$BK_DIR")
  fi
  if [ "$PRUNE" = 1 ]; then
    extra_args+=(--delete)
  fi
  # Never refresh a SOUL.md that carries the platform's managed team section —
  # a refresh from the (fence-less) original would silently strip it.
  if [ "$mode" = "sync" ] && [ -f "$DEST_HOME/SOUL.md" ] \
     && grep -q 'recons:team:begin' "$DEST_HOME/SOUL.md" 2>/dev/null; then
    extra_args+=(--exclude=SOUL.md)
    note "SOUL.md carries the managed team section — kept local, not refreshed"
  fi

  mkdir -p "$DEST_HOME"
  note "copying $SRC_HOST:$SRC_HOME_ABS → $DEST_HOME"
  # Pass 1: everything except live DBs (staged separately), credentials
  # (placed per --auth policy) and caches. Exclude-list, not include-list:
  # wherever Hermes keeps its Telegram token and pairing state, it rides along.
  # VERIFY the cache/tmp/logs names against the installed Hermes layout.
  rsync -a -s --partial "${RSYNC_SSH[@]}" \
    --exclude='*.db' --exclude='*.db-wal' --exclude='*.db-shm' \
    --exclude='auth.json' \
    --exclude='cache/' --exclude='tmp/' --exclude='logs/' \
    "${EXTRA_EXCLUDES[@]}" \
    "${extra_args[@]}" \
    "$SRC_HOST:$SRC_HOME_ABS/" "$DEST_HOME/"

  # Pass 2: the consistent DB snapshots staged on the remote side.
  rsync -a -s "${RSYNC_SSH[@]}" "$SRC_HOST:$STAGING_DIR/" "$DEST_HOME/"
  ssh_run rm -rf "$STAGING_DIR"

  if [ -n "$BK_DIR" ] && [ -d "$BK_DIR" ]; then
    note "anything overwritten was saved to $BK_DIR"
  fi
}

fetch_remote_auth() {
  rm -f "$DEST_HOME/auth.json"
  if rsync -a -s "${RSYNC_SSH[@]}" "$SRC_HOST:$SRC_HOME_ABS/auth.json" "$DEST_HOME/auth.json" 2>/dev/null; then
    chmod 600 "$DEST_HOME/auth.json"
    note "brought the remote auth.json across (chmod 600)"
  else
    warn "no auth.json on the remote to copy"
  fi
}

place_auth() {
  case "$AUTH_MODE" in
    shared)
      local default_home="${HERMES_HOME:-$HOME/.hermes}"
      if [ -f "$default_home/auth.json" ]; then
        ln -sfn "$default_home/auth.json" "$DEST_HOME/auth.json"
        note "auth.json → symlink to $default_home/auth.json (this machine's shared credentials)"
        note "(the remote's own credentials come across automatically at 'sync --final')"
      else
        warn "no auth.json in $default_home yet — the copy is unauthenticated until a provider is connected here (or use --auth copy)"
      fi
      ;;
    copy)
      fetch_remote_auth
      warn "two live machines refreshing one OAuth token can invalidate each other — prefer --auth shared until cutover"
      ;;
    none)
      note "auth: none (copy left unauthenticated)"
      ;;
  esac
}

fix_perms() {
  if [ -f "$DEST_HOME/auth.json" ] && [ ! -L "$DEST_HOME/auth.json" ]; then
    chmod 600 "$DEST_HOME/auth.json"
  fi
  find "$DEST_HOME" -maxdepth 1 -name '*.env' -exec chmod 600 {} + 2>/dev/null || true
  # When run via sudo, hand the copy to whoever owns the agents tree.
  if [ "$(id -u)" -eq 0 ] && [ -d "$RECONS_ROOT/agents" ]; then
    local owner
    owner="$(stat -c '%U:%G' "$RECONS_ROOT/agents")"
    if [ "$owner" != "root:root" ]; then
      chown -R "$owner" "$AGENT_DIR"
    fi
  fi
}

register() {
  local payload resp code
  payload="$(python3 - "$DEST_HOME" "$NAME" "$ROLE" <<'PY'
import json, sys
print(json.dumps({"home": sys.argv[1], "name": sys.argv[2], "role": sys.argv[3]}))
PY
)"
  resp="$(mktemp)"
  code="$(curl -sS -o "$resp" -w '%{http_code}' -X POST "$API/api/import" \
          -H 'content-type: application/json' -d "$payload" 2>/dev/null || echo 000)"
  if [ "$code" = "201" ]; then
    note "registered '$NAME' in the roster (imported — no gateway started here)"
  elif [ "$code" = "409" ] && grep -q "already imported" "$resp"; then
    note "already in the roster — nothing to register"
  elif [ "$code" = "000" ]; then
    rm -f "$resp"
    die "could not reach the orchestrator at $API — the files are copied; start recons-orchestrator and re-run 'pull' (re-running is cheap, rsync resumes)"
  else
    local detail
    detail="$(cat "$resp")"
    rm -f "$resp"
    die "registration failed (HTTP $code): $detail"
  fi
  rm -f "$resp"
}

divergence_guard() {
  local drift=()
  local rec cur
  rec="$(stamp_get state_db_mtime_after_sync)"
  if [ -f "$DEST_HOME/state.db" ] && [ -n "$rec" ]; then
    cur="$(stat -c %Y "$DEST_HOME/state.db")"
    if [ "$cur" -gt "$rec" ]; then
      drift+=("state.db — dashboard chats with the copy since the last sync")
    fi
  fi
  rec="$(stamp_get soul_sha)"
  if [ -n "$rec" ] && [ -f "$DEST_HOME/SOUL.md" ]; then
    cur="$(sha256sum "$DEST_HOME/SOUL.md" | cut -d' ' -f1)"
    if [ "$cur" != "$rec" ] && ! grep -q 'recons:team:begin' "$DEST_HOME/SOUL.md" 2>/dev/null; then
      drift+=("SOUL.md — edited here since the last sync")
    fi
  fi
  rec="$(stamp_get config_sha)"
  if [ -n "$rec" ] && [ -f "$DEST_HOME/config.yaml" ]; then
    cur="$(sha256sum "$DEST_HOME/config.yaml" | cut -d' ' -f1)"
    if [ "$cur" != "$rec" ]; then
      drift+=("config.yaml — changed here since the last sync")
    fi
  fi

  if [ ${#drift[@]} -gt 0 ]; then
    warn "the copy on THIS machine has newer data than the last sync:"
    local d
    for d in "${drift[@]}"; do warn "  - $d"; done
    warn "refreshing overwrites it with the old machine's versions (recoverable from $AGENT_DIR/sync-backups/)"
    if [ "$YES" != 1 ]; then
      if [ ! -t 0 ]; then
        die "refusing to overwrite diverged local data non-interactively — pass --yes to accept"
      fi
      printf '   Continue? [y/N] '
      local answer
      read -r answer
      case "$answer" in y|Y|yes|YES) ;; *) die "aborted — nothing was changed" ;; esac
    fi
  fi
}

dry_run_report() {
  note "(dry run) files that would transfer:"
  rsync -a -s -n --stats "${RSYNC_SSH[@]}" \
    --exclude='*.db' --exclude='*.db-wal' --exclude='*.db-shm' \
    --exclude='auth.json' \
    --exclude='cache/' --exclude='tmp/' --exclude='logs/' \
    "${EXTRA_EXCLUDES[@]}" \
    "$SRC_HOST:$SRC_HOME_ABS/" "$DEST_HOME/" | tail -12
  note "(dry run) live DBs would be snapshotted remotely (sqlite3 .backup when available)"
  if [ "$CMD" = "pull" ]; then
    note "(dry run) would register: name='$NAME' id='$AGENT_ID' home='$DEST_HOME' via POST $API/api/import"
  fi
  note "(dry run) nothing was changed"
}

# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------
cmd_pull() {
  [ -n "$SRC_HOST" ] || die "pull needs --from <host> (the old machine's Tailscale/SSH name)"
  resolve_paths

  if [ -f "$STAMP_FILE" ] && [ "$(roster_imported_flag)" != "absent" ]; then
    die "'$AGENT_ID' is already migrated here — refresh it with: $0 sync"
  fi

  printf '\033[1m== Snapshot pull: %s:%s → %s ==\033[0m\n' "$SRC_HOST" "$SRC_HOME" "$DEST_HOME"
  mkdir -p "$AGENT_DIR"
  preflight
  if [ "$DRY" = 1 ]; then
    dry_run_report
    return
  fi
  stage_remote_dbs
  transfer pull
  place_auth
  fix_perms
  register
  write_stamp pull "$SQLITE_METHOD"

  printf '\n\033[32mDone.\033[0m\n'
  note "'$NAME' is in the dashboard now — chat and history work against the copy."
  note "The old machine is untouched and still owns any Telegram bot it serves."
  note "Refresh later with:   $0 sync"
  note "On cutover day:       stop the old gateway, then '$0 sync --final' and '$0 cutover-check'"
}

cmd_sync() {
  resolve_existing
  [ -f "$STAMP_FILE" ] || die "no snapshot here yet — start with: $0 pull --from <host>"

  if [ -z "$SRC_HOST" ]; then
    SRC_HOST="$(stamp_get source_host)"
  fi
  SRC_HOME="$(stamp_get source_home)"
  NAME="$(stamp_get name)"
  [ -n "$SRC_HOST" ] || die "the stamp file has no source host — re-run pull with --from"

  case "$(roster_imported_flag)" in
    false)
      die "'$AGENT_ID' has been PROMOTED — it is the live, managed copy now. Refusing to overwrite it with old-machine state. (Old files, if needed: $AGENT_DIR/sync-backups/)"
      ;;
    absent)
      warn "'$AGENT_ID' is not in the roster (orchestrator down, or it was removed) — syncing files only"
      ;;
  esac

  local kind="sync"
  if [ "$FINAL" = 1 ]; then
    kind="final"
    if [ "$ASSUME_STOPPED" != 1 ]; then
      warn "FINAL sync: the Hermes gateway on $SRC_HOST must be STOPPED first —"
      warn "one Telegram bot token supports exactly one live connection."
      if [ ! -t 0 ]; then
        die "not a terminal — pass --assume-old-stopped once the old gateway is confirmed stopped"
      fi
      printf '   Have you stopped it (e.g. systemctl --user disable --now <unit> on %s)? [y/N] ' "$SRC_HOST"
      local answer
      read -r answer
      case "$answer" in y|Y|yes|YES) ;; *) die "stop the old gateway first, then re-run" ;; esac
    fi
  fi

  printf '\033[1m== Refresh: %s:%s → %s%s ==\033[0m\n' "$SRC_HOST" "$SRC_HOME" "$DEST_HOME" \
    "$([ "$FINAL" = 1 ] && printf ' (FINAL)')"
  preflight
  if [ "$DRY" = 1 ]; then
    dry_run_report
    return
  fi
  divergence_guard
  stage_remote_dbs
  transfer sync

  if [ "$FINAL" = 1 ]; then
    # The old machine is quiescent and about to retire: the promoted agent
    # should carry its own credentials, not a symlink to this machine's.
    if [ "$AUTH_SET" = 1 ] && [ "$AUTH_MODE" != "copy" ]; then
      note "auth: keeping --auth $AUTH_MODE as requested"
    else
      fetch_remote_auth
    fi
  fi
  fix_perms
  write_stamp "$kind" "$SQLITE_METHOD"

  printf '\n\033[32mDone.\033[0m\n'
  if [ "$FINAL" = 1 ]; then
    note "cutover-grade snapshot complete — next: $0 cutover-check"
  else
    note "snapshot refreshed from $SRC_HOST"
  fi
}

cmd_status() {
  resolve_existing
  local red=0
  printf '\033[1m== migrate-hermes status: %s ==\033[0m\n' "$AGENT_ID"

  if [ ! -f "$STAMP_FILE" ]; then
    warn "no snapshot on this machine yet ($STAMP_FILE missing) — run: $0 pull --from <host>"
    exit 1
  fi
  note "last sync: $(stamp_get last_sync_utc) (kind: $(stamp_get last_sync_kind), db method: $(stamp_get sqlite_method))"
  note "source: $(stamp_get source_host):$(stamp_get source_home)"

  case "$(roster_imported_flag)" in
    true)   note "roster: registered (imported — pre-cutover state)" ;;
    false)  note "roster: registered and PROMOTED (managed — cutover done; sync is now refused)" ;;
    absent) warn "roster: NOT registered (orchestrator down or agent removed)"; red=1 ;;
  esac

  # Divergence since last sync.
  local rec cur
  rec="$(stamp_get state_db_mtime_after_sync)"
  if [ -f "$DEST_HOME/state.db" ] && [ -n "$rec" ] && [ "$(stat -c %Y "$DEST_HOME/state.db")" -gt "$rec" ]; then
    note "divergence: local copy has NEWER chats than the last sync (a refresh would overwrite them)"
  else
    note "divergence: none detected since the last sync"
  fi

  # Source reachability + versions.
  SRC_HOST="$(stamp_get source_host)"
  if [ -n "$SRC_HOST" ] && ssh_run true 2>/dev/null; then
    cur="$(ssh_run hermes --version 2>/dev/null | head -1 || echo unknown)"
    note "source: reachable | remote hermes: ${cur:-unknown} | local hermes: $(hermes --version 2>/dev/null | head -1 || echo unknown)"
  else
    warn "source $SRC_HOST: NOT reachable over SSH right now"
    red=1
  fi

  # Local gateway unit must stay off pre-cutover.
  if unit_is_active; then
    if [ "$(roster_imported_flag)" = "true" ]; then
      warn "hermes-gateway@$AGENT_ID is ACTIVE while the agent is still imported — it should not be; stop it"
      red=1
    else
      note "gateway unit: active (promoted)"
    fi
  else
    note "gateway unit: inactive (correct pre-cutover)"
  fi

  note "sizes: local $(du -sh "$DEST_HOME" 2>/dev/null | cut -f1 || echo '?')"
  if [ -d "$AGENT_DIR/sync-backups" ]; then
    note "sync backups: $(find "$AGENT_DIR/sync-backups" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | wc -l) (under $AGENT_DIR/sync-backups/)"
  fi

  exit "$red"
}

cmd_cutover_check() {
  resolve_existing
  printf '\033[1m== Telegram cutover preflight: %s ==\033[0m\n' "$AGENT_ID"

  [ -f "$STAMP_FILE" ] || die "no snapshot here — run pull/sync first"
  local kind
  kind="$(stamp_get last_sync_kind)"
  [ "$kind" = "final" ] || die "the last sync was '$kind', not cutover-grade — stop the old gateway and run: $0 sync --final"

  # Freshness: a final snapshot older than a day probably predates real traffic.
  local last age_ok
  last="$(stamp_get last_sync_utc)"
  age_ok="$(python3 - "$last" <<'PY'
import sys, time
try:
    t = time.mktime(time.strptime(sys.argv[1], "%Y-%m-%dT%H:%M:%SZ"))
    print("yes" if (time.time() - t) < 86400 else "no")
except Exception:
    print("no")
PY
)"
  if [ "$age_ok" = "yes" ]; then
    note "final snapshot: $last (fresh)"
  else
    warn "final snapshot is older than 24h ($last) — consider re-running: $0 sync --final"
  fi

  case "$(roster_imported_flag)" in
    true)   note "roster: registered, imported=true (ready to promote)" ;;
    false)  die "'$AGENT_ID' is already promoted — the cutover appears to be done" ;;
    absent) die "'$AGENT_ID' is not in the roster — is the orchestrator running?" ;;
  esac

  if unit_is_active; then
    die "hermes-gateway@$AGENT_ID is already ACTIVE here — stop it before cutover"
  fi
  note "local gateway unit: inactive (good — nothing here is polling the bot)"

  local rnum lnum
  rnum="$(version_number "$(stamp_get remote_hermes_version)")"
  lnum="$(version_number "$(stamp_get local_hermes_version)")"
  if [ -n "$rnum" ] && [ -n "$lnum" ]; then
    if version_ge "$lnum" "$rnum"; then
      note "hermes versions: local $lnum >= remote $rnum"
    else
      die "local Hermes ($lnum) is older than the snapshot's source ($rnum) — run ./scripts/update.sh first"
    fi
  else
    warn "could not compare Hermes versions (recorded: local '$(stamp_get local_hermes_version)', remote '$(stamp_get remote_hermes_version)')"
  fi

  # Best effort: is anything hermes-shaped still running on the old machine?
  SRC_HOST="$(stamp_get source_host)"
  if [ -n "$SRC_HOST" ] && ssh_run true 2>/dev/null; then
    local procs
    procs="$(ssh_run pgrep -af hermes 2>/dev/null || true)"
    if printf '%s' "$procs" | grep -qi gateway; then
      die "a hermes gateway still runs on $SRC_HOST — stop it (one bot token, one connection):
$procs"
    fi
    note "old machine: reachable, no hermes gateway process found"
  else
    note "old machine unreachable over SSH — if it is powered off, that also satisfies the one-connection rule"
  fi

  printf '\n\033[32mGREEN\033[0m — ready for cutover. Next: ./scripts/telegram-cutover.sh %s %s\n' \
    "$AGENT_ID" "${SRC_HOST:-<old-host>}"
}

case "$CMD" in
  pull)          cmd_pull ;;
  sync)          cmd_sync ;;
  status)        cmd_status ;;
  cutover-check) cmd_cutover_check ;;
esac
