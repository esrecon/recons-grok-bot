"""Provisioning-engine tests: one-click create, the shared/own split, and
lifecycle. These are the load-bearing guarantees behind the dashboard button."""

from __future__ import annotations

import yaml

from recons_orchestrator.config import A2A_PORT_BASE
from recons_orchestrator.models import AgentSpec, AgentStatus, ModelTier


def _spec(name: str, role: str = "Do the thing", tier=ModelTier.WORKHORSE) -> AgentSpec:
    return AgentSpec(name=name, role=role, tier=tier)


def test_create_agent_provisions_home_soul_and_config(provisioner, settings):
    rec = provisioner.create_agent(_spec("Recon", "Lead assistant", ModelTier.LEAD))

    assert rec.id == "recon"
    assert rec.is_lead is True  # first agent is the lead
    assert rec.a2a_port == A2A_PORT_BASE
    assert rec.created_at == "2026-08-15T12:00:00+00:00"

    home = settings.home_dir("recon")
    assert (home / "SOUL.md").exists()
    assert (home / "config.yaml").exists()

    soul = (home / "SOUL.md").read_text()
    assert "You are **Recon**" in soul
    assert "Lead assistant" in soul

    cfg = yaml.safe_load((home / "config.yaml").read_text())
    assert cfg["model"]["provider"] == "claude_wrapper"  # LEAD tier
    assert cfg["skills"]["write_approval"] is True
    assert cfg["approvals"]["mode"] == "smart"
    assert cfg["terminal"]["backend"] == "docker"


def test_soul_is_user_owned_after_creation(provisioner, settings):
    provisioner.create_agent(_spec("Scout"))
    soul = settings.home_dir("scout") / "SOUL.md"
    soul.write_text("# Scout\n\nEDITED BY USER\n")

    # Creating another agent rewires the mesh (rewrites configs) but must NOT
    # clobber an existing SOUL.md.
    provisioner.create_agent(_spec("Clerk"))
    assert "EDITED BY USER" in soul.read_text()


def test_shared_skills_and_secrets_are_shared_not_copied(provisioner, settings):
    provisioner.create_agent(_spec("Recon"))
    provisioner.create_agent(_spec("Scout"))

    assert settings.shared_skills_dir.is_dir()
    assert settings.shared_secrets_env.exists()
    # 0600 on the shared secrets file.
    assert (settings.shared_secrets_env.stat().st_mode & 0o777) == 0o600

    # Both agents point at the SAME shared skills dir — no per-agent copy.
    for aid in ("recon", "scout"):
        cfg = yaml.safe_load((settings.home_dir(aid) / "config.yaml").read_text())
        assert cfg["skills"]["external_dirs"] == [str(settings.shared_skills_dir)]


def test_a2a_ports_are_unique_and_sequential(provisioner):
    a = provisioner.create_agent(_spec("Recon"))
    b = provisioner.create_agent(_spec("Scout"))
    c = provisioner.create_agent(_spec("Clerk"))
    assert [a.a2a_port, b.a2a_port, c.a2a_port] == [
        A2A_PORT_BASE,
        A2A_PORT_BASE + 1,
        A2A_PORT_BASE + 2,
    ]


def test_duplicate_name_rejected(provisioner):
    provisioner.create_agent(_spec("Recon"))
    try:
        provisioner.create_agent(_spec("Recon"))
    except Exception as exc:  # ProvisioningError
        assert "already exists" in str(exc)
    else:
        raise AssertionError("expected duplicate-name rejection")


def test_services_started_in_order(provisioner, settings, services):
    provisioner.create_agent(_spec("Recon"))
    provisioner.create_agent(_spec("Scout"))

    # First create: daemon_reload then enable recon.
    # Second create: daemon_reload, restart recon (picks up new peer), enable scout.
    assert ("daemon_reload", "") in services.calls
    assert ("enable_now", settings.unit_name("recon")) in services.calls
    assert ("enable_now", settings.unit_name("scout")) in services.calls
    assert ("restart", settings.unit_name("recon")) in services.calls
    # scout is never restarted during its own creation.
    assert ("restart", settings.unit_name("scout")) not in services.calls


def test_pause_and_resume(provisioner, settings, services):
    provisioner.create_agent(_spec("Recon"))
    paused = provisioner.set_status("recon", AgentStatus.PAUSED)
    assert paused.status is AgentStatus.PAUSED
    assert ("stop", settings.unit_name("recon")) in services.calls
    assert provisioner.get_agent("recon").status is AgentStatus.PAUSED

    resumed = provisioner.set_status("recon", AgentStatus.RUNNING)
    assert resumed.status is AgentStatus.RUNNING


def test_create_survives_a_systemd_failure_with_error_status(settings, fixed_clock, token_factory):
    """By the time services start, the agent exists — a systemd failure must
    mark it errored (with the reason), never surface as a 500 that looks like
    nothing happened. This is exactly what a real install hit."""
    import subprocess

    from recons_orchestrator.provisioning import Provisioner

    class FailingServiceManager:
        def daemon_reload(self):
            raise subprocess.CalledProcessError(
                1, ["systemctl", "--user", "daemon-reload"]
            )

        def enable_now(self, unit): ...
        def stop(self, unit): ...
        def restart(self, unit): ...
        def disable(self, unit): ...

    prov = Provisioner(
        settings, services=FailingServiceManager(),
        clock=fixed_clock, token_factory=token_factory,
    )
    rec = prov.create_agent(_spec("Sophie", "Email and calendar"))

    assert rec.status is AgentStatus.ERROR
    assert "systemctl" in rec.status_detail
    # The agent is real: files on disk, persisted in the roster with the reason.
    assert (settings.home_dir("sophie") / "SOUL.md").exists()
    stored = prov.get_agent("sophie")
    assert stored is not None and stored.status is AgentStatus.ERROR
    assert stored.status_detail == rec.status_detail


def test_remove_agent_rewires_and_frees_port(provisioner, settings, services):
    provisioner.create_agent(_spec("Recon"))
    b = provisioner.create_agent(_spec("Scout"))
    provisioner.remove_agent("recon")

    assert provisioner.get_agent("recon") is None
    assert ("disable", settings.unit_name("recon")) in services.calls

    # scout's config no longer lists recon as a peer.
    cfg = yaml.safe_load((settings.home_dir("scout") / "config.yaml").read_text())
    assert "recon" not in (cfg.get("a2a_agents") or {})

    # A new agent reuses the freed low port.
    c = provisioner.create_agent(_spec("Clerk"))
    assert c.a2a_port == A2A_PORT_BASE
    assert b.a2a_port == A2A_PORT_BASE + 1
