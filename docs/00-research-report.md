# Deep-research report: cloning Grok Bot, self-hosted

_Compiled August 2026. This report is the "why" behind every design decision in this repo. All
external facts carry sources; where the situation is genuinely ambiguous (notably the Claude
subscription terms of service), that is stated plainly rather than smoothed over._

---

## 1. What "Grok Bot" actually is

**Grok Bot** is a real product, launched in early beta on **11 August 2026** by xAI (operating as
"SpaceXAI" after the SpaceX–xAI merger) together with Cursor. Tagline: _"AI teammates you can give
real work to."_ Confidence in the identification is very high — it is documented on official pages.

The model:

- Up to **50 named "Bots" per account**, each with a name, a job description, its own conversation,
  and durable memory. You treat them like colleagues.
- All of a user's Bots share **one persistent cloud Linux computer** (browser + terminal +
  filesystem, `/workspace` persists). Files, browser cookies and logins are account-wide — xAI
  explicitly warns "Do not use separate Bots as a security boundary."
- Bots run in parallel, **message each other, share context, hand off work, and coordinate in group
  chats**.
- **Skills** — reusable task instructions shared across Bots, invoked with `/`, created either by
  writing instructions or by a **"teach a task" screen-recording demonstration** (up to 10 minutes,
  produces a draft skill needing refinement).
- **Routines** — per-Bot scheduled or event-triggered automations.
- **Human-in-the-loop** — live takeover of the "Agent Computer" for passwords/2FA/CAPTCHAs/payments,
  with masked secret entry; plus approval gates and a model-based "Auto Review" for risky actions.
- **Background execution** independent of the user's device, with full desktop ↔ iPhone sync
  including a live view of a Bot's screen.

Platforms at launch: macOS, Windows, iPhone (iOS 18+). **No Linux desktop, no Android, no iPad.**
Pricing: no standalone price — bundled with SuperGrok Heavy ($300/mo), Cursor Ultra ($200/mo), or
Cursor Teams Premium ($120/seat/mo).

