# 1. The VPS

The always-on machine that hosts the agents, the dashboard, and the audit ledger.

## What you need

- An Ubuntu 24.04 VPS. A £5–10/month box (2 vCPU / 4 GB) is plenty — the models
  run in the cloud, so the VPS mostly shuttles text. Hetzner, Hostinger and
  DigitalOcean all work.
- SSH access with a key.
- Your Tailscale account.

---

## The short way

Two commands. Everything else — API keys, ChatGPT sign-in, creating agents —
happens **in the app**, not here.

```bash
# Clone, or update an existing checkout — safe to re-run.
git clone https://github.com/esrecon/recons-grok-bot /opt/recons/app 2>/dev/null \
  || git -C /opt/recons/app pull --ff-only
sudo /opt/recons/app/scripts/vps-quickstart.sh
```

Both commands are idempotent: if a previous attempt got partway, running them
again picks up where it left off rather than starting over.

**Two things to expect during step 3.** It downloads roughly 200 MB — Hermes
installs its own browser (Playwright Chromium) so your agents can use real
websites. Then Hermes's installer asks how to set itself up:

```
How would you like to set up Hermes?
  1. Quick Setup (Nous Portal) — free OAuth login, no API keys  (recommended)
  2. Full setup — configure every provider, tool & option yourself
  3. Blank Slate — everything off except the bare minimum
```

**Choose 1.** It signs you into Nous Portal for free and finishes in seconds.
Option 2 walks you through configuring every provider in the terminal, which is
exactly what the dashboard's setup screen does — you would be doing the same job
twice, in the harder place.

It installs the packages, sets the firewall to default-deny, joins your tailnet
(you'll get a sign-in link), installs Hermes and the service units, builds the
dashboard, starts the orchestrator, publishes it privately over HTTPS, and runs
the security check. It finishes by printing your URL.

Open that URL on any device on your tailnet and the app walks you through the
rest. **You should not need to come back to this terminal.**

If a step fails, or you'd rather do it piece by piece, the long way is below —
it's the same work, spelled out.

---

## The long way

## 1. System prep

Clone this repo onto the VPS, then:

```bash
sudo ./scripts/vps-bootstrap.sh
```

Idempotent — safe to re-run. It installs packages (curl, git, ufw, docker,
unattended-upgrades), sets the firewall to **default-deny inbound** with SSH and
`tailscale0` allowed, turns off SSH password auth, installs Tailscale, and
creates `/opt/recons`.

Then bring the machine onto your tailnet:

```bash
sudo tailscale up
```

## 2. User setup

```bash
./scripts/vps-bootstrap.sh --user
```

This installs `uv`, installs **Hermes Agent**, creates
`/opt/recons/shared/secrets.env` from the example (chmod 600), and installs the
three systemd user units.

Then make the services survive logout and reboot:

```bash
sudo loginctl enable-linger "$USER"
```

> **VERIFY**: the Hermes install command is
> `curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash`. Check the
> current one at [the docs](https://hermes-agent.nousresearch.com/docs/getting-started/installation)
> before running it. Pin **≥ v0.20.1** — earlier versions carry patched CVEs
> (see [60-security-hardening.md](60-security-hardening.md)).

## 3. Secrets — nothing to do

There is no file to edit. The orchestrator creates
`/opt/recons/shared/secrets.env` (chmod 600) on first boot and generates the
audit signing secret itself — that one is an internal secret between Hermes and
the ledger, not an account credential, so you should never have to run
`openssl rand` by hand.

Provider keys and logins are entered **in the app** (step 8), not here.

## 4. Providers — in the app

Skip ahead. Once the dashboard is up, its setup screen connects your providers:
paste a Nous key, sign in with your ChatGPT subscription (the link and code
appear in the app), and optionally add Claude. The background on each — and the
honest terms-of-service position — is in
[40-providers-and-tos.md](40-providers-and-tos.md).

## 5. Build the dashboard

```bash
cd apps/dashboard
npm ci
npm run build
sudo mkdir -p /opt/recons/dashboard
sudo cp -r dist/* /opt/recons/dashboard/
```

## 6. Start the orchestrator

The unit expects this repo at `/opt/recons/app`:

```bash
sudo mkdir -p /opt/recons/app
sudo cp -r . /opt/recons/app/        # or: git clone into that path
cd /opt/recons/app/apps/orchestrator && uv sync --frozen

systemctl --user enable --now recons-orchestrator.service
systemctl --user status recons-orchestrator.service
curl -s http://127.0.0.1:8330/api/health   # {"status":"ok"}
```

It binds **loopback only**. Nothing is reachable yet — that's next.

## 7. Publish it to your tailnet

Go to **[15-tailscale.md](15-tailscale.md)**, then come back.

## 8. Connect providers and create your first agents

Open the dashboard. On a fresh install it opens a two-step setup screen:

1. **Connect a brain.** Paste a Nous API key, and/or click *Sign in with
   ChatGPT* — the verification link and code appear right there, and the card
   flips to **Connected** when you finish in the browser. Claude is optional and
   can wait.
2. **Hire your first teammate.** Name and a one-line job. The first agent you
   create becomes the **lead**.

Keys you enter are written to the shared secrets file by the server and are
never shown again — the app only ever tells you whether a provider is connected.
You can change providers later under **Settings**.

Behind that one click, the orchestrator:

1. allocates an A2A port and records the agent in the roster,
2. writes its `SOUL.md` from the job you typed (yours to edit afterwards — it is
   never overwritten),
3. generates its `config.yaml` pointing at the shared skills dir and shared
   secrets,
4. mints a bearer token for every directed edge between it and each existing
   agent, and rewrites every agent's config so the mesh is complete,
5. runs `systemctl --user enable --now hermes-gateway@<id>`.

Suggested starting roster: **Recon** (lead, "coordinates the team"), **Scout**
("researches suppliers and drafts outreach"), **Clerk** ("handles admin, invoices
and bulk data entry").

## 9. Verify

```bash
./scripts/vps-verify.sh
```

Every check must pass before you point the agents at anything real. It asserts
nothing listens on a public address, funnel is off, secrets are 600, approvals
are on, agent-written skills need approval, tool execution is sandboxed in
Docker, and A2A is loopback-bound with tokens.

## Troubleshooting

| Symptom | Look at |
|---|---|
| Agent won't start | `journalctl --user -u hermes-gateway@<id> -n 50` |
| Dashboard 502 / blank | Is `recons-orchestrator` running? Is `RECONS_DASHBOARD_DIST` right? |
| Agent dies after logout | `sudo loginctl enable-linger $USER` |
| Config key rejected | You hit a `VERIFY` point — check the current Hermes docs |
| Docker permission denied | `sudo usermod -aG docker $USER`, then re-login |
