# 1. The VPS

The always-on machine that hosts the agents, the dashboard, and the audit ledger.

## What you need

- An Ubuntu 24.04 VPS. A £5–10/month box (2 vCPU / 4 GB) is plenty — the models
  run in the cloud, so the VPS mostly shuttles text. Hetzner, Hostinger and
  DigitalOcean all work.
- SSH access with a key.
- Your Tailscale account.

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

## 3. Fill in the secrets

Edit `/opt/recons/shared/secrets.env` (it stays chmod 600). Every agent reads
this one file, so a key added here is available to all of them.

```bash
# Generate the webhook signing secret:
openssl rand -hex 32
```

Set `RECONS_WEBHOOK_SECRET` to that value — the audit ledger verifies every
event Hermes sends against it. Provider keys are covered in
[40-providers-and-tos.md](40-providers-and-tos.md); you can start with just Nous
Portal and add the rest later.

## 3b. Set the operator login

The dashboard and its API are **locked until an operator credential exists**
— there is no anonymous mode, even on the tailnet. Generate both values on the
VPS (never type a plaintext password into the file):

```bash
cd /opt/recons/app/apps/orchestrator
uv run python -m recons_orchestrator.security session-secret   # prints RECONS_SESSION_SECRET=…
uv run python -m recons_orchestrator.security hash-password    # prompts twice, prints RECONS_OPERATOR_PASSWORD_HASH=…
```

Paste each printed line over its placeholder in `/opt/recons/shared/secrets.env`
and set `RECONS_OPERATOR_USER` to the name you will sign in with. The password
itself is never stored; rotating `RECONS_SESSION_SECRET` signs every browser out.

## 4. Providers

Follow [40-providers-and-tos.md](40-providers-and-tos.md) now, at least far
enough to have one working provider. The short version:

```bash
hermes model          # pick "ChatGPT or Codex Subscription" → device-code login
```

That gives you the workhorse tier. Claude (lead tier) needs the wrapper service;
Nous needs an API key in `secrets.env`.

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

It binds **loopback only**. Nothing is reachable yet — that's next. Prove the
login gate is on while you're here:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8330/api/agents   # 401
```

## 7. Publish it to your tailnet

Go to **[15-tailscale.md](15-tailscale.md)**, then come back.

## 8. Create your first agents

Open the dashboard, sign in with the operator login from §3b, click **+**, and
give the first agent a name and a one-line job. The first agent you create
becomes the **lead**.

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
| Dashboard says "operator login is not configured" | §3b — `RECONS_OPERATOR_USER` / `RECONS_OPERATOR_PASSWORD_HASH` missing from `secrets.env` |
| Every save fails with "cross-site request blocked" | A proxy rewrote `Host`/`Origin`; set `RECONS_ALLOWED_ORIGINS` (docs/65) |
| A key set in Settings has no effect | Agents read `secrets.env` at start — pause/resume the agent (or restart its unit) |
| Agent dies after logout | `sudo loginctl enable-linger $USER` |
| Config key rejected | You hit a `VERIFY` point — check the current Hermes docs |
| Docker permission denied | `sudo usermod -aG docker $USER`, then re-login |
