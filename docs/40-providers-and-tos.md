# 5. Providers, subscriptions, and the honest ToS position

You pay for Claude and ChatGPT already. This chapter is about using them here
without pretending the rules are simpler than they are.

Three tiers, assigned per agent when you create it:

| Tier | Provider | Used for | Status |
|---|---|---|---|
| **Lead** | Claude, via the Agent SDK wrapper | Your right-hand agent, interactive work | Contested — read below |
| **Workhorse** | ChatGPT/Codex subscription | Everyday agent work, the default | Explicitly endorsed by OpenAI |
| **Bulk** | Nous Portal (Hermes models) | High-volume, background, auxiliary tasks | Ordinary paid API |

## ChatGPT — the workhorse (start here)

OpenAI's position is clear and friendly. "Sign in with ChatGPT" is supported in
Codex, and in May 2026 Sam Altman publicly endorsed signing into third-party
agents (OpenClaw specifically) with a ChatGPT account. Hermes supports it
natively:

```bash
hermes model      # choose "ChatGPT or Codex Subscription", complete device-code login
```

The OAuth token lands in that agent's `auth.json` — nothing goes in
`secrets.env`. Flat rate, no per-token billing, subject to your plan's usage
limits. This is why the kit makes it the **default for every new agent** and the
first fallback in every chain.

## Claude — the lead tier, and the honest caveat

**What happened, in order:**

- **9 Jan 2026** — Anthropic began blocking third-party tools using consumer
  OAuth tokens.
- **19 Feb 2026** — docs updated: using Free/Pro/Max OAuth tokens "in any other
  product, tool, or service, including the Agent SDK, is not permitted."
- **4 Apr 2026** — Claude subscriptions **stopped covering** third-party
  harnesses. Options became extra-usage bundles or an API key.
- **13 May 2026** — a partial reinstatement announced (a separate monthly Agent
  SDK credit)…
- **15 Jun 2026** — **…paused on the day it was due.** The live help-center
  article still reads: *"For now, nothing has changed: Claude Agent SDK,
  `claude -p`, and third-party app usage still draw from your subscription's
  usage limits."*

So Anthropic's own material currently points two ways: the legal/compliance page
says third-party OAuth use isn't permitted and developers should use API keys,
while the Agent SDK help article says Agent SDK usage still draws from your
subscription. That contradiction is real, it is unresolved, and this kit does not
paper over it.

**What this kit does about it:**

1. **Uses the Agent SDK route you chose.** `claude-code-openai-wrapper` runs the
   official **Claude Agent SDK** against your own logged-in Claude CLI and exposes
   an OpenAI-compatible endpoint on loopback. This is the same mechanism family
   as Buzz by Block, which spawns your logged-in Claude Code CLI over ACP.
2. **Confines it to one agent.** Only the **lead** agent is on the Claude tier,
   for interactive work. Bulk and background jobs go to ChatGPT and Nous, so
   usage stays shaped like ordinary individual use rather than a server farm.
3. **Keeps the exit one line away.** If Anthropic un-pauses the billing change,
   or you'd simply rather be unambiguous, switch to a Claude Console API key —
   fully compliant, pay-as-you-go, and Anthropic's own recommendation for
   automation.

**Install the wrapper:**

```bash
git clone https://github.com/RichardAtCT/claude-code-openai-wrapper \
  /opt/recons/claude-wrapper
cd /opt/recons/claude-wrapper
git checkout <reviewed-commit>     # pin it; read the code before you run it
uv sync
systemctl --user enable --now claude-wrapper.service
```

Community software running with your Claude credentials deserves a read-through
first. Pin a commit rather than tracking `main`.

**The one-line fallback**, when you want it — in `secrets.env`:

```bash
ANTHROPIC_API_KEY=sk-ant-...
```

and change the lead agent's provider from `claude_wrapper` to `anthropic` in
`apps/orchestrator/recons_orchestrator/config.py` (the `TIERS` table). Restart
the agent. Done.

## Nous Portal — the bulk tier

Ordinary paid API, no ambiguity. Hermes models at roughly **$0.05 in / $0.20 out
per million tokens** — one to two orders of magnitude cheaper than GPT-5.x.

Get a key at [portal.nousresearch.com](https://portal.nousresearch.com), then in
`secrets.env`:

```bash
NOUS_API_KEY=...
```

Worth using for more than bulk agents: Hermes has ~11 auxiliary task slots
(approval risk-scoring, context compression, title generation, tool routing) that
otherwise burn your expensive tiers. Pointing those at Nous is the single
biggest cost saving available here.

## Failover

Every tier has a fallback chain, so one provider being down or rate-limited
doesn't brick your assistant:

| Tier | Primary | Falls back to |
|---|---|---|
| Lead | Claude (wrapper) | ChatGPT subscription |
| Workhorse | ChatGPT subscription | Nous Portal |
| Bulk | Nous Portal | ChatGPT subscription |

Defined in `TIERS` in `apps/orchestrator/recons_orchestrator/config.py`, rendered
into each agent's `fallback_providers`. Test it deliberately: stop the wrapper
service and confirm the lead agent keeps answering.

> **VERIFY**: model id strings (`claude-sonnet-4-6`, `gpt-5.6-sol`,
> `hermes-4-70b`) change often. Check them against each provider before relying
> on the exact string; they're all marked `VERIFY` in `config.py`.

## Cost sanity

An always-on assistant can burn money quietly. Two flat-rate subscriptions plus a
cheap metered tier is deliberately the cheapest sane shape. Watch it: the audit
log's Export gives you the raw events, and Hermes records per-session token and
cost figures in each agent's `state.db`.

## Don't double-dip your Claude login

If you also run **Buzz by Block**, it spawns your logged-in Claude Code CLI over
ACP — the same credentials this wrapper uses. Running both hard at once compounds
your rate limits and the ToS ambiguity. Pick one to carry the Claude load.
