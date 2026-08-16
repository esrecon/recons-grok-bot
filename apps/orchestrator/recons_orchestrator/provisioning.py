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

import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from jinja2 import Environment, PackageLoader, select_autoescape

from . import team
from .config import A2A_PORT_BASE, Settings
from .mesh import Mesh, TokenFactory, _default_token
from .models import AgentRecord, AgentSpec, AgentStatus, slugify
from .roster import Roster
from .services import ServiceManager, SystemdServiceManager

Clock = Callable[[], datetime]

log = logging.getLogger("recons.provisioning")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProvisioningError(RuntimeError):
    pass


class ImportedAgentError(ProvisioningError):
    """A lifecycle action needed systemd, but the agent is imported.

    Imported agents own their homes and (until promoted) run outside this
    platform's units, so pausing/resuming their gateway here is meaningless —
    and starting one could grab a messaging channel the original machine is
    still serving.
    """


def _service_error_detail(exc: Exception) -> str:
    if isinstance(exc, subprocess.CalledProcessError):
        cmd = " ".join(str(c) for c in exc.cmd) if isinstance(exc.cmd, (list, tuple)) else str(exc.cmd)
        return f"service start failed: `{cmd}` exited {exc.returncode}"
    return f"service start failed: {exc}"


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

        # Agents share credentials by design ("share all API keys"): subscription
        # logins (Codex, Nous OAuth) live in the default HERMES_HOME's auth
        # store, so each agent home gets a symlink to it. A symlink, not a copy:
        # refreshed tokens stay shared, and revoking once revokes everywhere.
        self._link_shared_auth(record)

        # Persist roster BEFORE wiring so a failure can't leave orphaned config
        # that the roster doesn't know about.
        self._roster.upsert(record)

        # Rewire the whole mesh (this agent + every existing peer).
        roster_now = self._roster.load()
        self._mesh.rewire(roster_now)
        # Refresh everyone's managed "Your team" SOUL section so existing
        # agents learn about the new teammate (imported homes are only touched
        # if they opted in — see team.py's consent model).
        team.sync(self._s, roster_now)

        # Start services: reload units, (re)start peers so they pick up the new
        # mesh, then start the new agent. By this point the agent EXISTS — its
        # files and roster entry are written — so a systemd failure must not
        # surface as a 500 that looks like nothing happened. Mark the agent as
        # errored instead; the roster shows it with a red status dot and the
        # reason, and a later resume retries the start.
        try:
            self._services.daemon_reload()
            for r in roster_now:
                if r.id == record.id or r.imported:
                    continue
                self._services.restart(self._s.unit_name(r.id))
            self._services.enable_now(self._s.unit_name(record.id))
        except (subprocess.CalledProcessError, OSError) as exc:
            record.status = AgentStatus.ERROR
            record.status_detail = _service_error_detail(exc)
            self._roster.upsert(record)

        return record

    def import_agent(
        self, home: Path, name: str | None = None, role: str = ""
    ) -> AgentRecord:
        """Adopt an existing Hermes home into the roster.

        Non-destructive by design: nothing under `home` is created, moved or
        rewritten. The agent keeps working through the Hermes CLI exactly as
        before; the dashboard gains the ability to see it and read its history.
        """
        home = Path(home)
        if not home.is_dir():
            raise ProvisioningError(f"no such directory: {home}")

        roster = self._roster.load()
        resolved = home.resolve()
        for existing in roster:
            if existing.home and Path(existing.home).resolve() == resolved:
                raise ProvisioningError(f"'{existing.name}' is already imported")

        display = (name or home.name.lstrip(".") or "hermes").strip()
        agent_id = slugify(display)
        if any(r.id == agent_id for r in roster):
            raise ProvisioningError(f"an agent named '{display}' already exists")

        record = AgentRecord(
            id=agent_id,
            name=display,
            role=role or "Imported Hermes agent",
            a2a_port=self._roster.next_a2a_port(A2A_PORT_BASE),
            status=AgentStatus.RUNNING,
            is_lead=(len(roster) == 0),
            created_at=self._clock().isoformat(),
            home=str(resolved),
            imported=True,
        )
        self._roster.upsert(record)
        # Rewire so provisioned agents stay consistent; rewire() skips imported
        # homes, so this cannot touch the adopted agent's files. Likewise the
        # team sync: managed teammates learn about the newcomer, while the
        # imported home itself is left alone (no fence until lead-ification).
        roster_now = self._roster.load()
        self._mesh.rewire(roster_now)
        team.sync(self._s, roster_now)
        return record

    def remove_agent(self, agent_id: str) -> None:
        record = self._roster.get(agent_id)
        if record is None:
            raise ProvisioningError(f"no such agent: {agent_id}")
        if not record.imported:
            # Imported agents never had a unit enabled here — nothing to disable.
            self._services.disable(self._s.unit_name(agent_id))
        self._roster.remove(agent_id)
        # Rewire remaining agents so they drop the removed peer.
        remaining = self._roster.load()
        self._mesh.rewire(remaining)
        # Deleting the lead must not leave the team headless forever: promote
        # the oldest MANAGED agent (roster order = creation order). Imported
        # agents are never auto-promoted — becoming lead writes into their
        # SOUL, which stays an explicit operator action.
        if record.is_lead and not any(r.is_lead for r in remaining):
            fallback = next((r for r in remaining if not r.imported), None)
            if fallback:
                remaining = self._roster.set_lead(fallback.id)
        team.sync(self._s, remaining)
        for r in remaining:
            if r.imported:
                # Same guard as create_agent: "restarting" an imported agent's
                # unit would actually START one against a home we don't manage.
                continue
            try:
                self._services.restart(self._s.unit_name(r.id))
            except (subprocess.CalledProcessError, OSError):
                # The roster change is already durable; one failed restart must
                # not abort the remaining peers or surface as a 500.
                log.warning("restart of %s after removal failed", r.id, exc_info=True)
        # Leave the agent's HERMES_HOME on disk (transcripts are business data);
        # the runbook covers archival deletion. We only remove it from the mesh.

    def set_lead(self, agent_id: str) -> AgentRecord:
        """Make this agent the head of staff — the sole lead.

        Roster metadata plus personas: every agent's managed team section is
        refreshed so the new lead carries the delegation contract and the
        teammates know who to report to. Seeding the fence into an IMPORTED
        home happens only here — the one deliberate exception to the
        never-touch-imported-files promise, triggered by an explicit operator
        action.
        """
        record = self._roster.get(agent_id)
        if record is None:
            raise ProvisioningError(f"no such agent: {agent_id}")
        if record.is_lead:
            # Already the lead — but an agent imported onto an empty roster
            # became lead by default WITHOUT consent to write its SOUL, so it
            # may still lack the contract. Calling this endpoint is that
            # consent: make sure the fence exists, change nothing else.
            records = self._roster.load()
            team.seed(self._s, record, records)
            team.sync(self._s, records)
            return record

        records = self._roster.set_lead(agent_id)
        new_lead = next(r for r in records if r.id == agent_id)
        team.seed(self._s, new_lead, records)
        team.sync(self._s, records)

        # Long-running gateways read SOUL.md at start (docs/50), so restart
        # the managed ones to make the new personas take effect. Per-unit and
        # forgiving: one failed restart must not abort the rest or undo the
        # roster change.
        for r in records:
            if r.imported:
                continue
            try:
                self._services.restart(self._s.unit_name(r.id))
            except (subprocess.CalledProcessError, OSError):
                log.warning("restart of %s after lead change failed", r.id, exc_info=True)
        return new_lead

    def set_status(self, agent_id: str, status: AgentStatus) -> AgentRecord:
        record = self._roster.get(agent_id)
        if record is None:
            raise ProvisioningError(f"no such agent: {agent_id}")
        if record.imported:
            raise ImportedAgentError(
                f"'{record.name}' is imported — its gateway runs outside this "
                "platform until promotion, so there is nothing to pause or "
                "resume here (see docs/70)"
            )
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

    def _link_shared_auth(self, record: AgentRecord) -> None:
        from .chat import ensure_shared_auth

        ensure_shared_auth(self._s.home_dir(record.id))

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
