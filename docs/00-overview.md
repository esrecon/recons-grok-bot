# Overview

## What this is

Your own Grok Bot: named, permanent AI teammates with their own job and identity,
running 24/7 on your VPS, reachable from any device on your Tailscale network,
using real websites through your logged-in browser — with a complete audit
transcript of everything they do and every message they send each other.

The agents themselves are [Hermes Agent](https://github.com/NousResearch/hermes-agent)
profiles. This repo adds the Grok Bot-style dashboard, one-click agent
provisioning, the merged audit ledger, and the deployment kit.

## The pieces

```
┌────────────────────── Tailscale tailnet (nothing public) ──────────────────────┐
│ VPS (Ubuntu 24.04)                                Real-world PC (Windows 11)   │
│ ── ORCHESTRATOR (FastAPI, loopback :8330)         ── Hermes instance "Deck"    │
│    · serves the dashboard SPA + PWA                  (A2A peer: terminal/files │
│    · one-click agent provisioning (profiles)          on the real PC)          │
│    · proxies chat to each profile's API (SSE)     ── Chrome remote-debugging   │
│    · audit ledger API + signed-webhook receiver       → CDP logged-in browsing │
│ ── Hermes profiles (one per agent, systemd        Android phone                │
│    template unit hermes-gateway@<name>)           ── PWA (installed icon) over │
│    shared: /opt/recons/shared/{skills,secrets}       tailnet TLS               │
│    own: SOUL.md, memory, sessions per profile     Other PC → same dashboard    │
│ ── claude-code-openai-wrapper (loopback)             URL in a browser          │
│ ── A2A mesh (per-peer tokens)                                                  │
└────────────────────────────────────────────────────────────────────────────────┘
```

## The agent roster

Agents are yours to name, but the kit assumes a shape like this:

| Agent | Job | Model tier |
|---|---|---|
| **Recon** | Lead assistant — coordinates the others | Lead (Claude) |
| **Scout** | Research and browsing | Workhorse (ChatGPT subscription) |
| **Clerk** | Admin, invoices, bulk data entry | Bulk (Nous Portal) |

Each agent gets its **own** `SOUL.md` (identity + job) and memory, and **shares**
the skill library and API keys with every other agent. Add more with one click;
the orchestrator wires the new agent into the A2A mesh automatically.

## What's shared vs. what's private

| Shared across all agents | Private per agent |
|---|---|
| Skill library (`shared/skills/`) | `SOUL.md` — name, job, standing rules |
| API keys (`shared/secrets.env`) | Memory and conversation history |
| The audit ledger | Sessions, workspace, cron routines |
| The A2A mesh | Its own model/tier and fallback chain |

## Threat model, in one paragraph

This stack can read your email, drive your logged-in browser, and run commands on
your real PC. That is the point, and it is also the risk. So: nothing is ever
exposed to the public internet (loopback binds + `tailscale serve` only); every
agent-to-agent edge has its own bearer token; risky actions need your approval;
agent-written skills wait for you to approve them; and tool execution on the VPS
runs inside Docker. Researchers found **40,000+ OpenClaw instances exposed on the
public internet** and **341 malicious skills** in a community skill hub, and
Hermes itself has had real 2026 CVEs (all patched in current releases). Those
numbers are why the defaults in this repo are not negotiable — see
[60-security-hardening.md](60-security-hardening.md).

## Reading order

The docs are numbered in install order:

1. **[10-vps.md](10-vps.md)** — the VPS, Hermes, the orchestrator, your first agents
2. **[15-tailscale.md](15-tailscale.md)** — publish to your tailnet; install the PWA on your phone
3. **[20-windows-pc.md](20-windows-pc.md)** — your real PC as an A2A peer + logged-in Chrome
4. **[30-phone.md](30-phone.md)** — phone access, and Telegram as a fallback channel
5. **[40-providers-and-tos.md](40-providers-and-tos.md)** — Claude, ChatGPT and Nous, and the honest ToS position
6. **[50-agents-a2a.md](50-agents-a2a.md)** — how agents are wired and how they talk
7. **[60-security-hardening.md](60-security-hardening.md)** — the defaults, and why
8. **[70-existing-hermes-and-buzz.md](70-existing-hermes-and-buzz.md)** — peer your existing Hermes; Buzz
9. **[80-backup-update.md](80-backup-update.md)** — backups, restores, updates
10. **[99-acceptance-checklist.md](99-acceptance-checklist.md)** — prove it all works

Background research (what Grok Bot is, why Hermes, the subscription ToS timeline)
is in **[00-research-report.md](00-research-report.md)**.

## The `VERIFY` convention

Hermes moves fast — v0.20.1 alone was a ~656-PR rollup. Wherever this repo
depends on an exact Hermes config key, CLI flag, or file location that could
drift between versions, the line is marked:

```yaml
soul_file: SOUL.md   # VERIFY key name
```

`VERIFY` means: **check it against
[hermes-agent.nousresearch.com/docs](https://hermes-agent.nousresearch.com/docs)
for your installed version before trusting it.** Grep for it after every Hermes
upgrade:

```bash
grep -rn "VERIFY" config/ apps/orchestrator/recons_orchestrator/
```

Version-fragile points to expect: the persona file key, environment substitution
in `config.yaml`, the A2A gateway block, sandbox/terminal keys, webhook block and
header names, model ids, and the pending-skills directory.
