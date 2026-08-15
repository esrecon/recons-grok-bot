# 3. Your real-world PC

This is what makes the agents useful on *your* machine: running commands on the
real PC, and driving websites in **your already-logged-in Chrome** so the agents
inherit your sessions instead of fighting logins.

Grok Bot gives every bot a shared cloud computer. We do the opposite and better:
the agents reach into the actual PC you use, under approval gates you control.

## The shape

Hermes has no "device node" concept, so the sanctioned pattern is a **second
Hermes instance on the PC**, peered to the VPS over **A2A v1.0**. The VPS agents
call it to run things locally; it appears in the audit ledger like any other
agent.

```
VPS agents  ──A2A (bearer token, over tailnet)──▶  "Deck" (Hermes on your PC)
                                                    ├── terminal + files
                                                    └── Chrome (CDP) — your logins
```

A lighter alternative, if you only need shell access and no browser: skip the
second instance and set `terminal.backend: ssh` on a VPS agent. Covered at the
bottom.

## 1. Install Tailscale and Hermes on the PC

Tailscale first, signed into the same tailnet. Then Hermes — Windows is Tier 1
supported:

```powershell
# VERIFY the current Windows install command against the Hermes docs
irm https://hermes-agent.nousresearch.com/install.ps1 | iex
```

## 2. Configure "Deck" as an A2A server

In the PC's Hermes config (`~/.hermes/config.yaml` on the PC):

```yaml
# VERIFY block/key names against your installed Hermes version
gateway:
  platforms:
    a2a:
      enabled: true
      extra:
        port: 9900

approvals:
  mode: smart          # keep approvals on — this machine is real
```

Set the inbound token in the PC's environment (`~/.hermes/.env`):

```
A2A_HOST=0.0.0.0                       # reachable on the tailnet interface
A2A_PEER_TOKENS=recon:<token>,scout:<token>
```

Generate tokens with `openssl rand -hex 32`. **Use a different token per calling
agent** — that's how the ledger attributes actions, and how you revoke one agent
without touching the others.

> Binding `0.0.0.0` here is only safe because the PC's firewall and Tailscale
> keep it off the public internet. Confirm your Windows firewall does not expose
> port 9900 to your LAN or the internet.

Start it, then confirm the agent card responds from the VPS:

```bash
curl http://<pc-tailscale-name>:9900/.well-known/agent-card.json
```

## 3. Tell the VPS agents about it

Add the peer to the agents that should reach the PC (in each agent's
`config.yaml` under `/opt/recons/agents/<id>/home/`):

```yaml
a2a_agents:
  deck:
    url: "http://<pc-tailscale-name>:9900"
    auth:
      type: bearer
      token: "${A2A_TOKEN_DECK}"
    capabilities: [terminal, files]    # VERIFY capability names
```

and put `A2A_TOKEN_DECK=<the token you set for that agent>` in
`/opt/recons/agents/<id>/service.env` (chmod 600). Restart the agent.

> The orchestrator regenerates `config.yaml` when the roster changes, so
> re-apply this peer block after adding or removing agents. Keeping the Deck
> peer in the generated template is a good enhancement once you've settled on
> the shape.

Now ask the lead agent something like *"use Deck to tell me the free disk space
on my PC"*. Both halves of the exchange appear in the **Audit log**.

## 4. Chrome with your real logins

Start Chrome on the PC with remote debugging on a dedicated profile:

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=9222 `
  --user-data-dir="$env:USERPROFILE\.hermes\chrome-agent" `
  --no-first-run
```

Sign into the sites you want the agents to use **in that window**. Then in the
PC's Hermes session:

```
/browser connect
```

Hermes attaches over CDP and drives the page through the accessibility tree —
clicking by label, not by pixel. Ask *"open the supplier portal and read me the
outstanding orders"* and it uses your existing session.

**Use a dedicated profile, not your everyday one.** Anything signed in inside
that profile is reachable by an agent. Sign in only to what the agents need.

Known limits: no file downloads through the browser tool, and there's no
extension relay — CDP attach is the mechanism.

## Alternative: SSH backend, no second instance

For shell access only:

```yaml
terminal:
  backend: ssh        # VERIFY key names
  # host/user/key settings per the Hermes terminal docs
```

Simpler, but no browser control, and the PC won't appear as a peer in the audit
ledger the way Deck does.

## Verify

- `curl http://<pc-tailscale-name>:9900/.well-known/agent-card.json` responds
- a VPS agent can run a harmless command on the PC (`whoami`)
- a request **without** the bearer token is rejected
- an agent drives a logged-in page in your Chrome profile
- both directions of the exchange show up in the **Audit log**
