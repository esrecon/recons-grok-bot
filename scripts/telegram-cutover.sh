#!/usr/bin/env bash
# telegram-cutover.sh — move a migrated agent's Telegram bot onto THIS machine
# and (by default) make the agent head of staff.
#
# One bot token supports exactly ONE live getUpdates connection, so the order
# here is the whole point:
#
#   stop old gateway → final sync → preflight → promote (files only)
#   → resume (the one moment polling starts) → verify
#
#   ./scripts/telegram-cutover.sh hermes old-vps.tailXXXX.ts.net \
#       --allowed-users 123456789
#
#   ./scripts/telegram-cutover.sh hermes old-vps.tailXXXX.ts.net --rollback
#
# Runbook with the full story: docs/35-telegram-cutover.md
set -euo pipefail

RECONS_ROOT="${RECONS_ROOT:-/opt/recons}"
API="${RECONS_API:-http://127.0.0.1:8330}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MIGRATE="$SCRIPT_DIR/migrate-hermes.sh"

step() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }
note() { printf '   %s\n' "$1"; }
warn() { printf '\033[33m   %s\033[0m\n' "$1"; }
die()  { printf '\n\033[31m%s\033[0m\n' "$1" >&2; exit 1; }

on_error() {
  local rc=$1 line=$2
  printf '\n\033[31mtelegram-cutover.sh stopped at line %s (exit %s).\033[0m\n' "$line" "$rc" >&2
  printf 'If the new gateway is misbehaving: %s %s %s --rollback\n' "$0" "${AGENT_ID:-<agent>}" "${OLD_HOST:-<old-host>}" >&2
}
trap 'on_error "$?" "$LINENO"' ERR

usage() {
  cat <<'EOF'
Usage: telegram-cutover.sh <agent-id> <old-host> [options]

Options
  --allowed-users <ids>  Numeric Telegram user id(s), comma-separated. Without
                         this, promotion relies on what can be extracted from
                         the copied home — pass it explicitly when in doubt.
  --token <bot-token>    BotFather token, only needed when extraction from the
                         copied home fails.
  --old-unit <name>      systemd unit serving `hermes gateway` on the old
                         machine (tried in user, sudo-system and system scope;
                         pkill is the fallback when unset).
  --no-lead              Do not make the agent head of staff.
  --yes                  Non-interactive (skips the DM round-trip prompt).
  --rollback             Undo a cutover: stop the gateway here, demote the
                         agent back to imported, re-enable the old gateway.
EOF
}

AGENT_ID="${1:-}"
OLD_HOST="${2:-}"
if [ -z "$AGENT_ID" ] || [ "$AGENT_ID" = "-h" ] || [ "$AGENT_ID" = "--help" ]; then
  usage
  exit 0
fi
[ -n "$OLD_HOST" ] || { usage; die "need both <agent-id> and <old-host>"; }
shift 2

ALLOWED_USERS=""
BOT_TOKEN=""
OLD_UNIT=""
MAKE_LEAD=1
YES=0
ROLLBACK=0
while [ $# -gt 0 ]; do
  case "$1" in
    --allowed-users) ALLOWED_USERS="${2:?--allowed-users needs ids}"; shift 2 ;;
    --token)         BOT_TOKEN="${2:?--token needs a value}"; shift 2 ;;
    --old-unit)      OLD_UNIT="${2:?--old-unit needs a name}"; shift 2 ;;
    --no-lead)       MAKE_LEAD=0; shift ;;
    --yes)           YES=1; shift ;;
    --rollback)      ROLLBACK=1; shift ;;
    -h|--help)       usage; exit 0 ;;
    *) die "unknown option: $1 (see --help)" ;;
  esac
done

UNIT="hermes-gateway@$AGENT_ID.service"

# Single-string remote commands so quoting survives the ssh hop.
ssh_old() {
  ssh -o BatchMode=yes -o ConnectTimeout=8 "$OLD_HOST" -- "$1"
}

systemd_scope() {
  local scope
  scope="$(cat "$RECONS_ROOT/systemd-scope" 2>/dev/null || true)"
  case "$scope" in
    user|system) printf '%s' "$scope" ;;
    *) printf '%s' "${RECONS_SYSTEMD_SCOPE:-system}" ;;
  esac
}

sctl() {  # local systemctl in whichever scope this install recorded
  if [ "$(systemd_scope)" = "user" ]; then
    systemctl --user "$@"
  else
    systemctl "$@"
  fi
}

