"""FastAPI app: the HTTP surface the dashboard talks to.

Phase 2 ships the agent-lifecycle endpoints (the one-click provisioning
backend). Chat-proxy and audit-ledger routers are mounted in later phases.
Everything binds loopback and is exposed only via `tailscale serve` (docs/15);
an operator login sits in front (Phase 4).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)

from pydantic import BaseModel

from .chat import ChatBackend
from .config import Settings
from .discovery import discover
from .ledger import Ledger
from .models import AgentRecord, AgentSpec, AgentStatus
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


class MessageInput(BaseModel):
    text: str


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
    # is idempotent.
    try:
        from .mesh import Mesh
        from .roster import Roster

        records = Roster(settings.roster_path).load()
        if records:
            Mesh(settings).rewire(records)
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

    @app.delete("/api/agents/{agent_id}", status_code=204)
    def delete_agent(
        agent_id: str, prov: Provisioner = Depends(get_provisioner)
    ) -> JSONResponse:
        try:
            prov.remove_agent(agent_id)
        except ProvisioningError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return JSONResponse(status_code=204, content=None)

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
