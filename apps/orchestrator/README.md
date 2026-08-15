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

Systemd interaction is behind `services.ServiceManager`, so the whole engine is
unit-tested against a temp directory with no init system and no network.

## Layout it manages (on the VPS)

```
$RECONS_ROOT (default /opt/recons)/
├── roster.json
├── shared/
│   ├── secrets.env        # API keys, shared by all agents, chmod 600
│   ├── skills/            # shared skill library (skills.external_dirs)
│   └── a2a-tokens.json    # directed-edge A2A secrets, chmod 600
└── agents/<id>/
    ├── home/              # HERMES_HOME: config.yaml (managed), SOUL.md (user-owned)
    └── service.env        # per-agent env for systemd, chmod 600
```

## Develop

```bash
uv sync --extra dev
uv run pytest -q
uv run uvicorn recons_orchestrator.app:app --host 127.0.0.1 --port 8330
```

`config.py` marks every provider/model string that should be re-checked against
the live Hermes docs with a `VERIFY` comment.
