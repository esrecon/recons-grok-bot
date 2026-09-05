# Orchestrator

FastAPI service behind the dashboard. It turns one-click actions into concrete
Hermes Agent profiles and exposes the merged audit ledger.

## What it does

- **Provisioning** (`provisioning.py`) — `create_agent(spec)` allocates an A2A
  port, writes a `SOUL.md` from the job role (written once, then user-owned),
  generates the Hermes `config.yaml`, wires the full A2A mesh, and starts the
  `hermes-gateway@<id>` systemd unit. Shared skills and API keys are referenced,
  never copied per-agent.
- **A2A mesh** (`mesh.py`) — every ordered pair of agents gets a unique bearer
  token so each directed edge is independently authenticated and audited.
  Secrets live only in per-agent `service.env` (chmod 600); `config.yaml` carries
  `${A2A_TOKEN_<PEER>}` placeholders.
- **Roster** (`roster.py`) — atomic JSON store of agent metadata.
- **Security** (`security.py`) — operator login (password or proxy mode),
  signed session cookie, CSRF, rate limits, security headers, all as one ASGI
  middleware. CLI: `python -m recons_orchestrator.security
  {hash-password|session-secret|proxy-secret}`.
- **Credentials** (`credentials.py`) — provider catalogue + `CredentialStore`
  interface; `EnvFileCredentialStore` edits `shared/secrets.env` in place
  (atomic, mode 600, comments kept) with a who/when sidecar. Write-only.
- **Operator audit** (`operator_audit.py`) — `audit/operator.jsonl`, merged
  into the ledger as source `operator`.
- **Chat proxy** (`chat.py`) — SSE to the browser, A2A `message/stream` to the
  agent's loopback port with the orchestrator's own edge token (`VERIFY` the
  event shapes against your Hermes version).
- **SPA** (`spa.py`) — serves the built dashboard with an `index.html`
  fallback, confined to `RECONS_DASHBOARD_DIST`.

Systemd interaction is behind `services.ServiceManager`, so the whole engine is
unit-tested against a temp directory with no init system and no network.

## Layout it manages (on the VPS)

```
$RECONS_ROOT (default /opt/recons)/
├── roster.json
├── audit/
│   ├── webhooks.jsonl     # accepted signed lifecycle events
│   ├── webhook-rejects.jsonl
│   └── operator.jsonl     # what the operator did (never secret values)
├── shared/
│   ├── secrets.env        # API keys + operator login, shared by all agents, chmod 600
│   ├── secrets.meta.json  # who changed which key when — no values, chmod 600
│   ├── skills/            # shared skill library (skills.external_dirs)
│   └── a2a-tokens.json    # directed-edge A2A secrets incl. orchestrator->agent, chmod 600
└── agents/<id>/
    ├── home/              # HERMES_HOME: config.yaml (managed), SOUL.md (user-owned)
    └── service.env        # per-agent env for systemd, chmod 600
```

## HTTP surface

| Route | Auth | Purpose |
|---|---|---|
| `GET /api/health` | none | liveness, nothing else |
| `GET/POST /api/auth/{session,login,logout}` | login: none; logout: session+CSRF | operator session |
| `POST /api/hooks` | HMAC signature | Hermes lifecycle events |
| `/api/agents…`, `/api/skills…`, `/api/routines…`, `/api/audit…`, `/api/sessions…`, `/api/settings…` | session (+CSRF on writes) | everything the dashboard does |
| `/` and static files | none (public code, no data) | the built SPA |

Environment (all optional except the operator login): `RECONS_AUTH_MODE`,
`RECONS_OPERATOR_USER`, `RECONS_OPERATOR_PASSWORD_HASH`, `RECONS_SESSION_SECRET`,
`RECONS_SESSION_TTL_SECONDS`, `RECONS_COOKIE_SECURE`, `RECONS_HSTS`,
`RECONS_ALLOWED_ORIGINS`, `RECONS_CLIENT_IP_HEADER`, `RECONS_LOGIN_RATE_LIMIT`,
`RECONS_API_RATE_LIMIT`, and for proxy mode `RECONS_PROXY_SECRET`,
`RECONS_PROXY_SECRET_HEADER`, `RECONS_PROXY_IDENTITY_HEADER`,
`RECONS_OPERATOR_EMAILS`. See docs/10 §3b and docs/65.

## Develop

```bash
uv sync --extra dev
uv run pytest -q
uv run uvicorn recons_orchestrator.app:app --host 127.0.0.1 --port 8330
```

`config.py` marks every provider/model string that should be re-checked against
the live Hermes docs with a `VERIFY` comment.
