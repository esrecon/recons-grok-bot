"""FastAPI app: the HTTP surface the dashboard talks to.

Phase 2 ships the agent-lifecycle endpoints (the one-click provisioning
backend). Chat-proxy and audit-ledger routers are mounted in later phases.
Everything binds loopback and is exposed only via `tailscale serve` (docs/15);
an operator login sits in front (Phase 4).
"""

from __future__ import annotations

import logging
import re
import time
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)

from pydantic import BaseModel, Field, field_validator

from . import assist, avatars, custom_models
from .chat import ChatBackend
from .config import TIERS, Settings, inherited_model
from .discovery import discover
from .ledger import Ledger
from .models import AgentRecord, AgentSpec, AgentStatus, PromoteReport, slugify
from .providers import ProviderService
from .provisioning import ImportedAgentError, Provisioner, ProvisioningError
from .routines import RoutineStore
from .secrets_store import SecretsError, SecretsStore
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


def get_secrets(settings: Settings = Depends(get_settings)) -> SecretsStore:
    return SecretsStore(settings.shared_secrets_env)


# Overridden per-app in create_app so in-flight sign-in sessions survive polling
# (a per-request instance would forget them between calls).
def get_providers() -> ProviderService:  # pragma: no cover - replaced at startup
    raise RuntimeError("provider service not configured")


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
    text: str


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


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """First boot: create the secrets file and mint the audit signing secret.

    The user should never have to run `openssl rand` or hand-edit a file on the
    VPS — that is an internal secret between Hermes and the ledger, not an
    account credential.
    """
    settings = Settings()
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


