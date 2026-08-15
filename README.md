# Recons Grok Bot

A self-hosted "AI teammates" platform for Essex Recons: a Grok Bot-style dashboard on top of a
[Hermes Agent](https://github.com/NousResearch/hermes-agent) backend. Named, **permanent agents** with
their own identity (`SOUL.md`) and job role that browse real websites, run 24/7 on your own VPS,
learn skills from demonstrated workflows, talk to each other over A2A — with a **full audit
transcript of every exchange** — and reach your phone as an installable PWA over Tailscale.

> Grok Bot (x.ai/bot) is xAI's closed, bundled-subscription product. This project is an independent,
> private, self-hosted equivalent built on open-source components. It is not affiliated with xAI,
> and it deliberately ships the one thing Grok Bot's beta lacks: an audit trail.

## Architecture

```
┌────────────────────── Tailscale tailnet (nothing public) ──────────────────────┐
│ VPS (Ubuntu 24.04)                                Real-world PC (Windows 11)   │
│ ── ORCHESTRATOR (FastAPI, loopback :8330)         ── Hermes instance "Deck"    │
│    · serves the dashboard SPA + PWA                  (A2A peer: terminal/files │
│    · one-click agent provisioning (profiles)          on the real PC)          │
│    · proxies chat to each profile's API (SSE)     ── Chrome remote-debugging   │
│    · audit ledger API + signed-webhook receiver       → CDP logged-in browsing │
│ ── Hermes profiles (one per agent, systemd        Android phone                │
│    template unit hermes-gateway@<name>):          ── PWA (installed icon) over │
│    Recon (lead) · Scout (browser) · Clerk (bulk)      tailnet TLS              │
│    + any created later via one click              ── Telegram gateway optional │
│    shared: /opt/recons/shared/{skills,secrets}        fallback channel         │
│    own: SOUL.md, memory, sessions per profile     Other PC → same dashboard    │
│ ── claude-code-openai-wrapper (loopback)              URL in a browser         │
│ ── A2A mesh (per-peer tokens) + Buzz platform                                  │
│ Existing hermes-agent instance ⇄ A2A peer                                      │
│ `tailscale serve`: / → :8330 dashboard · /hermes → :9119 admin (fallback)      │
└────────────────────────────────────────────────────────────────────────────────┘
```

## What's in this repo

| Path | What it is |
|---|---|
| `apps/dashboard/` | The Grok Bot-style web UI (React + Vite + Tailwind, installable PWA): agent roster, chat, Skills, Routines, **Audit**, Settings, one-click **New agent** |
| `apps/orchestrator/` | FastAPI service: one-click agent provisioning (Hermes profiles), chat proxy (SSE), merged audit ledger + signed-webhook receiver, operator login |
| `config/` | Profile templates (config.yaml, SOUL.md skeleton), A2A peer map, systemd units, shared skills/secrets layout |
| `scripts/` | `vps-bootstrap.sh`, `vps-verify.sh`, backup/restore/update, `check-all.sh` (CI entrypoint) |
| `tools/teach-mode/` | Workflow-demonstration → skill drafting helpers (`/learn` structure, `validate_skill.py`, guardrails) |
| `docs/` | Numbered runbooks in install order, the full research report, security hardening, acceptance checklist |
| `skills/` | Vendored workspace skills shared by all agents |

## Start here

Read the docs in numeric order — they match the install order:

1. [docs/00-overview.md](docs/00-overview.md) — architecture, threat model, conventions
2. [docs/10-vps.md](docs/10-vps.md) — VPS, Hermes, orchestrator, your first agents
3. [docs/15-tailscale.md](docs/15-tailscale.md) — tailnet-only access + installing the PWA on Android
4. [docs/20-windows-pc.md](docs/20-windows-pc.md) — your real PC as an A2A peer + logged-in Chrome
5. [docs/30-phone.md](docs/30-phone.md) — phone access and messaging fallback
6. [docs/40-providers-and-tos.md](docs/40-providers-and-tos.md) — Claude, ChatGPT, Nous, and the honest ToS position
7. [docs/50-agents-a2a.md](docs/50-agents-a2a.md) — agent identity and how they talk
8. [docs/60-security-hardening.md](docs/60-security-hardening.md) — the non-negotiable defaults, and why
9. [docs/70-existing-hermes-and-buzz.md](docs/70-existing-hermes-and-buzz.md) — peer an existing Hermes; Buzz
10. [docs/80-backup-update.md](docs/80-backup-update.md) — backups, restores, updates
11. [docs/99-acceptance-checklist.md](docs/99-acceptance-checklist.md) — prove it all works, tick by tick

Full deep-research findings (what Grok Bot is, why Hermes, the subscription ToS situation as of
August 2026) live in [docs/00-research-report.md](docs/00-research-report.md).

## Quick start

On a fresh Ubuntu 24.04 VPS:

```bash
git clone https://github.com/esrecon/recons-grok-bot /opt/recons/app
sudo /opt/recons/app/scripts/vps-quickstart.sh
```

That's the whole server-side install — packages, firewall, Tailscale, Hermes, the
orchestrator, the dashboard, and a private HTTPS address on your tailnet. It
prints your URL when it's done.

**Everything after that happens in the app**: connect a model provider (paste a
key, or sign in with your ChatGPT subscription — the link and code appear in the
app), then create your first agent. No config files, no keys in the terminal.

On your phone: install Tailscale, open the same URL in Chrome, tap **Install app**.

## Development

```bash
./scripts/check-all.sh   # everything CI runs: config validation, tests, builds, lints
```

- Orchestrator: Python 3.11, `uv`, FastAPI — `cd apps/orchestrator && uv run pytest`
- Dashboard: Node 22 — `cd apps/dashboard && npm install && npm run build`

## Security posture (summary — details in docs/60)

Every service binds loopback and is published only via `tailscale serve`. Nothing is ever exposed
to the public internet. Hermes is pinned ≥ v0.20.1 (2026 CVE history makes update cadence a
security control). Agent approvals stay on; skills self-created by agents require explicit human
approval; API keys live in one shared, `chmod 600` secrets file, server-side only.

## License

MIT — see `LICENSE`.
