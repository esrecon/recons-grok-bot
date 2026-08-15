"""FastAPI app: the HTTP surface the dashboard talks to.

Phase 2 ships the agent-lifecycle endpoints (the one-click provisioning
backend). Chat-proxy and audit-ledger routers are mounted in later phases.
Everything binds loopback and is exposed only via `tailscale serve` (docs/15);
an operator login sits in front (Phase 4).
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse

from .config import Settings
from .models import AgentRecord, AgentSpec, AgentStatus
from .provisioning import Provisioner, ProvisioningError


def get_settings() -> Settings:
    return Settings()


def get_provisioner(settings: Settings = Depends(get_settings)) -> Provisioner:
    # Real deployments use the default systemd service manager. Tests override
    # this dependency to inject a temp root + recording service manager.
    return Provisioner(settings)


def create_app() -> FastAPI:
    app = FastAPI(title="Recons Grok Bot orchestrator", version="0.1.0")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/agents", response_model=list[AgentRecord])
    def list_agents(prov: Provisioner = Depends(get_provisioner)) -> list[AgentRecord]:
        return prov.list_agents()

    @app.post("/api/agents", response_model=AgentRecord, status_code=201)
    def create_agent(
        spec: AgentSpec, prov: Provisioner = Depends(get_provisioner)
    ) -> AgentRecord:
        try:
            return prov.create_agent(spec)
        except ProvisioningError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/agents/{agent_id}", response_model=AgentRecord)
    def get_agent(
        agent_id: str, prov: Provisioner = Depends(get_provisioner)
    ) -> AgentRecord:
        record = prov.get_agent(agent_id)
        if record is None:
            raise HTTPException(status_code=404, detail="no such agent")
        return record

    @app.post("/api/agents/{agent_id}/pause", response_model=AgentRecord)
    def pause_agent(
        agent_id: str, prov: Provisioner = Depends(get_provisioner)
    ) -> AgentRecord:
        return _set_status(prov, agent_id, AgentStatus.PAUSED)

    @app.post("/api/agents/{agent_id}/resume", response_model=AgentRecord)
    def resume_agent(
        agent_id: str, prov: Provisioner = Depends(get_provisioner)
    ) -> AgentRecord:
        return _set_status(prov, agent_id, AgentStatus.RUNNING)

    @app.delete("/api/agents/{agent_id}", status_code=204)
    def delete_agent(
        agent_id: str, prov: Provisioner = Depends(get_provisioner)
    ) -> JSONResponse:
        try:
            prov.remove_agent(agent_id)
        except ProvisioningError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return JSONResponse(status_code=204, content=None)

    return app


def _set_status(prov: Provisioner, agent_id: str, status: AgentStatus) -> AgentRecord:
    try:
        return prov.set_status(agent_id, status)
    except ProvisioningError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


app = create_app()