**Known beta gaps (per the eesel.ai review):** no dry-run mode, **no audit trail yet** ("An audit
view of Bot actions is coming"), no compliance certifications. Our build treats the audit trail as a
first-class feature precisely because the product Tony is cloning doesn't have one.

Commentators consistently frame Grok Bot as a **managed, closed-source take on OpenClaw** — Neowin's
headline was literally "OpenClaw-style Grok Bot," and the first substantive reply in the Hacker News
launch thread was "so like OpenClaw ??".

Sources: [Introducing Grok Bot](https://x.ai/news/introducing-grok-bot) ·
[Grok Bot product page](https://x.ai/bot) · [Grok Bot docs](https://docs.x.ai/grok-bot/overview) ·
[Skills and routines](https://docs.x.ai/grok-bot/skills-routines-and-automations) ·
[Approvals, security, privacy](https://docs.x.ai/grok-bot/approvals-security-and-privacy) ·
[9to5Mac launch coverage](https://9to5mac.com/2026/08/11/grok-bot-is-an-all-new-iphone-and-mac-app-from-spacexai-and-cursor/) ·
[eesel review of the beta](https://www.eesel.ai/blog/grok-bot-review) ·
[Neowin: "OpenClaw-style Grok Bot"](https://www.neowin.net/news/spacexai-launches-openclaw-style-grok-bot-that-can-work-across-apps-on-its-own/)

---

## 2. The repo Tony linked: OpenMausBot

`milind-soni/OpenMausBot` is real, MIT-licensed, and the canonical upstream (not a fork) — but it is
**only four days old** (created 11 Aug 2026, i.e. within a day of Grok Bot's launch) and is a
**desktop chat cockpit** for agent CLIs you've already logged into (Claude Code, Codex, Grok). It has
**no messaging channels, no Android, no browser-automation subsystem, no skills system, no remote
gateway** — i.e. it is missing most of Tony's requirements.

It also already has **impersonation copies** appearing on GitHub (`andreycurious252/OpenMausBot` and
others), which fits a known pattern: the OpenClaw ecosystem saw fake installers pushing GhostSocks
infostealers and ~400 malicious "skills." If OpenMausBot is ever installed, install **only** from
`milind-soni/OpenMausBot`.

Conclusion: not a viable base. This is why the project pivoted to a full framework.

Sources: [OpenMausBot repo](https://github.com/milind-soni/OpenMausBot) ·
[Huntress: fake OpenClaw installers → GhostSocks](https://www.huntress.com/blog/openclaw-github-ghostsocks-infostealer) ·
[Infosecurity: malicious skills in the ecosystem](https://www.infosecurity-magazine.com/news/malicious-crypto-trading-skills/)

---

## 3. Why Hermes Agent is the base (chosen over OpenClaw)

Two open-source frameworks can host a self-hosted Grok Bot equivalent: **OpenClaw**
(github.com/openclaw/openclaw) and **Hermes Agent** (github.com/NousResearch/hermes-agent). Both are
mature (hundreds of thousands of GitHub stars). Tony chose **Hermes Agent** as the more robust base;
it also ships an official `hermes claw migrate` command, so nothing from the OpenClaw world is lost.

Hermes Agent, version **v0.20.1** ("Herald" line, August 2026), maps onto the requirements as
follows. ✅ = native, ⚠️ = needs the glue this repo provides.

| Grok Bot capability | Hermes Agent |
|---|---|
| Named permanent agents, each own identity/memory/role | ✅ **Profiles** — each is a separate `HERMES_HOME` with its own `config.yaml`, `SOUL.md`, memory, sessions |
| Agents talk to each other | ✅ **A2A v1.0** (Linux Foundation standard) — server + client, `a2a_call`/`a2a_orchestrate`, per-peer bearer tokens |
| Full audit transcript | ⚠️ Data exists (`state.db`, append-only `a2a_audit.jsonl`, cron `executions.db`, signed webhooks) but is **not merged** — we build the merged ledger |
| Skills, "/" invocation, teach-by-demonstration | ✅ agentskills.io standard + **`/learn`** + autonomous `skill_manage` with staged approval; ⚠️ we add structure + guardrails |
| Routines | ✅ built-in **cron** (natural-language schedules, per-channel delivery) |
| Approvals for risky actions | ✅ `approvals.mode: smart` (LLM risk-scoring) + hardline blocklist |
| Background execution 24/7 | ✅ `hermes gateway` daemon as a systemd service + `loginctl enable-linger` |
| Use websites natively | ✅ browser tool, **CDP attach to a real logged-in Chrome**; ⚠️ no extension relay, no file downloads |
| Phone, live sync | ✅ via messaging gateways (Telegram/WhatsApp/Signal, inherently live); ⚠️ **no official app** → we ship a PWA |
| The Grok-style dashboard itself | ⚠️ Hermes has a functional admin dashboard, but not the Grok Bot look → **we build it** |

Architecture: one codebase, multiple entry points — `hermes` (CLI/TUI), `hermes gateway` (the
long-running daemon hosting all messaging adapters, authorization, cron), `hermes dashboard`/`serve`
(web UI + JSON-RPC/WebSocket on `127.0.0.1:9119`), and an **OpenAI-compatible API server**
(`/v1/chat/completions`, `/v1/responses` with SSE, Runs/Jobs APIs on port 8642). Runtime is Python
3.11 (managed by `uv`) plus Node 22. "Run it on a $5 VPS." No built-in TLS anywhere — a reverse
proxy, `tailscale serve`, or plain tailnet WireGuard is assumed.

**Sessions/transcripts** (the audit-viewer's inputs): the canonical store is **SQLite** at
`~/.hermes/state.db` (WAL mode; `sessions` and `messages` tables with tool-call JSON, cost/token
counts, and `parent_session_id` delegate lineage; FTS5 search). A2A exchanges additionally append to
**`~/.hermes/a2a_audit.jsonl`**. Cron runs are in `~/.hermes/cron/executions.db`. **Outbound
HMAC-SHA256 signed webhooks** fire on every lifecycle event (`pre_tool_call`, `post_tool_call`,
`on_session_start`, `subagent_start`, …) — the cleanest live feed for a ledger. There is **no native
merged cross-source timeline**, which is exactly the gap we fill.

Sources: [Hermes Agent repo](https://github.com/NousResearch/hermes-agent) ·
[Architecture](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture) ·
[Session storage](https://hermes-agent.nousresearch.com/docs/developer-guide/session-storage) ·
[A2A](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/a2a) ·
[Webhooks](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/webhooks) ·
[Skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills) ·
[API server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server/)

---

## 4. Providers and the subscription ToS situation

Tony wants to power the system with subscriptions he already pays for (Claude, ChatGPT), plus Hermes
models and API keys. As of August 2026 the three providers sit in very different places.

### Claude (Anthropic) — the contested one

The timeline matters:

- **9 Jan 2026** — Anthropic began server-side blocking of third-party tools using consumer OAuth
  tokens ("This credential is only authorized for use with Claude Code…").
- **19 Feb 2026** — docs updated: using OAuth tokens from Free/Pro/Max accounts "in any other
  product, tool, or service, including the Agent SDK, is not permitted."
- **4 Apr 2026** — **Claude subscriptions stopped covering third-party harnesses** (OpenClaw, Cline,
  etc.). You could still use them via **extra-usage bundles** or a **Claude API key**.
- **13 May 2026** — announced a partial reinstatement (a separate monthly Agent SDK credit)…
- **15 Jun 2026** — **…and paused that change on the day it was due.** The live help-center article
  still reads: _"For now, nothing has changed: Claude Agent SDK, `claude -p`, and third-party app
  usage still draw from your subscription's usage limits."_

So there is a **genuine, documented contradiction** in Anthropic's own material: the legal/compliance
page says third-party OAuth use is not permitted and developers should use API keys, while the Agent
SDK help article says `claude -p`/Agent SDK use still draws from subscription limits. Enforcement in
practice has been light since April (the one high-profile ban, of OpenClaw's creator, was reversed
within hours), but the risk is real and the policy is under active revision.

**What this repo does about it (Tony's chosen route — Agent SDK / `claude -p` on his plan):**

1. Claude is provided to agents via **claude-code-openai-wrapper** (a community project that puts the
   **official Claude Agent SDK** behind an OpenAI-compatible endpoint), authenticated with Tony's own
   Claude login. This is the same _mechanism family_ as Buzz by Block, which spawns the logged-in
   Claude Code CLI. Pinned to a reviewed commit.
2. **Claude is pinned to the single lead agent (Recon) for interactive work only.** Bulk and
   background work goes to GPT and Nous, so Claude usage stays shaped like ordinary individual use.
3. The fallback is **one environment flip**: switch the provider to native `anthropic` with a
   **Claude Console API key** (fully compliant, pay-as-you-go — Anthropic's own recommendation for
   automation). If Anthropic un-pauses the billing change, this is the switch to throw.

Sources: [Claude Code legal & compliance](https://code.claude.com/docs/en/legal-and-compliance) ·
[Use the Claude Agent SDK with your plan (June 15 pause note)](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan) ·
[Boris Cherny announcement, Apr 3 2026](https://www.threads.com/@boris_cherny/post/DWsAWeND5nm/) ·
[TechCrunch: subscribers pay extra for OpenClaw usage](https://techcrunch.com/2026/04/04/anthropic-says-claude-code-subscribers-will-need-to-pay-extra-for-openclaw-support/) ·
[claude-code-openai-wrapper](https://github.com/RichardAtCT/claude-code-openai-wrapper)

### ChatGPT (OpenAI) — the blessed one

The opposite posture. "Sign in with ChatGPT" is officially supported in Codex, and on ~1 May 2026
**Sam Altman publicly endorsed signing into OpenClaw with a ChatGPT account** ("happy lobstering").
Hermes has **native** support: `hermes model` → "ChatGPT or Codex Subscription" runs a device-code
OAuth flow, and it can even drive the Codex CLI as a harness. This makes a flat-rate ChatGPT Plus/Pro
subscription the **always-on workhorse** — designated for Scout and the default fallback chains.

Sources: [Codex auth docs](https://learn.chatgpt.com/docs/auth) ·
[TNW: OpenAI opens ChatGPT subscriptions to OpenClaw](https://thenextweb.com/news/openai-openclaw-chatgpt-subscription-agent)

### Hermes models (Nous Research) — the cheap one

"Hermes" here also refers to Nous Research's own open models. Via the **Nous Portal** OpenAI-compatible
API (`https://inference-api.nousresearch.com/v1`), Hermes-4-70B runs around **$0.05 in / $0.20 out per
million tokens** — one to two orders of magnitude cheaper than GPT-5.x. Perfect for bulk work (agent
"Clerk") and for Hermes's **11 auxiliary task slots** (approval scoring, compression, title
generation, tool routing, …).

Sources: [Nous Portal](https://hermes-agent.nousresearch.com/docs/integrations/nous-portal) ·
[Hermes 4.3 on Hugging Face](https://huggingface.co/NousResearch/Hermes-4.3-36B)

### Buzz by Block

Tony uses **Buzz** (buzz.xyz — Block/Jack Dorsey's open-source, Nostr-based team chat where AI agents
are channel members). Buzz powers agents by spawning a local agent CLI over the Agent Client Protocol
(ACP), using your **Claude Code or Codex subscription** — the same subscription-reuse mechanism this
repo uses for Claude. Nous ships **three official Hermes ↔ Buzz integration paths**, so Tony's agents
can appear as members of his Buzz channels. The one rule: **don't drive the same Claude login from
both Buzz's harness and this repo's wrapper simultaneously** — it compounds rate limits and the ToS
ambiguity. See `docs/70-existing-hermes-and-buzz.md`.

Sources: [Buzz + Hermes integration paths](https://www.marktechpost.com/2026/07/31/nous-research-ships-three-integration-paths-for-hermes-agent-and-buzz-blocks-open-source-nostr-workspace-for-humans-and-agents/) ·
[Buzz by Block setup](https://www.dplooy.com/blog/buzz-by-block-setup-claude-code-agents-channels)

---

## 5. Security reality check

This is not paranoia — it is the documented recent history of exactly this class of software:

- **OpenClaw:** SecurityScorecard found **40,214 exposed instances** on the public internet
  (Feb 2026), ~12,800 of them RCE-exploitable. Koi Security's "ClawHavoc" report found **341 malicious
  skills** on the community skill hub, delivering Atomic Stealer.
- **Hermes Agent:** real 2026 CVEs including **CVE-2026-10223** (RCE in the memory tool, ≤ 2026.4.30,
  public PoC), **CVE-2026-9366** (unauthenticated prompt-pipeline injection), and **CVE-2026-11461**
  (session authorization bypass ≤ 0.12.0). All are patched in current releases — which is precisely
  why **pinning ≥ v0.20.1 and keeping current is a security control**, not just hygiene.

The hardening baked into this repo's defaults (full list in `docs/60-security-hardening.md`): every
service binds loopback and is exposed only through `tailscale serve`; nothing touches the public
internet; A2A uses per-peer bearer tokens with rate limits; messaging gateways use DM pairing +
allowlists (never `GATEWAY_ALLOW_ALL_USERS`); the VPS terminal backend runs in Docker with
`--cap-drop ALL`; agent approvals stay on and YOLO mode is never enabled; agent-created skills require
human approval and hub installs are audited; secrets live in one `chmod 600` file, server-side only.

Sources: [40,000+ exposed OpenClaw instances](https://www.infosecurity-magazine.com/news/researchers-40000-exposed-openclaw/) ·
[Koi Security: 341 malicious skills](https://www.koi.ai/blog/clawhavoc-341-malicious-clawedbot-skills-found-by-the-bot-they-were-targeting) ·
[CVE-2026-10223](https://www.sentinelone.com/vulnerability-database/cve-2026-10223/) ·
[Hermes security docs](https://hermes-agent.nousresearch.com/docs/user-guide/security)

---

## 6. What we build on top of Hermes (the gaps)

1. **The Grok Bot-style dashboard** (`apps/dashboard/`) — Hermes's own admin UI works but looks
   nothing like Grok Bot. We build the roster/chat/skills/routines/audit/settings UI to match.
2. **One-click permanent-agent provisioning** (`apps/orchestrator/`) — Hermes profiles are created via
   CLI; we wrap that in a single API call that generates a `SOUL.md` from a job-role form, wires shared
   skills + shared API keys, allocates A2A tokens, updates the peer mesh, and starts the systemd unit.
3. **The merged audit ledger** — the feature Grok Bot itself lacks: one chronological, filterable
   timeline across every agent's `state.db`, the `a2a_audit.jsonl`, cron runs, and the live signed-
   webhook feed.
4. **Teach-mode structure** — a thin layer over Hermes's native `/learn` + staged-approval skill
   creation, adding a deterministic validator and a mandatory guardrail block.
5. **The PWA** — because Hermes has no official mobile app, the dashboard is an installable PWA served
   over the tailnet with real TLS.
