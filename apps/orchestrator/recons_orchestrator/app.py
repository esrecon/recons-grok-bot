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
from .credentials import (
    PROVIDERS,
    CredentialError,
    EnvFileCredentialStore,
    HttpProber,
    spec_for_key,
)
from .ledger import Ledger
from .models import AgentRecord, AgentSpec, AgentStatus
from .operator_audit import OperatorAudit
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


def get_credentials(settings: Settings = Depends(get_settings)) -> EnvFileCredentialStore:
    return EnvFileCredentialStore(settings.shared_secrets_env, settings.shared_secrets_meta)


def get_operator_audit(settings: Settings = Depends(get_settings)) -> OperatorAudit:
    return OperatorAudit(settings)


def actor(request: Request) -> str:
    """The signed-in operator's name, for audit rows."""
    op = getattr(request.state, "operator", None)
    return op.name if op is not None else "anonymous"


class RoutineInput(BaseModel):
    agent: str
    schedule: str
    instruction: str
    deliver: str | None = None


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


class KeyStatus(BaseModel):
    key: str
    label: str
    secret: bool
    writable: bool
    required: bool
    hint: str = ""
    configured: bool
    updated_at: str | None = None
    updated_by: str | None = None


class ProviderStatus(BaseModel):
    id: str
    name: str
    description: str
    health: str  # ok | unreachable | configured | not_configured
    keys: list[KeyStatus]


class WebhookFeedStatus(BaseModel):
    last_event_at: str | None = None
    accepted_count: int = 0
    rejected_count: int = 0


class ProvidersResponse(BaseModel):
    providers: list[ProviderStatus]
    integrations: dict[str, WebhookFeedStatus]
    restart_required: bool


class CredentialChange(BaseModel):
    key: str
    action: str  # created | replaced | removed
    configured: bool
    updated_at: str | None = None
    restart_required: bool


