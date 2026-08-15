# 6. Agents, identity, and how they talk

## What one agent is

Each agent is a Hermes **profile** — a completely separate `HERMES_HOME` with its
own config, identity, memory and history — plus a systemd unit and a slot in the
A2A mesh.

```
/opt/recons/agents/scout/
├── home/
│   ├── SOUL.md          ← identity + job. Yours to edit. Never overwritten.
│   ├── config.yaml      ← GENERATED. Do not hand-edit; rewritten on roster change.
│   ├── state.db         ← sessions, messages, tool calls (feeds the audit log)
│   ├── a2a_audit.jsonl  ← every agent-to-agent exchange
│   ├── memory/          ← what it remembers about you and its work
│   └── cron/            ← its routines
└── service.env          ← HERMES_HOME + its A2A tokens. chmod 600.
```

Shared, not duplicated: `/opt/recons/shared/skills/` (the skill library) and
`/opt/recons/shared/secrets.env` (API keys). Teach one agent something and
approve it, and every agent has it.

## SOUL.md — the thing that makes an agent *someone*

Generated from the job you type in the New-agent modal, then it's yours. It
carries the name, the job, optional standing rules, how to work with teammates,
and the ground rules (stay in your lane, never take irreversible actions
unasked, never print secrets).

Edit it whenever the agent's role changes:

```bash
nano /opt/recons/agents/scout/home/SOUL.md
systemctl --user restart hermes-gateway@scout
```

Good job descriptions are specific and bounded — *"Researches suppliers, drafts
outreach emails, never sends them"* beats *"helps with sales"*. The description
is also how the lead agent decides who to delegate to.

## The A2A mesh

Every ordered pair of agents gets **its own bearer token**:

```
recon ──token A──▶ scout        scout ──token B──▶ recon
```

Two independent secrets, not one shared one. That means each direction is
separately authenticated, separately rate-limited, and separately revocable — and
the audit ledger can attribute every message to a specific caller.

Where they live:

- **`config.yaml`** — the peers an agent may call, with `${A2A_TOKEN_SCOUT}`
  placeholders. No secrets in this file.
- **`service.env`** (chmod 600) — the actual values: `A2A_PEER_TOKENS` for
  inbound callers, `A2A_TOKEN_<PEER>` for outbound calls.
- **`shared/a2a-tokens.json`** (chmod 600) — the orchestrator's edge store, so
  existing edges keep their tokens when the roster changes and only genuinely new
  edges mint new secrets.

All of this is regenerated automatically whenever you add or remove an agent. You
never hand-manage it.

## Getting agents to work together

Ask the lead agent in plain language:

> "Ask Scout to price these three suppliers, then summarise what comes back."

Hermes gives agents `a2a_list` (who's available), `a2a_call` (hand off a task),
`a2a_orchestrate` (fan out to several) and `a2a_history`. Every call and reply
appends to `a2a_audit.jsonl`, which the **Audit log** picks up and renders as
`Recon → Scout` with both halves linked by session key.

Practical notes:

- **Give jobs, not instructions.** "Ask Scout to price these" works because
  Scout's own `SOUL.md` tells it how to price things.
- **Loops are capped.** Hermes limits ping-pong turns between agents, so two
  agents can't chat forever on your budget.
- **Peers can't run your slash commands.** An inbound A2A message can't make an
  agent execute operator commands, and inbound content is prompt-injection
  filtered.
- **Subagents are different.** `delegate_task` spawns a short-lived child of the
  *same* agent for parallel work; A2A is between your named, permanent agents.
  Both show in the ledger.

## Lifecycle

| Action | What happens |
|---|---|
| **Create** | Roster entry, SOUL.md, config, mesh rewire, unit enabled |
| **Pause** | `systemctl --user stop hermes-gateway@<id>`; keeps all state |
| **Resume** | Unit re-enabled; picks up where it left off |
| **Delete** | Unit disabled, removed from roster and mesh, tokens pruned — but its **HERMES_HOME stays on disk** (transcripts are business data) |

To reclaim a deleted agent's disk, remove `/opt/recons/agents/<id>` yourself
after backing it up.

## Verify

- Each agent answers on its own tier and knows its job when asked "what do you do?"
- `Recon` can delegate to `Scout`, and both halves appear joined in the Audit log
- An A2A call with a wrong/missing token is rejected
- `grep -c A2A_TOKEN /opt/recons/agents/*/service.env` shows N-1 tokens per agent
