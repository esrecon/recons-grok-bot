# 4b. Moving your Telegram bot here (the cutover)

You migrated a Hermes agent from another machine with
`migrate-hermes.sh` ([70-existing-hermes-and-buzz.md](70-existing-hermes-and-buzz.md)),
and its Telegram bot still answers from the old machine. This runbook moves
the bot **here** and makes the agent your **head of staff** — the lead that
triages what you text it and delegates to the rest of the roster over A2A.

## The one rule everything else follows from

**One bot token supports exactly one live connection.** Telegram rejects a
second `getUpdates` poller with `409 Conflict`. So the old gateway is always
stopped — reboot-proof — *before* anything here starts. That ordering is the
whole reason the cutover is a script and not a dashboard button.

## Prerequisites

- The snapshot exists here and works in the dashboard (`migrate-hermes.sh
  status` is green).
- Hermes on this VPS is at least as new as on the old machine
  (`./scripts/update.sh` first if not) — its copied `state.db` schema must be
  readable.
- You know your **numeric Telegram user id** (the allowlist does not travel
  from the old machine — it lived in that machine's environment, not in the
  copied home).
- SSH to the old machine works from here (same tailnet).

## The cutover

```bash
./scripts/telegram-cutover.sh hermes old-vps.tailXXXX.ts.net \
    --allowed-users <your-telegram-user-id>
```

Useful flags: `--old-unit <name>` if the old gateway runs under systemd (so
it is disabled, not just killed), `--token <bot-token>` if the token can't be
extracted from the copied home, `--no-lead` to skip the head-of-staff
promotion, `--yes` for non-interactive runs.

What it does, in order:

1. **Stops and disables** the gateway on the old machine, and verifies no
   `hermes gateway` process remains.
2. **Final sync** (`migrate-hermes.sh sync --final`): a consistent copy of the
   now-quiescent databases, the last Telegram turns, and — only at this
   grade — the agent's own `auth.json`, replacing the interim shared-auth
   symlink.
3. **Preflight** (`migrate-hermes.sh cutover-check`): snapshot is
   cutover-grade and fresh, agent registered and still imported, no local
   gateway unit active, Hermes versions compatible.
4. **Promote** (`POST /api/agents/<id>/promote`): the agent becomes
   *managed* — the orchestrator now owns its `config.yaml`/`service.env`, it
   joins the A2A mesh (every peer already carried tokens for it), the
   Telegram block renders into its config with the real token only in
   `service.env` (chmod 600), it keeps the model it arrived with, its SOUL
   gains the managed head-of-staff team section, and running teammates are
   restarted so they can actually call it. The old config is archived as
   `config.yaml.pre-promote.bak`, and anything the template doesn't carry is
   listed in the output. **Nothing starts yet** — the record comes out
   paused.
5. **Resume** (`POST /api/agents/<id>/resume`): the systemd unit
   `hermes-gateway@<id>` is enabled and started. *This* is the moment the bot
   connects from here.
6. **Verify**: unit active; journal free of `409 Conflict`; `getMe` confirms
   the token (never `getUpdates` — that would steal the very connection under
   test); then you DM the bot and the reply should come from this machine
   (it also appears in the agent's dashboard history).

After that: same bot, same chat thread, answered by your head of staff here.
`migrate-hermes.sh sync` now **refuses** to run — refreshing a live agent
from stale old-machine state would be data loss, which is exactly what you
want it to refuse.

## Rollback

```bash
./scripts/telegram-cutover.sh hermes old-vps.tailXXXX.ts.net --rollback
```

Stops/disables the gateway here, demotes the agent (restores
`config.yaml.pre-promote.bak`, deletes the generated `service.env` so an
accidental start fails closed, Telegram off, `imported=true` again, lead
handed back to the oldest managed agent), and re-enables the old machine's
gateway. Messages handled here during the failed window exist only in this
copy's `state.db` and are overwritten by the next sync — accepted losses of a
failed window.

## Worth knowing

- **Pairing**: your DM pairing state lives inside the copied home and should
  survive the move (VERIFY — where Hermes persists approvals can drift by
  version). If the bot asks again, one `hermes pairing approve telegram
  <CODE>` with `HERMES_HOME=/opt/recons/agents/<id>/home` re-pairs.
- **Cron came along**: routines from the old machine now run here (they
  couldn't double-fire — the old gateway is stopped). Review them in the
  Routines tab after cutover.
- **The allowlist is enforced via `TELEGRAM_ALLOWED_USERS`** in the agent's
  `service.env`, regenerated from the roster on every rewire; the roster
  refuses `telegram_enabled` without numeric ids, and CI refuses
  `GATEWAY_ALLOW_ALL_USERS` anywhere ([60-security-hardening.md](60-security-hardening.md)).
- **Managed agents** can also get a bot without any migration: Settings →
  Telegram → paste a fresh BotFather token. The write-only rule from provider
  keys applies — the server reports *that* a token is stored, never the token.
- If the installed Hermes ignores the config-block/env token (`# VERIFY`
  markers in the template), the fallback is one interactive
  `HERMES_HOME=/opt/recons/agents/<id>/home hermes gateway setup` run —
  everything else in this runbook stands.

## Verify

- The old machine has no `hermes gateway` process and the unit is disabled
- `systemctl --user is-active hermes-gateway@<id>` (or system scope) is active
- The journal shows no `409 Conflict: terminated by other getUpdates request`
- A DM gets a reply, and the turn shows in the agent's dashboard history
- `migrate-hermes.sh status` reports "PROMOTED … sync is now refused"
- A teammate reachable over A2A: ask the head of staff to `a2a_list`
