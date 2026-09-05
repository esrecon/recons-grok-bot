"""Head-of-staff (lead) reassignment: exactly one lead at a time, explicit
consent before touching imported homes, and managed gateways restarted so the
new personas take effect."""

from __future__ import annotations

import json

import pytest

from recons_orchestrator.models import AgentSpec
from recons_orchestrator.provisioning import ProvisioningError


def _spec(name: str, role: str = "Do the thing") -> AgentSpec:
    return AgentSpec(name=name, role=role)


def _make_home(path, *, name="Hermes"):
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.yaml").write_text(
        "model:\n  provider: nous\n  default: hermes-4-70b\n"
    )
    (path / "SOUL.md").write_text(f"# {name}\n\nMy own soul.\n")
    return path


def test_set_lead_moves_the_badge(provisioner, settings):
    provisioner.create_agent(_spec("Recon"))
    provisioner.create_agent(_spec("Scout"))

    rec = provisioner.set_lead("scout")

    assert rec.is_lead is True
    assert [a.id for a in provisioner.list_agents() if a.is_lead] == ["scout"]
    # On disk too: exactly one lead.
    raw = json.loads((settings.root / "roster.json").read_text())
    assert [r["id"] for r in raw if r["is_lead"]] == ["scout"]


def test_set_lead_is_idempotent(provisioner, settings, services):
    provisioner.create_agent(_spec("Recon"))
    provisioner.set_lead("recon")
    roster_before = (settings.root / "roster.json").read_bytes()
    soul_before = (settings.home_dir("recon") / "SOUL.md").read_bytes()
    services.calls.clear()

    provisioner.set_lead("recon")

    assert (settings.root / "roster.json").read_bytes() == roster_before
    assert (settings.home_dir("recon") / "SOUL.md").read_bytes() == soul_before
    # A no-op reassignment restarts nothing.
    assert services.calls == []


def test_set_lead_unknown_agent_raises(provisioner):
    with pytest.raises(ProvisioningError, match="no such agent"):
        provisioner.set_lead("nope")


def test_set_lead_restarts_managed_but_never_imported(
    provisioner, settings, services, tmp_path
):
    provisioner.create_agent(_spec("Recon"))
    provisioner.import_agent(_make_home(tmp_path / ".hermes"), name="Hermes")
    services.calls.clear()

    provisioner.set_lead("hermes")

    # Managed teammates reload their SOULs; the imported agent has no unit
    # here, and starting one could grab a live messaging channel.
    assert ("restart", settings.unit_name("recon")) in services.calls
    assert not any("hermes-gateway@hermes" in unit for _, unit in services.calls)


def test_removing_the_lead_promotes_the_oldest_managed_agent(provisioner):
    provisioner.create_agent(_spec("Recon"))  # first = lead
    provisioner.create_agent(_spec("Scout"))
    provisioner.create_agent(_spec("Clerk"))

    provisioner.remove_agent("recon")

    assert [a.id for a in provisioner.list_agents() if a.is_lead] == ["scout"]


def test_removing_the_lead_never_auto_promotes_an_imported_agent(
    provisioner, tmp_path
):
    home = _make_home(tmp_path / ".hermes")
    provisioner.create_agent(_spec("Recon"))  # lead
    provisioner.import_agent(home, name="Hermes")

    provisioner.remove_agent("recon")

    # No managed agent left to promote; the roster shows no lead rather than
    # writing into an imported SOUL without consent.
    assert [a.id for a in provisioner.list_agents() if a.is_lead] == []
    assert "recons:team" not in (home / "SOUL.md").read_text()


def test_at_most_one_lead_across_any_sequence(provisioner, tmp_path):
    home = _make_home(tmp_path / ".hermes")
    provisioner.create_agent(_spec("Recon"))
    provisioner.create_agent(_spec("Scout"))
    provisioner.import_agent(home, name="Hermes")

    provisioner.set_lead("scout")
    provisioner.set_lead("hermes")
    provisioner.remove_agent("hermes")

    leads = [a for a in provisioner.list_agents() if a.is_lead]
    assert len(leads) == 1  # the oldest managed agent stepped back up
    assert leads[0].id == "recon"
