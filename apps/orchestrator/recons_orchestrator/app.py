"""FastAPI app: the HTTP surface the dashboard talks to.

Everything binds loopback and is exposed only via `tailscale serve` (docs/15).
An operator login (security.py) gates every /api route except the health
check, the HMAC-verified webhook receiver and the auth endpoints themselves.
The built dashboard is served from the same origin (spa.py).
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets as pysecrets
import time
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)

from pydantic import BaseModel, Field, ValidationError, field_validator

from . import assist, avatars, custom_models
from .chat import ChatBackend
from .config import TIERS, Settings, inherited_model
from .credentials import (
    PROVIDERS,
    CredentialError,
    EnvFileCredentialStore,
    HttpProber,
    spec_for_key,
)
from .discovery import discover
from .ledger import Ledger
from .models import AgentRecord, AgentSpec, AgentStatus, PromoteReport, slugify
from .operator_audit import OperatorAudit
from .providers import ProviderService
from .provisioning import ImportedAgentError, Provisioner, ProvisioningError
from .routines import RoutineStore
from .secrets_store import SecretsError, SecretsStore
from .security import SecurityContext, SecurityMiddleware
from .skills import SkillLibrary
from .spa import register_spa
from .webhooks import WebhookReceiver

log = logging.getLogger("recons.app")


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


def get_secrets(settings: Settings = Depends(get_settings)) -> SecretsStore:
    return SecretsStore(settings.shared_secrets_env)


def get_credentials(settings: Settings = Depends(get_settings)) -> EnvFileCredentialStore:
    return EnvFileCredentialStore(settings.shared_secrets_env, settings.shared_secrets_meta)


def get_operator_audit(settings: Settings = Depends(get_settings)) -> OperatorAudit:
    return OperatorAudit(settings)


def actor(request: Request) -> str:
    """The signed-in operator's name, for audit rows."""
    op = getattr(request.state, "operator", None)
    return op.name if op is not None else "anonymous"


# Overridden per-app in create_app so in-flight sign-in sessions survive polling
# (a per-request instance would forget them between calls).
def get_providers() -> ProviderService:  # pragma: no cover - replaced at startup
    raise RuntimeError("provider service not configured")


# --- request / response models --------------------------------------------------
class RoutineInput(BaseModel):
    agent: str
    schedule: str
    instruction: str
    deliver: str | None = None


class ApiKeyInput(BaseModel):
    key: str


class ImportInput(BaseModel):
    home: str
    name: str | None = None
    role: str = ""


class PromoteInput(BaseModel):
    telegram_enabled: bool = False
    telegram_allowed_users: str = ""
    telegram_token: str | None = None
    make_lead: bool = False


class TelegramInput(BaseModel):
    enabled: bool
    allowed_users: str = ""
    token: str | None = None


class MessageInput(BaseModel):
    text: str = Field(max_length=20_000)


class DecisionInput(BaseModel):
    decision: Literal["approve", "deny"]


class AgentPatch(BaseModel):
    """PATCH /api/agents/{id} — every field optional; absent means unchanged.

    Limits and validators mirror AgentSpec so an edit can't produce a record
    that create would have refused. model_provider/model_name travel as a
    pair; both as empty strings resets the agent to its tier's default."""

    name: str | None = Field(default=None, min_length=1, max_length=48)
    role: str | None = Field(default=None, min_length=1, max_length=120)
    personality: str | None = Field(default=None, max_length=2000)
    avatar_color: str | None = None
    model_provider: str | None = None
    model_name: str | None = None

    @field_validator("avatar_color")
    @classmethod
    def _check_color(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", v):
            raise ValueError("avatar_color must be a #rrggbb hex string")
        return v.lower()

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        slugify(v)  # same "usable display name" rule as create
        return v.strip()


class SoulInput(BaseModel):
    content: str = Field(max_length=200_000)


class AgentContext(BaseModel):
    name: str = ""
    role: str = ""


class ImproveInput(BaseModel):
    field: Literal["name", "role", "personality", "soul"]
    text: str = Field(min_length=1, max_length=200_000)
    agent_context: AgentContext = AgentContext()


class ImageSettingsInput(BaseModel):
    """PUT /api/settings/image — provided fields are written; "" clears one
    back to its default. The key itself is write-only, as everywhere."""

    key: str | None = None
    base_url: str | None = None
    model: str | None = None


class CustomModelInput(BaseModel):
    label: str = Field(min_length=1, max_length=48)
    base_url: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=100)
    api_key: str = Field(min_length=1)


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


