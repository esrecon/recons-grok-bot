# 4. Your phone

Two ways to reach your agents from Android. The PWA is the main one; a messaging
channel is a useful fallback when you're off the tailnet.

## Primary: the installed PWA

Covered in [15-tailscale.md](15-tailscale.md#install-the-app-on-your-android-phone).
Short version: install Tailscale, open `https://<magicdns-name>/` in Chrome, tap
**Install app**.

What you get:

- The same dashboard as your PC — same agents, same conversations, same audit
  log — because it *is* the same app talking to the same orchestrator. Nothing to
  sync.
- A home-screen icon that opens full-screen with no browser chrome.
- Single-pane navigation on phone widths: the roster is the home screen, tapping
  an agent pushes the conversation with a **‹ Agents** back bar.
- Approval cards inline in chat, so you can approve or deny from your pocket.

It needs Tailscale connected. That's the deliberate trade: your agent control
panel is never on the public internet.

## Fallback: a messaging channel

If you want your agents reachable when Tailscale isn't connected — or you just
prefer texting them — Hermes has native messaging gateways (Telegram, WhatsApp,
Signal, and ~25 more). Telegram is the least friction:

```bash
hermes gateway setup      # choose Telegram, paste the BotFather token
```

Then lock it down (this matters — an open gateway is an open door):

```
TELEGRAM_ALLOWED_USERS=<your-telegram-user-id>
```

Never set `GATEWAY_ALLOW_ALL_USERS=true`. Pair your account with
`hermes pairing approve telegram <CODE>`.

> Already have a Telegram-connected Hermes on another machine? Don't set up a
> second bot — migrate the agent here ([70-existing-hermes-and-buzz.md](70-existing-hermes-and-buzz.md))
> and move the bot at cutover ([35-telegram-cutover.md](35-telegram-cutover.md)).
> One bot token supports only one live connection, so the old gateway is
> stopped before the bot is enabled here.

Channel messages carry the same approval prompts, and voice notes are
transcribed. Each agent can have its own bot, or one bot can front the lead
agent.

Caveat: messages route through Telegram's servers, unlike the PWA which stays
inside your tailnet. Decide per your own sensitivity.

## What phones can't do here

There's no official Hermes mobile app, and the phone is not a device node — no
camera, contacts or notification access from the agent side. If you want an
agent to *use* the phone rather than just talk to it, that's a Termux install on
the phone itself, which is out of scope for this kit.

## Verify

- The PWA installs and opens full-screen from the home screen icon
- Roster → chat → back works with one hand
- With Tailscale **off** (mobile data), the URL fails to load
- With Tailscale **on**, it loads and streams a reply
- An approval card can be approved from the phone