jrnl() {
  if [ "$(systemd_scope)" = "user" ]; then
    journalctl --user "$@"
  else
    journalctl "$@"
  fi
}

api_call() {  # method path [json-body] → body on stdout; dies on non-2xx
  local method="$1" path="$2" body="${3:-}"
  local resp code
  resp="$(mktemp)"
  if [ -n "$body" ]; then
    code="$(curl -sS -o "$resp" -w '%{http_code}' -X "$method" "$API$path" \
            -H 'content-type: application/json' -d "$body" 2>/dev/null || echo 000)"
  else
    code="$(curl -sS -o "$resp" -w '%{http_code}' -X "$method" "$API$path" 2>/dev/null || echo 000)"
  fi
  if [ "${code:0:1}" != "2" ]; then
    local detail
    detail="$(cat "$resp")"
    rm -f "$resp"
    die "$method $path failed (HTTP $code): $detail"
  fi
  cat "$resp"
  rm -f "$resp"
}

json_get() {  # json_get '<json>' key → value (top-level or record.<key>)
  python3 - "$1" "$2" <<'PY'
import json, sys
try:
    doc = json.loads(sys.argv[1])
except Exception:
    raise SystemExit
key = sys.argv[2]
node = doc
for part in key.split("."):
    if not isinstance(node, dict):
        raise SystemExit
    node = node.get(part)
if node is None:
    raise SystemExit
print(node if not isinstance(node, (dict, list)) else json.dumps(node))
PY
}

# --------------------------------------------------------------------------
rollback() {
  step "Rolling back: freeing the connection here"
  sctl disable --now "$UNIT" 2>/dev/null || note "local unit already inactive"

  step "Demoting '$AGENT_ID' back to imported"
  api_call POST "/api/agents/$AGENT_ID/demote" >/dev/null
  note "config.yaml.pre-promote.bak restored; Telegram off; imported again"

  step "Re-enabling the gateway on $OLD_HOST"
  if [ -n "$OLD_UNIT" ]; then
    if ssh_old "systemctl --user enable --now '$OLD_UNIT'" 2>/dev/null \
       || ssh_old "sudo -n systemctl enable --now '$OLD_UNIT'" 2>/dev/null \
       || ssh_old "systemctl enable --now '$OLD_UNIT'" 2>/dev/null; then
      note "old gateway re-enabled"
    else
      warn "could not re-enable $OLD_UNIT over SSH — start it on $OLD_HOST manually"
    fi
  else
    warn "no --old-unit given — restart the gateway on $OLD_HOST yourself (e.g. 'hermes gateway' under its old supervisor)"
  fi

  printf '\n\033[32mRolled back.\033[0m\n'
  note "DM the bot to confirm the OLD machine answers again."
  note "Messages handled here during the failed window live only in this copy's"
  note "state.db and will be overwritten by the next sync — that is expected."
}

if [ "$ROLLBACK" = 1 ]; then
  rollback
  exit 0
fi

# --------------------------------------------------------------------------
step "Preflight"
[ -x "$MIGRATE" ] || die "cannot find $MIGRATE"
AGENT_JSON="$(api_call GET "/api/agents/$AGENT_ID")"
IMPORTED="$(json_get "$AGENT_JSON" imported || true)"
case "$IMPORTED" in
  True|true) note "'$AGENT_ID' is registered and imported — good" ;;
  False|false) die "'$AGENT_ID' is already promoted — nothing to cut over (rollback with --rollback)" ;;
  *) die "could not read '$AGENT_ID' from the roster at $API" ;;
esac
ssh_old true 2>/dev/null || die "cannot SSH to $OLD_HOST"
note "SSH to $OLD_HOST works"

step "Stopping the Hermes gateway on $OLD_HOST (reboot-proof)"
if [ -n "$OLD_UNIT" ]; then
  if ssh_old "systemctl --user disable --now '$OLD_UNIT'" 2>/dev/null \
     || ssh_old "sudo -n systemctl disable --now '$OLD_UNIT'" 2>/dev/null \
     || ssh_old "systemctl disable --now '$OLD_UNIT'" 2>/dev/null; then
    note "disabled $OLD_UNIT"
  else
    warn "systemctl could not disable $OLD_UNIT — trying pkill"
  fi
fi
ssh_old "pkill -f 'hermes gateway'" 2>/dev/null || true
sleep 2
if ssh_old "pgrep -af hermes 2>/dev/null" 2>/dev/null | grep -qi gateway; then
  die "a hermes gateway is STILL running on $OLD_HOST — stop it manually (which supervisor keeps restarting it?), then re-run"
