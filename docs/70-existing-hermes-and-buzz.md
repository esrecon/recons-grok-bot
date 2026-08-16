# 8. Your existing Hermes instance, and Buzz

## Peering the Hermes instance you already run

Your existing instance doesn't need to move or be rebuilt. Peer it over A2A and
it becomes another teammate — visible in the roster's conversations and, more
importantly, in the audit log.

Requires **v0.20+** on both sides (A2A v1.0 shipped in the "Herald" release):

```bash
hermes --version
```

### On your existing instance

```yaml
# ~/.hermes/config.yaml        VERIFY block/key names
gateway:
  platforms:
    a2a:
      enabled: true
      extra:
        port: 9900
```

```bash
# ~/.hermes/.env
A2A_HOST=0.0.0.0                    # tailnet interface; firewall keeps it private
A2A_PEER_TOKENS=recon:<token-for-recon>
```

Confirm it's answering:

```bash
curl http://<its-tailscale-name>:9900/.well-known/agent-card.json
```

### On the VPS

Add it as a peer to whichever agent should reach it — usually the lead:

```yaml
# /opt/recons/agents/recon/home/config.yaml
a2a_agents:
  hermes_home:
    url: "http://<its-tailscale-name>:9900"
    auth:
      type: bearer
      token: "${A2A_TOKEN_HERMES_HOME}"
```

```bash
# /opt/recons/agents/recon/service.env   (chmod 600)
A2A_TOKEN_HERMES_HOME=<token-for-recon>
```

Restart the agent. Now *"ask my home Hermes what it's been working on"* works,
and both halves land in the Audit log tagged `agent → agent`.

> Remember: the orchestrator regenerates `config.yaml` when the roster changes,
> so re-apply this peer block after adding or deleting agents.

### If you'd rather consolidate: migrate it here

`scripts/migrate-hermes.sh` snapshot-copies the instance onto the VPS while the
original **keeps running** — memory, history and (wherever Hermes stores it)
its Telegram setup all ride along:

```bash
# Day 1 — the old machine stays primary; this just makes a copy here:
./scripts/migrate-hermes.sh pull --from <old-machine-tailscale-name>

# Whenever you want a fresher copy, and a health/drift report:
./scripts/migrate-hermes.sh sync
./scripts/migrate-hermes.sh status
```

The copy lands in `/opt/recons/agents/<id>/home` and is registered as an
**imported** agent: chat and history work in the dashboard immediately, but it
keeps its own config, gets no A2A peers, and no gateway unit is enabled here —
so nothing on the VPS can grab a Telegram bot's single live connection while
the old machine still serves it.

Worth knowing while both copies are live:

- **They diverge.** Chatting with the copy in the dashboard writes to the
  copy's `state.db`; the next `sync` overwrites that (after a warning), saving
  whatever it replaced under `agents/<id>/sync-backups/<stamp>/`.
- **Credentials are not cloned by default.** `--auth shared` (the default)
  symlinks this machine's auth store instead — two live machines refreshing
  one OAuth token can invalidate each other. The final sync brings the
  original `auth.json` across, when the old side is quiescent.
- **Live databases are copied consistently** via remote `sqlite3 ".backup"`
  when available (WAL-aware raw copy otherwise — accepted for interim
  snapshots, required-stopped at cutover grade).

When you're ready to move the Telegram bot here (and make the migrated agent
the head of staff), that is the **cutover**: stop the old gateway, run
`sync --final` and `cutover-check`, then `scripts/telegram-cutover.sh` — the
ordered runbook is [35-telegram-cutover.md](35-telegram-cutover.md). One bot
token supports exactly one live connection, so the old gateway is always
stopped first. After promotion the agent is template-managed: the manual peer
block above becomes obsolete for it, and `migrate-hermes.sh sync` refuses to
overwrite it with stale old-machine state.

Peering is less disruptive; consolidating means one machine to keep patched.

## Buzz by Block

[Buzz](https://buzz.xyz) is Block's open-source Nostr team chat where AI agents
are members of channels. Nous ships **three official ways** to connect Hermes to
Buzz, so your agents can join your channels:

1. **Buzz Desktop managed runtime** — Buzz Desktop discovers `hermes-acp` on
   PATH and spawns Hermes itself. Zero config, but it runs its own local agent
   rather than the ones on your VPS.
2. **`buzz-acp` relay bridge** — bridges Buzz's relay to a Hermes instance you
   already run. This is the one that fits here: your VPS agents, with their
   memory, skills and audit trail intact, appearing in Buzz.
3. **Native Buzz gateway platform** (recommended by Nous) — Buzz becomes a
   first-class messaging platform inside Hermes:

   ```bash
   hermes gateway setup      # choose Buzz; needs a dedicated Nostr keypair
   ```

   Channels, DMs, threads, reactions, approvals and cron delivery all work, and
   the messages flow through the same session storage the audit ledger reads.

Start with option 3 on the lead agent if you want your team (or just you) talking
to the agents in Buzz channels.

### One rule

Buzz spawns your logged-in **Claude Code CLI** over ACP to power its agents —
the same credentials the Claude wrapper here uses. Driving both hard at once
compounds your rate limits and the ToS ambiguity described in
[40-providers-and-tos.md](40-providers-and-tos.md). Pick one to carry the Claude
load and point the other at ChatGPT or Nous.

## Verify

- The peer's agent card responds over the tailnet
- The lead agent can call it and gets a reply
- A call with a wrong token is rejected
- The exchange appears in the Audit log as `agent → agent`
- (If using Buzz) a message in a Buzz channel reaches the agent and the reply
  appears in both Buzz and the Audit log