class CredentialProvider(BaseModel):
    id: str
    name: str
    description: str
    health: str  # ok | unreachable | configured | not_configured
    keys: list[KeyStatus]


class WebhookFeedStatus(BaseModel):
    last_event_at: str | None = None
    accepted_count: int = 0
    rejected_count: int = 0


class CredentialsResponse(BaseModel):
    providers: list[CredentialProvider]
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


# Provider id → the secrets-file key its "paste a key" flow writes, so those
# writes get the same audit row as the Settings credential editor.
_PROVIDER_KEY = {"nous": "NOUS_API_KEY", "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """First boot: create the secrets file and mint the audit signing secret.

    The user should never have to run `openssl rand` or hand-edit a file on the
    VPS — that is an internal secret between Hermes and the ledger, not an
    account credential.
    """
    settings = getattr(app.state, "settings", None) or Settings()
    try:
        store = SecretsStore(settings.shared_secrets_env)
        store.ensure_file()
        store.ensure_webhook_secret()
    except OSError:
        # A missing or read-only root is surfaced by /api/setup rather than
        # crashing the service on boot.
        pass

    # Regenerate every managed agent config on boot. Config templates evolve
    # (a provider-id fix, say) but rewiring only ran on roster changes, so
    # existing agents kept stale configs until something unrelated touched the
    # roster. Restarting the orchestrator now deploys template fixes. Imported
    # agents are skipped by rewire() as always; edge tokens are stable, so this
    # is idempotent. The team-section sync rides along for the same reason:
    # contract-text improvements deploy on restart instead of waiting for a
    # roster change (imported homes still only if they carry the fence).
    try:
        from . import team
        from .mesh import Mesh
        from .roster import Roster

        records = Roster(settings.roster_path).load()
        if records:
            Mesh(settings).rewire(records)
            team.sync(settings, records)
    except Exception:  # noqa: BLE001 - boot must not die on a bad roster file
        logging.getLogger("recons.app").exception("startup rewire failed")
    yield


def _ensure_session_secret(settings: Settings) -> None:
    """Persist a session-signing secret in the secrets file if the environment
    didn't supply one, so operator sessions survive restarts without a manual
    step. Falls back to an ephemeral secret (SecurityContext) on any error."""
    if settings.session_secret:
        return
    try:
        store = SecretsStore(settings.shared_secrets_env)
        existing = store.get("RECONS_SESSION_SECRET")
        if not existing:
            store.ensure_file()
            existing = pysecrets.token_urlsafe(48)
            store.set_many({"RECONS_SESSION_SECRET": existing})
        settings.session_secret = existing
    except (OSError, SecretsError):
        pass


def create_app(settings: Settings | None = None) -> FastAPI:
    explicit_settings = settings is not None
    settings = settings if settings is not None else Settings()
    app = FastAPI(
        title="Recons Grok Bot orchestrator", version="0.1.0", lifespan=_lifespan
    )
    app.state.settings = settings
    if explicit_settings:
        app.dependency_overrides[get_settings] = lambda: settings

    _ensure_session_secret(settings)
    security = SecurityContext(settings)
    app.state.security = security
    app.add_middleware(SecurityMiddleware, security=security)

    # One receiver instance so the delivery-id dedupe set persists across
    # requests. Tests override app.state.receiver with a temp-root instance.
    app.state.receiver = WebhookReceiver(settings)

    def receiver() -> WebhookReceiver:
        return app.state.receiver

    # Likewise one provider service, so a sign-in started by one request is
    # still pollable by the next. Tests override app.state.providers.
    app.state.providers = ProviderService(SecretsStore(settings.shared_secrets_env))
    app.dependency_overrides[get_providers] = lambda: app.state.providers

    @app.get("/api/health")
    def health() -> dict[str, str]:
        # Deliberately minimal: reachable without a session, says nothing else.
        return {"status": "ok"}

    # --- operator auth --------------------------------------------------------
    def _session_info() -> SessionInfo:
        auth = security.auth
        return SessionInfo(
            authenticated=False, mode=auth.mode, configured=auth.configured, reason=auth.reason,
        )

    @app.get("/api/auth/session", response_model=SessionInfo)
    def auth_session(request: Request):
        info = _session_info()
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
        info = _session_info()
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

    # --- setup: what still needs doing, answered for the dashboard ------------
    @app.get("/api/setup")
    def setup_status(
        prov: Provisioner = Depends(get_provisioner),
        providers: ProviderService = Depends(get_providers),
    ) -> dict:
        statuses = providers.statuses()
        any_provider = any(s.state.value == "configured" for s in statuses)
        agents = prov.list_agents()
        return {
            "providers": [s.to_json() for s in statuses],
            "has_provider": any_provider,
            "has_agents": bool(agents),
            # The wizard shows until there is at least one provider AND one agent.
            "complete": any_provider and bool(agents),
        }

    # --- providers ------------------------------------------------------------
    @app.get("/api/providers")
    def list_providers(providers: ProviderService = Depends(get_providers)) -> dict:
        return {"providers": [s.to_json() for s in providers.statuses()]}

    @app.put("/api/providers/{provider_id}/key")
    def set_provider_key(
        provider_id: str,
        body: ApiKeyInput,
        providers: ProviderService = Depends(get_providers),
        secrets: SecretsStore = Depends(get_secrets),
        creds: EnvFileCredentialStore = Depends(get_credentials),
        audit: OperatorAudit = Depends(get_operator_audit),
        who: str = Depends(actor),
    ) -> dict:
        key = _PROVIDER_KEY.get(provider_id)
        existed = bool(key and secrets.is_set(key))
        try:
            status = providers.save_api_key(provider_id, body.key).to_json()
        except (ValueError, SecretsError) as exc:
            if key:
                audit.credential(actor=who, key=key, action="replaced" if existed else "created",
                                 result="error")
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if key:
            action = "replaced" if existed else "created"
            creds.note_change(key, actor=who, action=action)
            audit.credential(actor=who, key=key, action=action, result="ok")
            app.state.credentials_changed = True
        return status

    @app.delete("/api/providers/{provider_id}/key")
    def clear_provider_key(
        provider_id: str,
        providers: ProviderService = Depends(get_providers),
        creds: EnvFileCredentialStore = Depends(get_credentials),
        audit: OperatorAudit = Depends(get_operator_audit),
        who: str = Depends(actor),
    ) -> dict:
        key = _PROVIDER_KEY.get(provider_id)
        try:
            status = providers.clear(provider_id).to_json()
        except (ValueError, SecretsError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if key:
            creds.note_change(key, actor=who, action="removed")
            audit.credential(actor=who, key=key, action="removed", result="ok")
            app.state.credentials_changed = True
        return status

    @app.post("/api/providers/{provider_id}/login", status_code=201)
    def start_provider_login(
        provider_id: str, providers: ProviderService = Depends(get_providers),
        audit: OperatorAudit = Depends(get_operator_audit), who: str = Depends(actor),
    ) -> dict:
        try:
            session = providers.start_login(provider_id).to_json()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit.record(actor=who, category="auth", action="provider_login_started", target=provider_id)
        return session

    @app.get("/api/providers/login/{login_id}")
    def poll_provider_login(
        login_id: str, providers: ProviderService = Depends(get_providers)
    ) -> dict:
        session = providers.get_login(login_id)
        if session is None:
            raise HTTPException(status_code=404, detail="no such sign-in attempt")
        return session.to_json()

    # --- settings: credentials catalogue, security posture, services ----------
    app.state.prober = HttpProber()
    app.state.credentials_changed = False

    def _runtime_configured(key: str) -> bool:
        """Auth keys are read by this process from its environment, not the
        file (they may be set only in the unit's env) — reflect that."""
        s = settings
        by_setting = {
            "RECONS_AUTH_MODE": bool(s.auth_mode),
            "RECONS_SESSION_SECRET": bool(s.session_secret),
            "RECONS_OPERATOR_USER": bool(s.operator_user),
            "RECONS_OPERATOR_PASSWORD_HASH": bool(s.operator_password_hash),
            "RECONS_PROXY_SECRET": bool(s.proxy_secret),
            "RECONS_OPERATOR_EMAILS": bool(s.operator_emails),
        }
        if key in by_setting:
            return by_setting[key]
        return bool(os.environ.get(key, "").strip())

    def _webhook_feed() -> WebhookFeedStatus:
        audit_dir = settings.root / "audit"
        out = WebhookFeedStatus()
        store = audit_dir / "webhooks.jsonl"
        if store.exists():
            lines = [l for l in store.read_text("utf-8").splitlines() if l.strip()]
            out.accepted_count = len(lines)
            if lines:
                try:
                    ts = json.loads(lines[-1]).get("ts")
                    if isinstance(ts, (int, float)):
                        from datetime import datetime, timezone

                        out.last_event_at = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                except (ValueError, AttributeError):
                    pass
        rejects = audit_dir / "webhook-rejects.jsonl"
        if rejects.exists():
            out.rejected_count = len([l for l in rejects.read_text("utf-8").splitlines() if l.strip()])
        return out

    @app.get("/api/settings/credentials", response_model=CredentialsResponse)
    def settings_credentials(store: EnvFileCredentialStore = Depends(get_credentials)) -> CredentialsResponse:
        providers: list[CredentialProvider] = []
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
            providers.append(CredentialProvider(
                id=spec.id, name=spec.name, description=spec.description, health=health, keys=keys,
            ))
        return CredentialsResponse(
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
            raise HTTPException(status_code=422, detail='body must be {"value": string}')
        try:
            action = store.set(key, value, actor=who)
        except CredentialError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
        except Exception as exc:  # disk / permission failure: audited, never echoed
            audit.credential(actor=who, key=key,
                             action="replaced" if store.status(key).configured else "created",
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

    @app.get("/api/settings/services")
    def settings_services(prov: Provisioner = Depends(get_provisioner)) -> dict:
        rows = []
        for rec in prov.list_agents():
            if rec.imported:
                # Runs its own gateway outside this platform until promoted.
                rows.append({
                    "agent": rec.id, "name": rec.name, "unit": None, "status": "external",
                    "expected": "external", "healthy": True,
                })
                continue
            state = prov.service_status(rec.id)
            expected = "paused" if rec.status is AgentStatus.PAUSED else "running"
            healthy = state == "active" if expected == "running" else state in ("inactive", "unknown")
            rows.append({
                "agent": rec.id, "name": rec.name, "unit": settings.unit_name(rec.id),
                "status": state, "expected": expected, "healthy": healthy,
            })
        return {"services": rows}

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
        lines = "\n".join(json.dumps(e) for e in ledger.query(limit=1_000_000))
        return PlainTextResponse(
            lines,
            headers={"content-disposition": "attachment; filename=audit-export.jsonl"},
        )

    # --- sessions (conversation history, from the ledger) ---------------------
    @app.get("/api/sessions")
    def sessions(agent: str | None = None, ledger: Ledger = Depends(get_ledger)) -> dict:
        return {"sessions": [s.to_json() for s in ledger.sessions(agent=agent)]}

    @app.get("/api/sessions/{agent_id}/{session_id}")
    def session_detail(agent_id: str, session_id: str, ledger: Ledger = Depends(get_ledger)) -> dict:
        summary = next(
            (s for s in ledger.sessions(agent=agent_id) if s.session_id == session_id), None
        )
        if summary is None:
            raise HTTPException(status_code=404, detail="no such session")
        return {"session": summary.to_json(), "events": ledger.session_events(agent_id, session_id)}

    # --- signed-webhook receiver (Hermes lifecycle events) --------------------
    @app.post("/api/hooks")
    async def hooks(request: Request) -> JSONResponse:
        body = await request.body()
        status, payload = receiver().handle(body, dict(request.headers))
        return JSONResponse(status_code=status, content=payload)

    # --- chat history: the agent's own state.db is the source of truth --------
    # Hermes records every turn there, so history survives page reloads, view
    # switches, and even conversations held outside the dashboard.
    @app.get("/api/agents/{agent_id}/history")
    def chat_history(
        agent_id: str,
        limit: int = 100,
        prov: Provisioner = Depends(get_provisioner),
        settings: Settings = Depends(get_settings),
    ) -> dict:
        from pathlib import Path as _Path

        from .ledger import read_state_db

        record = prov.get_agent(agent_id)
        if record is None:
            raise HTTPException(status_code=404, detail="no such agent")
        home = _Path(record.home) if record.home else settings.home_dir(agent_id)
        events = read_state_db(agent_id, home / "state.db")
        turns = [
            {"role": e.role, "text": e.text, "ts_iso": e.ts_iso}
            for e in events
            if e.kind == "message" and e.role in ("user", "assistant") and e.text
        ]
        return {"messages": turns[-limit:]}

    # --- chat: one turn with an agent, streamed as SSE ------------------------
    @app.post("/api/agents/{agent_id}/messages")
    def send_message(
        agent_id: str,
        body: MessageInput,
        prov: Provisioner = Depends(get_provisioner),
        settings: Settings = Depends(get_settings),
        audit: OperatorAudit = Depends(get_operator_audit),
        who: str = Depends(actor),
    ) -> StreamingResponse:
        record = prov.get_agent(agent_id)
        if record is None:
            raise HTTPException(status_code=404, detail="no such agent")
        if not body.text.strip():
            raise HTTPException(status_code=422, detail="message must not be empty")
        if record.status is AgentStatus.PAUSED:
            raise HTTPException(status_code=409, detail="agent is paused — resume it to chat")
        # The message text itself is recorded by the agent (state.db → ledger);
        # the operator row only says that a turn was sent.
        audit.record(actor=who, category="chat", action="message", target=agent_id,
                     extra={"chars": len(body.text)})

        backend = ChatBackend(settings)

        def frames():
            for event in backend.stream(record, body.text):
                yield f"data: {json.dumps(event)}\n\n"

        return StreamingResponse(
            frames(),
            media_type="text/event-stream",
            headers={"cache-control": "no-store", "x-accel-buffering": "no"},
        )

    @app.post("/api/agents/{agent_id}/approvals/{approval_id}")
    def decide_approval(
        agent_id: str, approval_id: str, body: DecisionInput,
        prov: Provisioner = Depends(get_provisioner),
        audit: OperatorAudit = Depends(get_operator_audit), who: str = Depends(actor),
    ) -> dict:
        """Record the operator's decision on an in-chat approval card.

        The CLI chat backend has no separate approval channel (VERIFY against
        your Hermes version), so this is the audit record of what the operator
        decided; the card itself came from the agent's own prompt."""
        if prov.get_agent(agent_id) is None:
            raise HTTPException(status_code=404, detail="no such agent")
        audit.record(actor=who, category="chat", action=f"approval_{body.decision}",
                     target=f"{agent_id}/{approval_id[:120]}")
        return {"status": "recorded", "decision": body.decision}

    # --- importing agents that already exist on this machine ------------------
    @app.get("/api/import/candidates")
    def import_candidates(prov: Provisioner = Depends(get_provisioner)) -> dict:
        from pathlib import Path as _Path

        known = {
            _Path(r.home) for r in prov.list_agents() if r.home
        }
        return {"candidates": [c.to_json() for c in discover(known)]}

    @app.post("/api/import", response_model=AgentRecord, status_code=201)
    def import_agent(
        body: ImportInput, prov: Provisioner = Depends(get_provisioner),
        audit: OperatorAudit = Depends(get_operator_audit), who: str = Depends(actor),
    ) -> AgentRecord:
        from pathlib import Path as _Path

        try:
            record = prov.import_agent(_Path(body.home), body.name, body.role)
        except ProvisioningError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit.record(actor=who, category="agent", action="imported", target=record.id)
        return record

    # --- skills (shared library + teach-mode approval queue) ------------------
    @app.get("/api/skills")
    def skills(lib: SkillLibrary = Depends(get_skills)) -> dict:
        return {
            "shared": [s.to_json() for s in lib.list_shared()],
            "pending": [s.to_json() for s in lib.list_pending()],
        }

    def _skill_detail(lib: SkillLibrary, source: str, slug: str, agent: str | None = None) -> dict:
        try:
            return lib.inspect(source, slug, agent=agent).to_json()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def _skill_file(lib: SkillLibrary, source: str, slug: str, path: str, agent: str | None = None) -> dict:
        try:
            return lib.read_file(source, slug, path, agent=agent)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/skills/shared/{slug}")
    def shared_skill_detail(slug: str, lib: SkillLibrary = Depends(get_skills)) -> dict:
        return _skill_detail(lib, "shared", slug)

    @app.get("/api/skills/shared/{slug}/file")
    def shared_skill_file(slug: str, path: str, lib: SkillLibrary = Depends(get_skills)) -> dict:
        return _skill_file(lib, "shared", slug, path)

    @app.get("/api/skills/pending/{agent_id}/{slug}")
    def pending_skill_detail(agent_id: str, slug: str, lib: SkillLibrary = Depends(get_skills)) -> dict:
        return _skill_detail(lib, "pending", slug, agent=agent_id)

    @app.get("/api/skills/pending/{agent_id}/{slug}/file")
    def pending_skill_file(
        agent_id: str, slug: str, path: str, lib: SkillLibrary = Depends(get_skills)
    ) -> dict:
        return _skill_file(lib, "pending", slug, path, agent=agent_id)

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

    # --- agents ---------------------------------------------------------------
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

    @app.post("/api/agents/{agent_id}/lead", response_model=AgentRecord)
    def make_lead(
        agent_id: str, prov: Provisioner = Depends(get_provisioner),
        audit: OperatorAudit = Depends(get_operator_audit), who: str = Depends(actor),
    ) -> AgentRecord:
        """Make this agent the head of staff. Exactly one lead at a time."""
        try:
            record = prov.set_lead(agent_id)
        except ProvisioningError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        audit.record(actor=who, category="agent", action="made_lead", target=agent_id)
        return record

    @app.post("/api/agents/{agent_id}/promote", response_model=PromoteReport)
    def promote_agent(
        agent_id: str, body: PromoteInput, prov: Provisioner = Depends(get_provisioner),
        audit: OperatorAudit = Depends(get_operator_audit), who: str = Depends(actor),
    ) -> PromoteReport:
        """Adopt an imported agent as managed. Files and roster only — the
        record comes out PAUSED and nothing is started; the cutover script
        owns the moment a gateway begins polling (docs/35)."""
        try:
            report = prov.promote_agent(
                agent_id,
                telegram_enabled=body.telegram_enabled,
                telegram_allowed_users=body.telegram_allowed_users,
                telegram_token=body.telegram_token,
                make_lead=body.make_lead,
            )
        except ProvisioningError as exc:
            status = 404 if "no such agent" in str(exc) else 409
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit.record(actor=who, category="agent", action="promoted", target=agent_id)
        if body.telegram_token:
            audit.credential(actor=who, key=f"TELEGRAM_BOT_TOKEN:{agent_id}", action="created", result="ok")
        return report

    @app.post("/api/agents/{agent_id}/demote", response_model=AgentRecord)
    def demote_agent(
        agent_id: str, prov: Provisioner = Depends(get_provisioner),
        audit: OperatorAudit = Depends(get_operator_audit), who: str = Depends(actor),
    ) -> AgentRecord:
        """Cutover rollback: back to imported, gateway disabled, Telegram off."""
        try:
            record = prov.demote_agent(agent_id)
        except ProvisioningError as exc:
            status = 404 if "no such agent" in str(exc) else 409
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        audit.record(actor=who, category="agent", action="demoted", target=agent_id)
        return record

    @app.get("/api/agents/{agent_id}/telegram")
    def get_telegram(
        agent_id: str,
        prov: Provisioner = Depends(get_provisioner),
        settings: Settings = Depends(get_settings),
    ) -> dict:
        """Telegram status. Write-only secret contract: reports whether a
        token is stored, never the token itself."""
        from .telegram import load_tokens

        record = prov.get_agent(agent_id)
        if record is None:
            raise HTTPException(status_code=404, detail="no such agent")
        return {
            "enabled": record.telegram_enabled,
            "allowed_users": record.telegram_allowed_users,
            "token_set": bool(load_tokens(settings).get(agent_id)),
            "imported": record.imported,
        }

    @app.put("/api/agents/{agent_id}/telegram", response_model=AgentRecord)
    def set_telegram(
        agent_id: str, body: TelegramInput, prov: Provisioner = Depends(get_provisioner),
        settings: Settings = Depends(get_settings),
        audit: OperatorAudit = Depends(get_operator_audit), who: str = Depends(actor),
    ) -> AgentRecord:
        from .telegram import load_tokens

        had_token = bool(load_tokens(settings).get(agent_id))
        try:
            record = prov.set_telegram(
                agent_id,
                enabled=body.enabled,
                allowed_users=body.allowed_users,
                token=body.token,
            )
        except ImportedAgentError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ProvisioningError as exc:
            status = 404 if "no such agent" in str(exc) else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if body.token:
            audit.credential(actor=who, key=f"TELEGRAM_BOT_TOKEN:{agent_id}",
                             action="replaced" if had_token else "created", result="ok")
        audit.record(actor=who, category="agent", action="telegram_enabled" if body.enabled else "telegram_disabled",
                     target=agent_id)
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

    # --- customize: identity, soul, avatar, model -----------------------------
    @app.patch("/api/agents/{agent_id}", response_model=AgentRecord)
    def update_agent(
        agent_id: str, body: AgentPatch, prov: Provisioner = Depends(get_provisioner),
        audit: OperatorAudit = Depends(get_operator_audit), who: str = Depends(actor),
    ) -> AgentRecord:
        """Edit name/role/personality/colour/model. The id never changes —
        it is load-bearing (paths, unit names, A2A peers), so renames are
        display-only."""
        try:
            record = prov.update_agent(
                agent_id,
                name=body.name,
                role=body.role,
                personality=body.personality,
                avatar_color=body.avatar_color,
                model_provider=body.model_provider,
                model_name=body.model_name,
            )
        except ImportedAgentError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ProvisioningError as exc:
            detail = str(exc)
            if "no such agent" in detail:
                status = 404
            elif "already exists" in detail:
                status = 409
            else:
                status = 400
            raise HTTPException(status_code=status, detail=detail) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        changed = [k for k, v in body.model_dump().items() if v is not None]
        audit.record(actor=who, category="agent", action="updated", target=agent_id,
                     extra={"fields": changed})
        return record

    @app.get("/api/agents/{agent_id}/soul")
    def get_soul(agent_id: str, prov: Provisioner = Depends(get_provisioner)) -> dict:
        try:
            content, exists = prov.read_soul(agent_id)
        except ProvisioningError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"content": content, "exists": exists}

    @app.put("/api/agents/{agent_id}/soul")
    def put_soul(
        agent_id: str, body: SoulInput, prov: Provisioner = Depends(get_provisioner),
        audit: OperatorAudit = Depends(get_operator_audit), who: str = Depends(actor),
    ) -> dict:
        """Save SOUL.md. The managed team fence is repaired server-side, so
        the response content is the file as actually written."""
        try:
            content = prov.write_soul(agent_id, body.content)
        except ProvisioningError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"could not write SOUL.md: {exc}"
            ) from exc
        audit.record(actor=who, category="agent", action="soul_edited", target=agent_id)
        return {"content": content}

    @app.post("/api/agents/{agent_id}/avatar", response_model=AgentRecord)
    def generate_avatar(
        agent_id: str,
        prov: Provisioner = Depends(get_provisioner),
        settings: Settings = Depends(get_settings),
        secrets: SecretsStore = Depends(get_secrets),
    ) -> AgentRecord:
        """Generate the Pixar-style headshot from the agent's name + role."""
        record = prov.get_agent(agent_id)
        if record is None:
            raise HTTPException(status_code=404, detail="no such agent")
        try:
            data = avatars.generate(secrets, record.name, record.role)
        except avatars.AvatarError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        path = avatars.avatar_path(settings, agent_id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"could not save the avatar: {exc}"
            ) from exc
        return prov.set_avatar_version(agent_id, int(time.time()))

    @app.get("/api/agents/{agent_id}/avatar")
    def get_avatar(
        agent_id: str,
        prov: Provisioner = Depends(get_provisioner),
        settings: Settings = Depends(get_settings),
    ) -> FileResponse:
        if prov.get_agent(agent_id) is None:
            raise HTTPException(status_code=404, detail="no such agent")
        path = avatars.avatar_path(settings, agent_id)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="no generated avatar")
        with path.open("rb") as fh:
            head = fh.read(16)
        # Immutable is safe: the URL carries ?v=<avatar_version> so a regenerate
        # changes the URL, never the cached body.
        return FileResponse(
            path,
            media_type=avatars.media_type_of(head),
            headers={"cache-control": "public, max-age=31536000, immutable"},
        )

    @app.post("/api/assist/improve")
    def improve_text(
        body: ImproveInput, secrets: SecretsStore = Depends(get_secrets)
    ) -> dict:
        """The ✨ Improve button: rewrite one field's text via the cheap tier."""
        try:
            improved = assist.improve(
                secrets,
                body.field,
                body.text,
                agent_name=body.agent_context.name,
                agent_role=body.agent_context.role,
            )
        except assist.AssistError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        return {"text": improved}

    @app.get("/api/settings/image")
    def image_settings(secrets: SecretsStore = Depends(get_secrets)) -> dict:
        return avatars.settings_of(secrets)

    @app.put("/api/settings/image")
    def set_image_settings(
        body: ImageSettingsInput, secrets: SecretsStore = Depends(get_secrets),
        creds: EnvFileCredentialStore = Depends(get_credentials),
        audit: OperatorAudit = Depends(get_operator_audit), who: str = Depends(actor),
    ) -> dict:
        values: dict[str, str] = {}
        if body.key is not None:
            values["IMAGE_API_KEY"] = body.key.strip()
        if body.base_url is not None:
            values["IMAGE_API_BASE_URL"] = body.base_url.strip().rstrip("/")
        if body.model is not None:
            values["IMAGE_API_MODEL"] = body.model.strip()
        key_action = None
        if "IMAGE_API_KEY" in values:
            had = secrets.is_set("IMAGE_API_KEY")
            key_action = "removed" if not values["IMAGE_API_KEY"] else ("replaced" if had else "created")
        try:
            if values:
                secrets.set_many(values)
        except SecretsError as exc:
            if key_action:
                audit.credential(actor=who, key="IMAGE_API_KEY", action=key_action, result="error")
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if key_action:
            creds.note_change("IMAGE_API_KEY", actor=who, action=key_action)
            audit.credential(actor=who, key="IMAGE_API_KEY", action=key_action, result="ok")
        return avatars.settings_of(secrets)

    @app.get("/api/models")
    def list_models(
        settings: Settings = Depends(get_settings),
        secrets: SecretsStore = Depends(get_secrets),
        providers: ProviderService = Depends(get_providers),
    ) -> dict:
        """Every model an agent could run on: the three tiers (availability
        from the provider statuses) plus operator-registered custom entries."""
        state = {s["id"]: s["state"] for s in (p.to_json() for p in providers.statuses())}
        tier_provider = {"lead": "anthropic", "workhorse": "openai", "bulk": "nous"}
        tier_label = {"lead": "Claude", "workhorse": "GPT", "bulk": "Hermes"}
        options = []
        for tier_key, cfg in TIERS.items():
            if tier_key == "workhorse":
                # Show what a workhorse would actually run (mesh inherits the
                # default home's model when it can).
                cfg = inherited_model() or cfg
            options.append(
                {
                    "provider": cfg.provider,
                    "model": cfg.model,
                    "label": tier_label[tier_key],
                    "tier": tier_key,
                    "source": "tier",
                    "available": state.get(tier_provider[tier_key]) == "configured",
                }
            )
        for cm in custom_models.load(settings):
            options.append(
                {
                    "provider": cm.id,
                    "model": cm.model,
                    "label": cm.label,
                    "tier": None,
                    "source": "custom",
                    "available": secrets.is_set(cm.key_env),
                }
            )
        return {"options": options}

    @app.post("/api/models/custom", status_code=201)
    def add_custom_model(
        body: CustomModelInput,
        settings: Settings = Depends(get_settings),
        secrets: SecretsStore = Depends(get_secrets),
        audit: OperatorAudit = Depends(get_operator_audit), who: str = Depends(actor),
    ) -> dict:
        """Register an OpenAI-compatible provider + key so its model becomes
        selectable in the Customize tab."""
        try:
            entry = custom_models.add(
                settings, label=body.label, base_url=body.base_url, model=body.model
            )
        except custom_models.CustomModelError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            secrets.set_many({entry.key_env: body.api_key.strip()})
        except SecretsError as exc:
            # Don't leave a keyless orphan entry behind.
            custom_models.remove(settings, entry.id)
            audit.credential(actor=who, key=entry.key_env, action="created", result="error")
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit.credential(actor=who, key=entry.key_env, action="created", result="ok")
        return {"model": entry.to_json()}

    @app.delete("/api/models/custom/{provider_id}")
    def delete_custom_model(
        provider_id: str,
        prov: Provisioner = Depends(get_provisioner),
        settings: Settings = Depends(get_settings),
        secrets: SecretsStore = Depends(get_secrets),
        audit: OperatorAudit = Depends(get_operator_audit), who: str = Depends(actor),
    ) -> dict:
        in_use = sorted(
            r.name for r in prov.list_agents() if r.model_provider == provider_id
        )
        if in_use:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"'{provider_id}' is still used by: {', '.join(in_use)} — "
                    "switch their model first"
                ),
            )
        try:
            entry = custom_models.remove(settings, provider_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="no such custom model") from exc
        try:
            secrets.unset(entry.key_env)
        except SecretsError:  # pragma: no cover - key_env is always writable
            pass
        audit.credential(actor=who, key=entry.key_env, action="removed", result="ok")
        return {"removed": entry.id}

    # --- the dashboard itself -------------------------------------------------
    # Registered last so every /api route above wins the match (spa.py).
    register_spa(app, settings)
    return app


def _set_status(prov: Provisioner, agent_id: str, status: AgentStatus) -> AgentRecord:
    try:
        return prov.set_status(agent_id, status)
    except ImportedAgentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ProvisioningError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


app = create_app()