fi
note "no hermes gateway process remains on $OLD_HOST"
if [ -z "$OLD_UNIT" ]; then
  warn "no --old-unit given: the process is dead, but whatever supervised it may bring it back on reboot — disable that on $OLD_HOST."
fi

step "Final sync (consistent snapshot, credentials, last Telegram turns)"
SYNC_ARGS=(sync --final --assume-old-stopped --agent-id "$AGENT_ID")
if [ "$YES" = 1 ]; then
  SYNC_ARGS+=(--yes)
fi
"$MIGRATE" "${SYNC_ARGS[@]}"

step "Cutover preflight"
"$MIGRATE" cutover-check --agent-id "$AGENT_ID"

step "Promoting '$AGENT_ID' (files + roster only — nothing starts yet)"
PROMOTE_BODY="$(python3 - "$ALLOWED_USERS" "$BOT_TOKEN" "$MAKE_LEAD" <<'PY'
import json, sys
users, token, make_lead = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
body = {"telegram_enabled": True, "make_lead": make_lead}
if users:
    body["telegram_allowed_users"] = users
if token:
    body["telegram_token"] = token
print(json.dumps(body))
PY
)"
REPORT="$(api_call POST "/api/agents/$AGENT_ID/promote" "$PROMOTE_BODY")"
note "token source: $(json_get "$REPORT" telegram_token_source || echo '?')"
DROPPED="$(json_get "$REPORT" dropped_config_keys || echo '[]')"
if [ "$DROPPED" != "[]" ] && [ -n "$DROPPED" ]; then
  warn "old config keys not carried into the managed template: $DROPPED"
  warn "(archived at $(json_get "$REPORT" backup_path || echo 'config.yaml.pre-promote.bak'))"
fi
note "promoted: managed, in the A2A mesh$([ "$MAKE_LEAD" = 1 ] && printf ', head of staff')"

step "Starting the gateway HERE (this is the moment polling begins)"
api_call POST "/api/agents/$AGENT_ID/resume" >/dev/null
sleep 3

step "Verifying"
if sctl is-active --quiet "$UNIT"; then
  note "$UNIT is active"
else
  warn "$UNIT is NOT active — check: journalctl $([ "$(systemd_scope)" = user ] && printf -- '--user ')-u $UNIT -n 50"
  die "gateway failed to start. Roll back with: $0 $AGENT_ID $OLD_HOST --rollback"
fi

# Telegram's own 409 text is stable; Hermes' log lines are VERIFY.
if jrnl -u "$UNIT" -n 80 --no-pager 2>/dev/null | grep -qiE '409|conflict.*getupdates|terminated by other'; then
  warn "the journal mentions a getUpdates conflict — something else may still hold the bot's connection. Investigate before trusting this cutover."
else
  note "no getUpdates conflict in the journal"
fi

TOKEN_LINE="$(grep '^TELEGRAM_BOT_TOKEN=' "$RECONS_ROOT/agents/$AGENT_ID/service.env" 2>/dev/null | head -1 | cut -d= -f2- || true)"
if [ -n "$TOKEN_LINE" ]; then
  # getMe is safe — it never touches the live getUpdates poll. NEVER call
  # getUpdates here: that would steal the connection we're verifying.
  if curl -s --max-time 10 "https://api.telegram.org/bot$TOKEN_LINE/getMe" | grep -q '"ok":true'; then
    note "bot token valid (getMe ok)"
  else
    warn "getMe did not confirm the token — check the token and network"
  fi
else
  warn "no TELEGRAM_BOT_TOKEN in service.env — the mesh should have written it"
fi

if [ "$YES" != 1 ] && [ -t 0 ]; then
  printf '\n   Now DM the bot from your phone. Did the reply arrive? [y/N] '
  read -r answer
  case "$answer" in
    y|Y|yes|YES) note "end-to-end confirmed" ;;
    *) warn "no reply seen — check 'journalctl -u $UNIT' and GET $API/api/agents/$AGENT_ID/history; roll back with --rollback if needed" ;;
  esac
else
  note "skipping the DM round-trip prompt (non-interactive) — send the bot a message and check /api/agents/$AGENT_ID/history"
fi

printf '\n\033[32mCutover complete.\033[0m\n'
note "Same bot, same chat — answered from this machine now, as your head of staff."
note "The old machine stays stopped; 'migrate-hermes.sh sync' now refuses (by design)."
note "Retire the old VPS when comfortable — the runbook is docs/35-telegram-cutover.md."