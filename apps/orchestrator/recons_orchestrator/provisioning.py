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

import json
import logging
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import yaml
from jinja2 import Environment, PackageLoader, select_autoescape

from . import custom_models, soul, team, telegram
from .config import A2A_PORT_BASE, Settings, TIERS
from .mesh import Mesh, TokenFactory, _default_token
from .models import AgentRecord, AgentSpec, AgentStatus, PromoteReport, slugify
from .roster import Roster
from .services import ServiceManager, SystemdServiceManager

Clock = Callable[[], datetime]

log = logging.getLogger("recons.provisioning")

# Top-level keys config.yaml.j2 renders. Anything else in an adopted agent's
# config is operator customisation the template cannot model (MCP servers,
# extra peers, tool blocks) — captured into config-extras.yaml at promotion
# instead of being dropped.
TEMPLATE_TOP_LEVEL_KEYS = frozenset(
    {
        "soul_file",
        "model",
        "fallback_providers",
        "skills",
        "approvals",
        "terminal",
        "platform_toolsets",
        "gateway",
        "a2a_agents",
        "webhooks",
    }
)


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

    def service_status(self, agent_id: str) -> str:
        """Live systemd state of the agent's unit (via the ServiceManager seam)."""
        return self._services.status(self._s.unit_name(agent_id))

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
        # roster change. Paused agents stay down — "restart" of a stopped
        # unit would START it, which is never this endpoint's job.
        for r in records:
            if r.imported or r.status is not AgentStatus.RUNNING:
                continue
            try:
                self._services.restart(self._s.unit_name(r.id))
            except (subprocess.CalledProcessError, OSError):
                log.warning("restart of %s after lead change failed", r.id, exc_info=True)
        return new_lead

    def promote_agent(
        self,
        agent_id: str,
        *,
        telegram_enabled: bool = False,
        telegram_allowed_users: str = "",
        telegram_token: str | None = None,
        make_lead: bool = False,
    ) -> PromoteReport:
        """Adopt an imported agent as fully managed — the cutover step.

        From here on the orchestrator owns its config.yaml/service.env: it
        joins the A2A mesh (its edge tokens were pre-minted by every rewire)
        and can carry the template-managed Telegram gateway. The home is
        relocated into the canonical layout when it lives elsewhere, because
        the systemd unit hardwires EnvironmentFile/ReadWritePaths to
        /opt/recons/agents/<id>/. This is where "non-destructive import"
        deliberately ends — the home here is a snapshot copy; the original
        still exists on the source machine.

        Starts NOTHING: the record comes out PAUSED, and the cutover script
        owns the moment a gateway begins polling (one bot token, one live
        connection). Resume it via POST /api/agents/<id>/resume.
        """
        record = self._roster.get(agent_id)
        if record is None:
            raise ProvisioningError(f"no such agent: {agent_id}")
        if not record.imported:
            raise ProvisioningError(f"'{record.name}' is not an imported agent")

        home = Path(record.home) if record.home else self._s.home_dir(agent_id)
        old_cfg: dict = {}
        cfg_file = home / "config.yaml"
        if cfg_file.is_file():
            try:
                loaded = yaml.safe_load(cfg_file.read_text("utf-8"))
                old_cfg = loaded if isinstance(loaded, dict) else {}
            except yaml.YAMLError:
                old_cfg = {}

        # Telegram intake first, so a missing token/allowlist fails the whole
        # promote before anything is mutated.
        token_source = "none"
        token = (telegram_token or "").strip() or None
        allowed = telegram_allowed_users.strip()
        if telegram_enabled:
            extracted_token, extracted_users = telegram.extract_telegram(home)
            if token:
                token_source = "provided"
            elif extracted_token:
                token, token_source = extracted_token, "extracted"
            else:
                raise ProvisioningError(
                    "telegram_enabled needs a bot token: none was provided and "
                    "none could be extracted from the copied home (docs/35)"
                )
            if not allowed and extracted_users:
                allowed = extracted_users
            if not allowed:
                raise ProvisioningError(
                    "telegram_enabled needs telegram_allowed_users (your numeric "
                    "Telegram user id) — the old machine's env allowlist does "
                    "not travel with the home"
                )

        # Relocate into the canonical layout if needed (no-op for homes the
        # migration script pulled — it lands them there directly).
        canonical = self._s.home_dir(agent_id)
        if home.resolve() != canonical.resolve():
            if canonical.exists():
                raise ProvisioningError(
                    f"cannot relocate {home}: {canonical} already exists"
                )
            canonical.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(home), str(canonical))
            home = canonical

        # Archive the config the agent arrived with: the rewire below
        # overwrites it, the report points at this file, and demote restores it.
        bak = home / "config.yaml.pre-promote.bak"
        if (home / "config.yaml").is_file():
            shutil.copy2(home / "config.yaml", bak)

        # Capture what the template can't model into config-extras.yaml so it
        # survives every regeneration (the mesh re-appends that file to the
        # generated config). Data folders the migration brought across get
        # their paths rewritten from the stamp, so e.g. an MCP vault points at
        # its new local copy instead of a directory on the old machine.
        captured = {k: v for k, v in old_cfg.items() if k not in TEMPLATE_TOP_LEVEL_KEYS}
        extras_file = self._s.config_extras(agent_id)
        if captured and not extras_file.exists():
            text = yaml.safe_dump(captured, sort_keys=False, default_flow_style=False)
            for remote, local in self._migration_path_map(agent_id):
                text = text.replace(remote, local)
            extras_file.parent.mkdir(parents=True, exist_ok=True)
            extras_file.write_text(
                "# Captured from the pre-promotion config — user-owned from here on.\n"
                "# The mesh appends this file verbatim to the generated config.yaml on\n"
                "# every regeneration, so edits made here are durable.\n" + text,
                "utf-8",
            )

        # Capture the model it arrived with (same keys inherited_model reads),
        # so the rendered config keeps its brain instead of the tier constants.
        old_model = old_cfg.get("model") if isinstance(old_cfg.get("model"), dict) else {}
        provider = old_model.get("provider")
        default = old_model.get("default") or old_model.get("model")
        model_captured = bool(provider and default)

        record.imported = False
        record.home = None
        record.status = AgentStatus.PAUSED  # nothing runs until the cutover resumes it
        record.telegram_enabled = telegram_enabled
        record.telegram_allowed_users = allowed if telegram_enabled else ""
        if model_captured:
            record.model_provider = str(provider)
            record.model_name = str(default)
        # Field assignment skips pydantic validation — re-validate so a bad
        # allowlist can never reach the roster/mesh.
        record = AgentRecord.model_validate(record.model_dump())

        if token:
            telegram.store_token(self._s, agent_id, token)

        self._roster.upsert(record)
        records = self._roster.set_lead(agent_id) if make_lead else self._roster.load()
        self._mesh.rewire(records)  # now writes this agent's config + service.env too
        team.sync(self._s, records)  # managed now → gets the team fence (lead variant if make_lead)

        # Peers' mesh configs changed (live endpoint now) and possibly their
        # personas; restart the RUNNING managed ones so they can actually call
        # the newcomer. The promoted agent itself stays down — deliberately.
        for r in records:
            if r.id == agent_id or r.imported or r.status is not AgentStatus.RUNNING:
                continue
            try:
                self._services.restart(self._s.unit_name(r.id))
            except (subprocess.CalledProcessError, OSError):
                log.warning("restart of %s after promotion failed", r.id, exc_info=True)

        return PromoteReport(
            record=self._roster.get(agent_id) or record,
            model_captured=model_captured,
            telegram_token_source=token_source,
            captured_config_keys=sorted(captured),
            extras_path=str(extras_file),
            backup_path=str(bak),
        )

    def demote_agent(self, agent_id: str) -> AgentRecord:
        """Roll a promoted agent back to imported — the cutover rollback.

        Stops/disables the gateway unit here, restores the archived
        pre-promote config, removes the generated service.env (so an
        accidental start fails closed again), turns Telegram off, and hands
        the home back to the agent (imported=True). The stored bot token is
        kept — chmod 600 and write-only — so a later re-promotion doesn't
        need it re-pasted. Files are not moved back and the old machine is
        not touched; the cutover script owns restarting its gateway.
        """
        record = self._roster.get(agent_id)
        if record is None:
            raise ProvisioningError(f"no such agent: {agent_id}")
        if record.imported:
            raise ProvisioningError(f"'{record.name}' is already imported")
        home = self._s.home_dir(agent_id)
        bak = home / "config.yaml.pre-promote.bak"
        if not bak.is_file():
            raise ProvisioningError(
                f"'{record.name}' was never promoted here (no {bak.name})"
            )

        try:
            self._services.disable(self._s.unit_name(agent_id))
        except (subprocess.CalledProcessError, OSError):
            log.warning("disable of %s during demote failed", agent_id, exc_info=True)

        shutil.copy2(bak, home / "config.yaml")
        self._s.service_env(agent_id).unlink(missing_ok=True)

        record.imported = True
        record.home = str(home)
        record.status = AgentStatus.RUNNING  # running elsewhere, as at import
        record.telegram_enabled = False
        record.telegram_allowed_users = ""
        self._roster.upsert(record)

        remaining = self._roster.load()
        # A demoted lead can't route anything (its A2A endpoint is dead
        # again): hand the badge to the oldest managed agent, as remove does.
        if record.is_lead:
            fallback = next((r for r in remaining if not r.imported), None)
            if fallback:
                remaining = self._roster.set_lead(fallback.id)
        self._mesh.rewire(remaining)  # skips the (again-)imported home
        team.sync(self._s, remaining)
        return self._roster.get(agent_id) or record

    def set_telegram(
        self,
        agent_id: str,
        *,
        enabled: bool,
        allowed_users: str = "",
        token: str | None = None,
    ) -> AgentRecord:
        """Template-managed Telegram for a MANAGED agent.

        Imported agents are refused: theirs is enabled at promotion by the
        cutover script, because the source machine may still hold the bot's
        single live connection.
        """
        record = self._roster.get(agent_id)
        if record is None:
            raise ProvisioningError(f"no such agent: {agent_id}")
        if record.imported:
            raise ImportedAgentError(
                f"'{record.name}' is imported — Telegram moves here at "
                "promotion (scripts/telegram-cutover.sh), not before: the "
                "original machine may still hold the bot's one live connection"
            )

        if token and token.strip():
            telegram.store_token(self._s, agent_id, token.strip())
        if enabled and not telegram.load_tokens(self._s).get(agent_id):
            raise ProvisioningError(
                f"no bot token stored for '{record.name}' — include one in the request"
            )

        record.telegram_enabled = enabled
        record.telegram_allowed_users = allowed_users if enabled else ""
        record = AgentRecord.model_validate(record.model_dump())  # allowlist shape
        self._roster.upsert(record)
        self._mesh.rewire(self._roster.load())

        # Reload a RUNNING gateway so the change takes effect now; a paused
        # unit stays down (restart would start it).
        if record.status is AgentStatus.RUNNING:
            try:
                self._services.restart(self._s.unit_name(agent_id))
            except (subprocess.CalledProcessError, OSError):
                log.warning("restart of %s after telegram change failed", agent_id, exc_info=True)
        return record

    def update_agent(
        self,
        agent_id: str,
        *,
        name: str | None = None,
        role: str | None = None,
        personality: str | None = None,
        avatar_color: str | None = None,
        model_provider: str | None = None,
        model_name: str | None = None,
    ) -> AgentRecord:
        """Edit an agent's identity, persona fields, or model from the Customize tab.

        `None` means "leave alone". The agent's id never changes — it is
        load-bearing (home path, unit name, A2A peer ids, ledger key), so a
        rename is display-only. model_provider/model_name come as a pair; both
        empty strings reset the agent to its tier's default model.

        Blast radius follows what each field reaches: name/role live in every
        peer's managed SOUL section, so those changes sync the team and restart
        all running managed agents (the set_lead pattern); personality lives
        only in this agent's SOUL and the model only in its config, so those
        restart just the target. Colour is roster-only.
        """
        record = self._roster.get(agent_id)
        if record is None:
            raise ProvisioningError(f"no such agent: {agent_id}")

        old_name = record.name
        changed: set[str] = set()

        # Validate everything before mutating anything.
        if name is not None and name.strip() != record.name:
            proposed = name.strip()
            for other in self._roster.load():
                if other.id != agent_id and other.name.strip().lower() == proposed.lower():
                    raise ProvisioningError(f"an agent named '{proposed}' already exists")
            changed.add("name")
        model_touched = model_provider is not None or model_name is not None
        new_provider, new_model = record.model_provider, record.model_name
        if model_touched:
            if record.imported:
                raise ImportedAgentError(
                    f"'{record.name}' is imported — it runs against its own "
                    "config, which this platform never rewrites; promote it "
                    "first to manage its model here"
                )
            mp = (model_provider or "").strip()
            mn = (model_name or "").strip()
            if bool(mp) != bool(mn):
                raise ProvisioningError(
                    "model_provider and model_name go together — set both, or "
                    "both empty to reset to the tier default"
                )
            if mp and mp not in {t.provider for t in TIERS.values()}:
                if custom_models.get(self._s, mp) is None:
                    raise ProvisioningError(f"unknown model provider: {mp}")
            new_provider, new_model = (mp or None, mn or None)
            if (new_provider, new_model) != (record.model_provider, record.model_name):
                changed.add("model")

        if name is not None and "name" in changed:
            record.name = name.strip()
        if role is not None and role.strip() != record.role:
            record.role = role.strip()
            changed.add("role")
        if personality is not None and personality.strip() != record.personality:
            record.personality = personality.strip()
            changed.add("personality")
        if avatar_color is not None and avatar_color != record.avatar_color:
            record.avatar_color = avatar_color
            changed.add("avatar_color")
        record.model_provider, record.model_name = new_provider, new_model

        if not changed:
            return record

        # Field assignment skips pydantic validation — re-validate, as promote does.
        record = AgentRecord.model_validate(record.model_dump())
        self._roster.upsert(record)

        # Keep the template-derived SOUL lines in step (user content untouched;
        # imported homes never written — the never-touch promise holds).
        if changed & {"name", "role", "personality"} and not record.imported:
            soul.apply_patch(
                self._s,
                record,
                old_name=old_name if "name" in changed else None,
                role_changed="role" in changed,
                personality_changed="personality" in changed,
            )

        records = self._roster.load()
        if changed & {"name", "model"}:
            # config.yaml/service.env carry the display name in comments and
            # the model block; regeneration is cheap and idempotent.
            self._mesh.rewire(records)
        if changed & {"name", "role"}:
            # Every peer's managed team section names this agent and its job.
            team.sync(self._s, records)
            restart_ids = [
                r.id
                for r in records
                if not r.imported and r.status is AgentStatus.RUNNING
            ]
        elif changed & {"model", "personality"}:
            restart_ids = (
                [agent_id]
                if not record.imported and record.status is AgentStatus.RUNNING
                else []
            )
        else:
            restart_ids = []
        for rid in restart_ids:
            try:
                self._services.restart(self._s.unit_name(rid))
            except (subprocess.CalledProcessError, OSError):
                log.warning("restart of %s after update failed", rid, exc_info=True)
        return record

    def set_avatar_version(self, agent_id: str, version: int) -> AgentRecord:
        record = self._roster.get(agent_id)
        if record is None:
            raise ProvisioningError(f"no such agent: {agent_id}")
        record.avatar_version = version
        self._roster.upsert(record)
        return record

    def read_soul(self, agent_id: str) -> tuple[str, bool]:
        """(content, exists) for the agent's SOUL.md."""
        record = self._roster.get(agent_id)
        if record is None:
            raise ProvisioningError(f"no such agent: {agent_id}")
        path = soul.soul_path(self._s, record)
        if not path.is_file():
            return "", False
        return path.read_text("utf-8"), True

    def write_soul(self, agent_id: str, content: str) -> str:
        """Save an edited SOUL.md; returns the file's final text.

        The managed team fence is repaired server-side after the write, so an
        edit (or an over-eager LLM rewrite) can never lose it — same consent
        rule as team.sync: managed agents always carry it, an imported home
        only keeps one it already had in the submitted content.
        """
        record = self._roster.get(agent_id)
        if record is None:
            raise ProvisioningError(f"no such agent: {agent_id}")
        path = soul.soul_path(self._s, record)
        text = content if content.endswith("\n") or not content else content + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, "utf-8")
        if (not record.imported) or team.TEAM_BEGIN in text:
            team.upsert_section(path, team.render_section(record, self._roster.load()))
        # Gateways read SOUL.md at start — reload a running managed one now.
        if not record.imported and record.status is AgentStatus.RUNNING:
            try:
                self._services.restart(self._s.unit_name(agent_id))
            except (subprocess.CalledProcessError, OSError):
                log.warning("restart of %s after soul edit failed", agent_id, exc_info=True)
        return path.read_text("utf-8")

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
    def _migration_path_map(self, agent_id: str) -> list[tuple[str, str]]:
        """(remote, local) path pairs for data folders the migration copied.

        migrate-hermes.sh records them in the stamp file's also_paths; promote
        uses them to rewrite paths inside captured config blocks."""
        stamp = self._s.agents_dir / agent_id / ".migrate-hermes.json"
        try:
            data = json.loads(stamp.read_text("utf-8"))
        except (OSError, ValueError):
            return []
        pairs: list[tuple[str, str]] = []
        for entry in data.get("also_paths") or []:
            if isinstance(entry, dict) and entry.get("remote") and entry.get("local"):
                pairs.append((str(entry["remote"]), str(entry["local"])))
        return pairs

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