class SecurityPosture(BaseModel):
    mode: str
    configured: bool
    operator: str | None
    via: str | None
    cookie_secure: bool
    hsts: bool
    session_ttl_seconds: int
    csrf_protection: bool = True
    proxy_identity_header: str | None = None
    allowed_origins: list[str]
    rate_limits: dict[str, int]


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
        audit = OperatorAudit(settings)
        if operator is None:
            audit.record(actor=body.username[:64], category="auth", action="login",
                         target=body.username[:64], result="denied")
            raise HTTPException(status_code=401, detail="invalid credentials")
        audit.record(actor=operator.name, category="auth", action="login", target=operator.name)
        cookie, csrf = security.sessions.issue(operator.name, operator.via)
        info = _session_info(request)
        info.authenticated, info.operator, info.via, info.csrf_token = True, operator.name, operator.via, csrf
        resp = JSONResponse(info.model_dump())
        security.sessions.set_cookie(resp, cookie)
        return resp

    @app.post("/api/auth/logout", status_code=204)
    def auth_logout(who: str = Depends(actor)) -> Response:
        OperatorAudit(settings).record(actor=who, category="auth", action="logout", target=who)
        resp = Response(status_code=204)
        security.sessions.clear_cookie(resp)
        return resp

    # --- settings: providers, credentials, security posture -------------------
    app.state.prober = HttpProber()
    app.state.credentials_changed = False

    def _runtime_configured(key: str) -> bool:
        """Auth keys are read by this process from its environment, not the
        file (they may be set only in the unit's env) — reflect that."""
        s = settings
        by_setting = {
            "RECONS_SESSION_SECRET": bool(s.session_secret),
            "RECONS_OPERATOR_USER": bool(s.operator_user),
            "RECONS_OPERATOR_PASSWORD_HASH": bool(s.operator_password_hash),
            "RECONS_PROXY_SECRET": bool(s.proxy_secret),
            "RECONS_OPERATOR_EMAILS": bool(s.operator_emails),
        }
        if key in by_setting:
            return by_setting[key]
        import os

        return bool(os.environ.get(key, "").strip())

    def _webhook_feed() -> WebhookFeedStatus:
        import json as _json

        audit_dir = settings.root / "audit"
        out = WebhookFeedStatus()
        store = audit_dir / "webhooks.jsonl"
        if store.exists():
            lines = [l for l in store.read_text("utf-8").splitlines() if l.strip()]
            out.accepted_count = len(lines)
            if lines:
                try:
                    ts = _json.loads(lines[-1]).get("ts")
                    if isinstance(ts, (int, float)):
                        from datetime import datetime, timezone

                        out.last_event_at = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                except (ValueError, AttributeError):
                    pass
        rejects = audit_dir / "webhook-rejects.jsonl"
        if rejects.exists():
            out.rejected_count = len([l for l in rejects.read_text("utf-8").splitlines() if l.strip()])
        return out

    @app.get("/api/settings/providers", response_model=ProvidersResponse)
    def settings_providers(store: EnvFileCredentialStore = Depends(get_credentials)) -> ProvidersResponse:
        providers: list[ProviderStatus] = []
        for spec in PROVIDERS:
            keys: list[KeyStatus] = []
            configured: dict[str, bool] = {}
            for k in spec.keys:
                st = store.status(k.key)
                is_set = st.configured or _runtime_configured(k.key)
                configured[k.key] = is_set
                keys.append(KeyStatus(
                    key=k.key, label=k.label, secret=k.secret, writable=k.writable,
                    required=k.required, hint=k.hint, configured=is_set,
                    updated_at=st.updated_at, updated_by=st.updated_by,
                ))
            if not all(configured.get(h, False) for h in spec.health_keys):
                health = "not_configured"
            elif spec.probe_url_key:
                url = store.read(spec.probe_url_key)
                health = app.state.prober.probe(url) if url else "not_configured"
            else:
                health = "configured"
            providers.append(ProviderStatus(
                id=spec.id, name=spec.name, description=spec.description, health=health, keys=keys,
            ))
        return ProvidersResponse(
            providers=providers,
            integrations={"webhook_feed": _webhook_feed()},
            restart_required=bool(app.state.credentials_changed),
        )

    @app.put("/api/settings/credentials/{key}", response_model=CredentialChange)
    async def set_credential(
        key: str,
        request: Request,
        store: EnvFileCredentialStore = Depends(get_credentials),
        audit: OperatorAudit = Depends(get_operator_audit),
        who: str = Depends(actor),
    ) -> CredentialChange:
        # The body is parsed by hand so a validation error can never echo the
        # submitted value back (FastAPI's default 422 includes the input).
        spec = spec_for_key(key)
        if spec is None:
            raise HTTPException(status_code=404, detail="unknown credential key")
        if not spec.writable:
            raise HTTPException(status_code=403, detail=f"{key} is managed on the server")
        try:
            body = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="body must be JSON") from exc
        value = body.get("value") if isinstance(body, dict) else None
        if not isinstance(value, str):
            raise HTTPException(status_code=422, detail="body must be {\"value\": string}")
        try:
            action = store.set(key, value, actor=who)
        except CredentialError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
        except Exception as exc:  # disk / permission failure: audited, never echoed
            audit.credential(actor=who, key=key, action="replaced" if store.status(key).configured else "created",
                             result="error")
            raise HTTPException(status_code=500, detail="could not write the secrets file") from exc
        audit.credential(actor=who, key=key, action=action, result="ok")
        app.state.credentials_changed = True
        st = store.status(key)
        return CredentialChange(key=key, action=action, configured=True,
                                updated_at=st.updated_at, restart_required=True)

    @app.delete("/api/settings/credentials/{key}", response_model=CredentialChange)
    def remove_credential(
        key: str,
        store: EnvFileCredentialStore = Depends(get_credentials),
        audit: OperatorAudit = Depends(get_operator_audit),
        who: str = Depends(actor),
    ) -> CredentialChange:
        try:
            removed = store.remove(key, actor=who)
        except CredentialError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
        except Exception as exc:
            audit.credential(actor=who, key=key, action="removed", result="error")
            raise HTTPException(status_code=500, detail="could not write the secrets file") from exc
        if not removed:
            raise HTTPException(status_code=404, detail="credential is not set")
        audit.credential(actor=who, key=key, action="removed", result="ok")
        app.state.credentials_changed = True
        return CredentialChange(key=key, action="removed", configured=False, restart_required=True)

    @app.get("/api/settings/security", response_model=SecurityPosture)
    def settings_security(request: Request) -> SecurityPosture:
        op = getattr(request.state, "operator", None)
        return SecurityPosture(
            mode=security.auth.mode,
            configured=security.auth.configured,
            operator=op.name if op else None,
            via=op.via if op else None,
            cookie_secure=settings.cookie_secure,
            hsts=settings.hsts,
            session_ttl_seconds=settings.session_ttl_seconds,
            proxy_identity_header=settings.proxy_identity_header if settings.auth_mode == "proxy" else None,
            allowed_origins=list(settings.allowed_origins),
            rate_limits={
                "login_per_minute": settings.login_rate_limit,
                "api_per_minute": settings.api_rate_limit,
            },
        )

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
        agent_id: str, slug: str, lib: SkillLibrary = Depends(get_skills),
        audit: OperatorAudit = Depends(get_operator_audit), who: str = Depends(actor),
    ) -> dict:
        try:
            skill = lib.approve(agent_id, slug)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        audit.record(actor=who, category="skill", action="approved", target=f"{agent_id}/{slug}")
        return skill.to_json()

    @app.post("/api/skills/{agent_id}/{slug}/reject", status_code=204)
    def reject_skill(
        agent_id: str, slug: str, lib: SkillLibrary = Depends(get_skills),
        audit: OperatorAudit = Depends(get_operator_audit), who: str = Depends(actor),
    ) -> JSONResponse:
        lib.reject(agent_id, slug)
        audit.record(actor=who, category="skill", action="rejected", target=f"{agent_id}/{slug}")
        return JSONResponse(status_code=204, content=None)

    # --- routines (per-agent cron automations) --------------------------------
    @app.get("/api/routines")
    def routines(store: RoutineStore = Depends(get_routines)) -> dict:
        return {"routines": [r.to_json() for r in store.list_all()]}

    @app.post("/api/routines", status_code=201)
    def create_routine(
        body: RoutineInput, store: RoutineStore = Depends(get_routines),
        audit: OperatorAudit = Depends(get_operator_audit), who: str = Depends(actor),
    ) -> dict:
        try:
            routine = store.create(body.agent, body.schedule, body.instruction, body.deliver)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        audit.record(actor=who, category="routine", action="created",
                     target=f"{routine.agent}/{routine.id}")
        return routine.to_json()

    @app.post("/api/routines/{agent_id}/{routine_id}/{action}")
    def toggle_routine(
        agent_id: str, routine_id: str, action: str,
        store: RoutineStore = Depends(get_routines),
        audit: OperatorAudit = Depends(get_operator_audit), who: str = Depends(actor),
    ) -> dict:
        if action not in ("enable", "pause"):
            raise HTTPException(status_code=400, detail="action must be enable|pause")
        try:
            routine = store.set_enabled(agent_id, routine_id, action == "enable")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        audit.record(actor=who, category="routine", action="enabled" if action == "enable" else "paused",
                     target=f"{agent_id}/{routine_id}")
        return routine.to_json()

    @app.delete("/api/routines/{agent_id}/{routine_id}", status_code=204)
    def delete_routine(
        agent_id: str, routine_id: str, store: RoutineStore = Depends(get_routines),
        audit: OperatorAudit = Depends(get_operator_audit), who: str = Depends(actor),
    ) -> JSONResponse:
        try:
            store.delete(agent_id, routine_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        audit.record(actor=who, category="routine", action="deleted", target=f"{agent_id}/{routine_id}")
        return JSONResponse(status_code=204, content=None)

    @app.get("/api/agents", response_model=list[AgentRecord])
    def list_agents(prov: Provisioner = Depends(get_provisioner)) -> list[AgentRecord]:
        return prov.list_agents()

    @app.post("/api/agents", response_model=AgentRecord, status_code=201)
    def create_agent(
        spec: AgentSpec, prov: Provisioner = Depends(get_provisioner),
        audit: OperatorAudit = Depends(get_operator_audit), who: str = Depends(actor),
    ) -> AgentRecord:
        try:
            record = prov.create_agent(spec)
        except ProvisioningError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        audit.record(actor=who, category="agent", action="created", target=record.id)
        return record

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
        agent_id: str, prov: Provisioner = Depends(get_provisioner),
        audit: OperatorAudit = Depends(get_operator_audit), who: str = Depends(actor),
    ) -> AgentRecord:
        record = _set_status(prov, agent_id, AgentStatus.PAUSED)
        audit.record(actor=who, category="agent", action="paused", target=agent_id)
        return record

    @app.post("/api/agents/{agent_id}/resume", response_model=AgentRecord)
    def resume_agent(
        agent_id: str, prov: Provisioner = Depends(get_provisioner),
        audit: OperatorAudit = Depends(get_operator_audit), who: str = Depends(actor),
    ) -> AgentRecord:
        record = _set_status(prov, agent_id, AgentStatus.RUNNING)
        audit.record(actor=who, category="agent", action="resumed", target=agent_id)
        return record

    @app.delete("/api/agents/{agent_id}", status_code=204)
    def delete_agent(
        agent_id: str, prov: Provisioner = Depends(get_provisioner),
        audit: OperatorAudit = Depends(get_operator_audit), who: str = Depends(actor),
    ) -> JSONResponse:
        try:
            prov.remove_agent(agent_id)
        except ProvisioningError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        audit.record(actor=who, category="agent", action="deleted", target=agent_id)
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
