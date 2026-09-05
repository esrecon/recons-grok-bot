"""FastAPI app: the HTTP surface the dashboard talks to.

Everything binds loopback and is exposed only via `tailscale serve` (docs/15).
An operator login (security.py) gates every /api route except the health
check, the HMAC-verified webhook receiver and the auth endpoints themselves.
The built dashboard is served from the same origin (spa.py).
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from pydantic import BaseModel, Field, ValidationError

from .config import Settings
from .ledger import Ledger
from .models import AgentRecord, AgentSpec, AgentStatus
from .provisioning import Provisioner, ProvisioningError
from .routines import RoutineStore
from .security import SecurityContext, SecurityMiddleware
from .skills import SkillLibrary
from .spa import register_spa
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


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


class SessionInfo(BaseModel):
    """What the dashboard asks on load: am I signed in, and how do I mutate."""

    authenticated: bool
    operator: str | None = None
    via: str | None = None
    csrf_token: str | None = None
    mode: str
    configured: bool
    reason: str | None = None


def create_app(settings: Settings | None = None) -> FastAPI:
    explicit_settings = settings is not None
    settings = settings if settings is not None else Settings()
    app = FastAPI(title="Recons Grok Bot orchestrator", version="0.1.0")
    app.state.settings = settings
    if explicit_settings:
        app.dependency_overrides[get_settings] = lambda: settings

    security = SecurityContext(settings)
    app.state.security = security
    app.add_middleware(SecurityMiddleware, security=security)

    # One receiver instance so the delivery-id dedupe set persists across
    # requests. Tests override app.state.receiver with a temp-root instance.
    app.state.receiver = WebhookReceiver(settings)

    def receiver() -> WebhookReceiver:
        return app.state.receiver

    @app.get("/api/health")
    def health() -> dict[str, str]:
        # Deliberately minimal: reachable without a session, says nothing else.
        return {"status": "ok"}

    # --- operator auth --------------------------------------------------------
    def _session_info(request: Request) -> SessionInfo:
        auth = security.auth
        return SessionInfo(
            authenticated=False, mode=auth.mode, configured=auth.configured, reason=auth.reason,
        )

    @app.get("/api/auth/session", response_model=SessionInfo)
    def auth_session(request: Request):
        info = _session_info(request)
        operator = getattr(request.state, "operator", None)
        session = getattr(request.state, "session", None)
        if operator is None:
            return info
        info.authenticated, info.operator, info.via = True, operator.name, operator.via
        if session is None:
            # Proxy-vouched operator without a session yet: issue one so the
            # browser has a CSRF token to mutate with.
            cookie, csrf = security.sessions.issue(operator.name, operator.via)
            info.csrf_token = csrf
            resp = JSONResponse(info.model_dump())
            security.sessions.set_cookie(resp, cookie)
            return resp
        info.csrf_token = session.csrf
        return info

    @app.post("/api/auth/login", response_model=SessionInfo)
    async def auth_login(request: Request):
        auth = security.auth
        if auth.mode != "password":
            raise HTTPException(status_code=404, detail="password login is not enabled")
        if not auth.configured:
            raise HTTPException(status_code=503, detail=auth.reason)
        if not request.headers.get("content-type", "").lower().startswith("application/json"):
            raise HTTPException(status_code=415, detail="login body must be application/json")
        try:
            body = LoginRequest.model_validate(await request.json())
        except (ValidationError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="invalid login body") from exc
        operator = auth.check_password(body.username, body.password)
        if operator is None:
            raise HTTPException(status_code=401, detail="invalid credentials")
        cookie, csrf = security.sessions.issue(operator.name, operator.via)
        info = _session_info(request)
        info.authenticated, info.operator, info.via, info.csrf_token = True, operator.name, operator.via, csrf
        resp = JSONResponse(info.model_dump())
        security.sessions.set_cookie(resp, cookie)
        return resp

    @app.post("/api/auth/logout", status_code=204)
    def auth_logout() -> Response:
        resp = Response(status_code=204)
        security.sessions.clear_cookie(resp)
        return resp

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

    # Must be last: catch-all for the built dashboard.
    register_spa(app, settings)
    return app


def _set_status(prov: Provisioner, agent_id: str, status: AgentStatus) -> AgentRecord:
    try:
        return prov.set_status(agent_id, status)
    except ProvisioningError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


app = create_app()
