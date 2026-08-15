"""FastAPI app: the HTTP surface the dashboard talks to.

Phase 2 ships the agent-lifecycle endpoints (the one-click provisioning
backend). Chat-proxy and audit-ledger routers are mounted in later phases.
Everything binds loopback and is exposed only via `tailscale serve` (docs/15);
an operator login sits in front (Phase 4).
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from pydantic import BaseModel

from .config import Settings
from .ledger import Ledger
from .models import AgentRecord, AgentSpec, AgentStatus
from .provisioning import Provisioner, ProvisioningError
from .routines import RoutineStore
from .skills import SkillLibrary
from .webhooks import WebhookReceiver


def get_settings() -> Settings:
    return Settings()


def get_provisioner(settings: Settings = Depends(get_settings)) -> Provisioner:
    # Real deployments use the default systemd service manager. Tests override
    # this dependency to inject a temp root + recording service manager.
    return Provisioner(settings)


def get_ledger(settings: Settings = Depends(get_settings)) -> Ledger:
    return Ledger(settings)


def get_skills(settings: Settings = Depends(get_settings)) -> SkillLibrary:
    return SkillLibrary(settings)


def get_routines(settings: Settings = Depends(get_settings)) -> RoutineStore:
    return RoutineStore(settings)


class RoutineInput(BaseModel):
    agent: str
    schedule: str
    instruction: str
    deliver: str | None = None


def create_app() -> FastAPI:
    app = FastAPI(title="Recons Grok Bot orchestrator", version="0.1.0")

    # One receiver instance so the delivery-id dedupe set persists across
    # requests. Tests override app.state.receiver with a temp-root instance.
    app.state.receiver = WebhookReceiver(Settings())

    def receiver() -> WebhookReceiver:
        return app.state.receiver

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # --- audit ledger ---------------------------------------------------------
    @app.get("/api/audit")
    def audit(
        agent: str | None = None,
        source: str | None = None,
        kind: str | None = None,
        a2a_only: bool = False,
        q: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 500,
        offset: int = 0,
        ledger: Ledger = Depends(get_ledger),
    ) -> dict:
        rows = ledger.query(
            agent=agent, source=source, kind=kind, a2a_only=a2a_only,
            search=q, since=since, until=until, limit=limit, offset=offset,
        )
        return {"events": rows, "count": len(rows)}

    @app.get("/api/audit/agents")
    def audit_agents(ledger: Ledger = Depends(get_ledger)) -> dict:
        return {"agents": ledger.agents()}

    @app.get("/api/audit/export.jsonl")
    def audit_export(ledger: Ledger = Depends(get_ledger)) -> PlainTextResponse:
        import json

        lines = "\n".join(json.dumps(e) for e in ledger.query(limit=1_000_000))
        return PlainTextResponse(
            lines,
            headers={"content-disposition": "attachment; filename=audit-export.jsonl"},
        )

    # --- signed-webhook receiver (Hermes lifecycle events) --------------------
    @app.post("/api/hooks")
    async def hooks(request: Request) -> JSONResponse:
        body = await request.body()
        status, payload = receiver().handle(body, dict(request.headers))
        return JSONResponse(status_code=status, content=payload)

    # --- skills (shared library + teach-mode approval queue) ------------------
    @app.get("/api/skills")
    def skills(lib: SkillLibrary = Depends(get_skills)) -> dict:
        return {
            "shared": [s.to_json() for s in lib.list_shared()],
            "pending": [s.to_json() for s in lib.list_pending()],
        }

    @app.post("/api/skills/{agent_id}/{slug}/approve")
    def approve_skill(
        agent_id: str, slug: str, lib: SkillLibrary = Depends(get_skills)
    ) -> dict:
        try:
            return lib.approve(agent_id, slug).to_json()
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/skills/{agent_id}/{slug}/reject", status_code=204)
    def reject_skill(
        agent_id: str, slug: str, lib: SkillLibrary = Depends(get_skills)
    ) -> JSONResponse:
        lib.reject(agent_id, slug)
        return JSONResponse(status_code=204, content=None)

    # --- routines (per-agent cron automations) --------------------------------
    @app.get("/api/routines")
    def routines(store: RoutineStore = Depends(get_routines)) -> dict:
        return {"routines": [r.to_json() for r in store.list_all()]}

    @app.post("/api/routines", status_code=201)
    def create_routine(
        body: RoutineInput, store: RoutineStore = Depends(get_routines)
    ) -> dict:
        try:
            return store.create(body.agent, body.schedule, body.instruction, body.deliver).to_json()
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/routines/{agent_id}/{routine_id}/{action}")
    def toggle_routine(
        agent_id: str, routine_id: str, action: str,
        store: RoutineStore = Depends(get_routines),
    ) -> dict:
        if action not in ("enable", "pause"):
            raise HTTPException(status_code=400, detail="action must be enable|pause")
        try:
            return store.set_enabled(agent_id, routine_id, action == "enable").to_json()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.delete("/api/routines/{agent_id}/{routine_id}", status_code=204)
    def delete_routine(
        agent_id: str, routine_id: str, store: RoutineStore = Depends(get_routines)
    ) -> JSONResponse:
        try:
            store.delete(agent_id, routine_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return JSONResponse(status_code=204, content=None)

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
