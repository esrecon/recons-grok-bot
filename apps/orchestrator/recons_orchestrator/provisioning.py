"""The provisioning engine — the backend for the dashboard's one-click actions.

`create_agent` turns an AgentSpec into a permanent, running Hermes profile:

  1. allocate an A2A port and record roster metadata
  2. render SOUL.md from the job role (written once; user-owned thereafter)
  3. bootstrap the shared skills dir + shared secrets file if missing
  4. rewire the full A2A mesh (regenerates every agent's config + service.env)
  5. daemon-reload and `enable --now hermes-gateway@<id>`

Steps 1–4 are pure filesystem work and fully unit-tested against a temp root;
step 5 goes through the injected ServiceManager so tests never touch systemd.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from jinja2 import Environment, PackageLoader, select_autoescape

from .config import A2A_PORT_BASE, Settings
from .mesh import Mesh, TokenFactory, _default_token
from .models import AgentRecord, AgentSpec, AgentStatus, slugify
from .roster import Roster
from .services import ServiceManager, SystemdServiceManager

Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProvisioningError(RuntimeError):
    pass


class Provisioner:
    def __init__(
        self,
        settings: Settings,
        services: ServiceManager | None = None,
        *,
        clock: Clock = _utcnow,
        token_factory: TokenFactory = _default_token,
    ) -> None:
        self._s = settings
        self._services = services or SystemdServiceManager()
        self._clock = clock
        self._roster = Roster(settings.roster_path)
        self._mesh = Mesh(settings, token_factory=token_factory)
        self._jinja = Environment(
            loader=PackageLoader("recons_orchestrator", "templates"),
            autoescape=select_autoescape(enabled_extensions=()),
            keep_trailing_newline=True,
            trim_blocks=True,
            lstrip_blocks=True,
        )

    # -- queries ---------------------------------------------------------------
    def list_agents(self) -> list[AgentRecord]:
        return self._roster.load()

    def get_agent(self, agent_id: str) -> AgentRecord | None:
        return self._roster.get(agent_id)

    # -- lifecycle -------------------------------------------------------------
    def create_agent(self, spec: AgentSpec) -> AgentRecord:
        agent_id = slugify(spec.name)
        existing = self._roster.load()
        if any(r.id == agent_id for r in existing):
            raise ProvisioningError(f"an agent named '{spec.name}' already exists")

        self._ensure_shared_layout()

        record = AgentRecord(
            id=agent_id,
            name=spec.name,
            role=spec.role,
            personality=spec.personality,
            tier=spec.tier,
            avatar_color=spec.avatar_color,
            a2a_port=self._roster.next_a2a_port(A2A_PORT_BASE),
            status=AgentStatus.RUNNING,
            is_lead=(len(existing) == 0),  # the first agent created is the lead
            created_at=self._clock().isoformat(),
        )

        # SOUL.md is written once and then owned by the user/agent — never
        # clobbered by later rewires.
        self._write_soul(record)

        # Persist roster BEFORE wiring so a failure can't leave orphaned config
        # that the roster doesn't know about.
        self._roster.upsert(record)

        # Rewire the whole mesh (this agent + every existing peer).
        roster_now = self._roster.load()
        self._mesh.rewire(roster_now)

        # Start services: reload units, (re)start peers so they pick up the new
        # mesh, then start the new agent.
        self._services.daemon_reload()
        for r in roster_now:
            if r.id == record.id:
                continue
            self._services.restart(self._s.unit_name(r.id))
        self._services.enable_now(self._s.unit_name(record.id))

        return record

    def remove_agent(self, agent_id: str) -> None:
        record = self._roster.get(agent_id)
        if record is None:
            raise ProvisioningError(f"no such agent: {agent_id}")
        self._services.disable(self._s.unit_name(agent_id))
        self._roster.remove(agent_id)
        # Rewire remaining agents so they drop the removed peer.
        remaining = self._roster.load()
        self._mesh.rewire(remaining)
        for r in remaining:
            self._services.restart(self._s.unit_name(r.id))
        # Leave the agent's HERMES_HOME on disk (transcripts are business data);
        # the runbook covers archival deletion. We only remove it from the mesh.

    def set_status(self, agent_id: str, status: AgentStatus) -> AgentRecord:
        record = self._roster.get(agent_id)
        if record is None:
            raise ProvisioningError(f"no such agent: {agent_id}")
        if status is AgentStatus.PAUSED:
            self._services.stop(self._s.unit_name(agent_id))
        elif status is AgentStatus.RUNNING:
            self._services.enable_now(self._s.unit_name(agent_id))
        record.status = status
        self._roster.upsert(record)
        return record

    # -- helpers ---------------------------------------------------------------
    def _write_soul(self, record: AgentRecord) -> None:
        home = self._s.home_dir(record.id)
        home.mkdir(parents=True, exist_ok=True)
        soul = home / "SOUL.md"
        if soul.exists():
            return  # user-owned once created
        rendered = self._jinja.get_template("SOUL.md.j2").render(
            name=record.name,
            role=record.role,
            personality=record.personality.strip(),
        )
        soul.write_text(rendered, "utf-8")

    def _ensure_shared_layout(self) -> None:
        self._s.shared_skills_dir.mkdir(parents=True, exist_ok=True)
        secrets_env = self._s.shared_secrets_env
        if not secrets_env.exists():
            secrets_env.parent.mkdir(parents=True, exist_ok=True)
            secrets_env.write_text(
                "# Shared secrets for ALL agents — chmod 600, never committed.\n"
                "# Populate from config/shared-secrets.env.example (see docs/40).\n",
                "utf-8",
            )
            secrets_env.chmod(0o600)