def create_app() -> FastAPI:
    app = FastAPI(
        title="Recons Grok Bot orchestrator", version="0.1.0", lifespan=_lifespan
    )

    # One receiver instance so the delivery-id dedupe set persists across
    # requests. Tests override app.state.receiver with a temp-root instance.
    app.state.receiver = WebhookReceiver(Settings())

    def receiver() -> WebhookReceiver:
        return app.state.receiver

    # Likewise one provider service, so a sign-in started by one request is
    # still pollable by the next. Tests override app.state.providers.
    app.state.providers = ProviderService(SecretsStore(Settings().shared_secrets_env))
    app.dependency_overrides[get_providers] = lambda: app.state.providers

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

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
    ) -> dict:
        try:
            return providers.save_api_key(provider_id, body.key).to_json()
        except (ValueError, SecretsError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/providers/{provider_id}/key")
    def clear_provider_key(
        provider_id: str, providers: ProviderService = Depends(get_providers)
    ) -> dict:
        try:
            return providers.clear(provider_id).to_json()
        except (ValueError, SecretsError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/providers/{provider_id}/login", status_code=201)
    def start_provider_login(
        provider_id: str, providers: ProviderService = Depends(get_providers)
    ) -> dict:
        try:
            return providers.start_login(provider_id).to_json()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/providers/login/{login_id}")
    def poll_provider_login(
        login_id: str, providers: ProviderService = Depends(get_providers)
    ) -> dict:
        session = providers.get_login(login_id)
        if session is None:
            raise HTTPException(status_code=404, detail="no such sign-in attempt")
        return session.to_json()

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
    ) -> StreamingResponse:
        record = prov.get_agent(agent_id)
        if record is None:
            raise HTTPException(status_code=404, detail="no such agent")
        if not body.text.strip():
            raise HTTPException(status_code=422, detail="message must not be empty")

        backend = ChatBackend(settings)

        def frames():
            import json as _json

            for event in backend.stream(record, body.text):
                yield f"data: {_json.dumps(event)}\n\n"

        return StreamingResponse(
            frames(),
            media_type="text/event-stream",
            headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
        )

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
        body: ImportInput, prov: Provisioner = Depends(get_provisioner)
    ) -> AgentRecord:
        from pathlib import Path as _Path

        try:
            return prov.import_agent(_Path(body.home), body.name, body.role)
        except ProvisioningError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

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

    @app.post("/api/agents/{agent_id}/lead", response_model=AgentRecord)
    def make_lead(
        agent_id: str, prov: Provisioner = Depends(get_provisioner)
    ) -> AgentRecord:
        """Make this agent the head of staff. Exactly one lead at a time."""
        try:
            return prov.set_lead(agent_id)
        except ProvisioningError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/agents/{agent_id}/promote", response_model=PromoteReport)
    def promote_agent(
        agent_id: str, body: PromoteInput, prov: Provisioner = Depends(get_provisioner)
    ) -> PromoteReport:
        """Adopt an imported agent as managed. Files and roster only — the
        record comes out PAUSED and nothing is started; the cutover script
        owns the moment a gateway begins polling (docs/35)."""
        try:
            return prov.promote_agent(
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

    @app.post("/api/agents/{agent_id}/demote", response_model=AgentRecord)
    def demote_agent(
        agent_id: str, prov: Provisioner = Depends(get_provisioner)
    ) -> AgentRecord:
        """Cutover rollback: back to imported, gateway disabled, Telegram off."""
        try:
            return prov.demote_agent(agent_id)
        except ProvisioningError as exc:
            status = 404 if "no such agent" in str(exc) else 409
            raise HTTPException(status_code=status, detail=str(exc)) from exc

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
        agent_id: str, body: TelegramInput, prov: Provisioner = Depends(get_provisioner)
    ) -> AgentRecord:
        try:
            return prov.set_telegram(
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

    @app.delete("/api/agents/{agent_id}", status_code=204)
    def delete_agent(
        agent_id: str, prov: Provisioner = Depends(get_provisioner)
    ) -> JSONResponse:
        try:
            prov.remove_agent(agent_id)
        except ProvisioningError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return JSONResponse(status_code=204, content=None)

    # --- customize: identity, soul, avatar, model -----------------------------
    @app.patch("/api/agents/{agent_id}", response_model=AgentRecord)
    def update_agent(
        agent_id: str, body: AgentPatch, prov: Provisioner = Depends(get_provisioner)
    ) -> AgentRecord:
        """Edit name/role/personality/colour/model. The id never changes —
        it is load-bearing (paths, unit names, A2A peers), so renames are
        display-only."""
        try:
            return prov.update_agent(
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

    @app.get("/api/agents/{agent_id}/soul")
    def get_soul(agent_id: str, prov: Provisioner = Depends(get_provisioner)) -> dict:
        try:
            content, exists = prov.read_soul(agent_id)
        except ProvisioningError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"content": content, "exists": exists}

    @app.put("/api/agents/{agent_id}/soul")
    def put_soul(
        agent_id: str, body: SoulInput, prov: Provisioner = Depends(get_provisioner)
    ) -> dict:
        """Save SOUL.md. The managed team fence is repaired server-side, so
        the response content is the file as actually written."""
        try:
            return {"content": prov.write_soul(agent_id, body.content)}
        except ProvisioningError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"could not write SOUL.md: {exc}"
            ) from exc

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
        body: ImageSettingsInput, secrets: SecretsStore = Depends(get_secrets)
    ) -> dict:
        values: dict[str, str] = {}
        if body.key is not None:
            values["IMAGE_API_KEY"] = body.key.strip()
        if body.base_url is not None:
            values["IMAGE_API_BASE_URL"] = body.base_url.strip().rstrip("/")
        if body.model is not None:
            values["IMAGE_API_MODEL"] = body.model.strip()
        try:
            if values:
                secrets.set_many(values)
        except SecretsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
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
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"model": entry.to_json()}

    @app.delete("/api/models/custom/{provider_id}")
    def delete_custom_model(
        provider_id: str,
        prov: Provisioner = Depends(get_provisioner),
        settings: Settings = Depends(get_settings),
        secrets: SecretsStore = Depends(get_secrets),
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
        return {"removed": entry.id}

    # --- the dashboard itself -------------------------------------------------
    # Registered last so every /api route above wins the match. This serves the
    # built SPA: real files when they exist (assets, the service worker, the
    # manifest, icons) and index.html for anything else, so client-side routes
    # survive a refresh.
    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_dashboard(
        full_path: str, settings: Settings = Depends(get_settings)
    ) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="no such endpoint")

        dist = settings.dashboard_dist
        index = dist / "index.html"
        if not index.is_file():
            raise HTTPException(
                status_code=503,
                detail=(
                    f"The dashboard is not built at {dist}. Build it with: "
                    "cd apps/dashboard && npm ci && npm run build — then copy "
                    "dist/ there, or point RECONS_DASHBOARD_DIST at it."
                ),
            )

        if full_path:
            candidate = dist / full_path
            try:
                resolved = candidate.resolve()
                # Refuse anything that escapes the dist directory.
                resolved.relative_to(dist.resolve())
                if resolved.is_file():
                    return FileResponse(resolved)
            except (ValueError, OSError):
                pass  # traversal attempt or unreadable — fall through to index

        return FileResponse(index)

    return app


def _set_status(prov: Provisioner, agent_id: str, status: AgentStatus) -> AgentRecord:
    try:
        return prov.set_status(agent_id, status)
    except ImportedAgentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ProvisioningError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


app = create_app()
