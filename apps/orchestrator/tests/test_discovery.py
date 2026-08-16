"""Discovering and adopting Hermes agents that already exist on the machine.

The guarantee that matters: importing must never modify the agent's files. A
user arrives with a working Hermes install and must still have one afterwards.
"""

from __future__ import annotations

import pytest

from recons_orchestrator.config import Settings
from recons_orchestrator.discovery import discover
from recons_orchestrator.provisioning import Provisioner, ProvisioningError
from recons_orchestrator.services import RecordingServiceManager


def _make_home(path, *, name="Sophie", model="gpt-5.6-terra", soul=True):
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.yaml").write_text(
        f"model:\n  provider: openai-codex\n  default: {model}\n"
    )
    if soul:
        (path / "SOUL.md").write_text(f"# {name}\n\nDoes things.\n")
    return path


@pytest.fixture
def provisioner(tmp_path):
    settings = Settings(root=tmp_path / "recons", dashboard_dist=tmp_path / "d")
    return Provisioner(settings, services=RecordingServiceManager())


def test_discovers_the_default_hermes_home(tmp_path, monkeypatch):
    home = _make_home(tmp_path / ".hermes", name="Recon")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("RECONS_DISCOVERY_PATHS", "")

    found = discover()
    assert any(d.home == home.resolve() for d in found)
    entry = next(d for d in found if d.home == home.resolve())
    assert entry.name == "Recon"          # read from SOUL.md
    assert entry.model == "gpt-5.6-terra"  # read from config.yaml


def test_ignores_directories_that_are_not_hermes_homes(tmp_path, monkeypatch):
    empty = tmp_path / "not-hermes"
    empty.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(empty))
    monkeypatch.setenv("RECONS_DISCOVERY_PATHS", "")
    assert discover() == []


def test_extra_search_paths_are_honoured(tmp_path, monkeypatch):
    other = _make_home(tmp_path / "elsewhere" / "agent-a", name="Scout")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "nonexistent"))
    monkeypatch.setenv("RECONS_DISCOVERY_PATHS", str(other))
    assert [d.name for d in discover()] == ["Scout"]


def test_already_imported_is_flagged(tmp_path, monkeypatch):
    home = _make_home(tmp_path / ".hermes")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("RECONS_DISCOVERY_PATHS", "")
    assert discover({home})[0].already_imported is True
    assert discover(set())[0].already_imported is False


# --- adoption -------------------------------------------------------------
def test_import_adds_to_roster_without_touching_the_files(provisioner, tmp_path):
    home = _make_home(tmp_path / "existing")
    before = {p.name: p.read_text() for p in home.iterdir() if p.is_file()}

    rec = provisioner.import_agent(home, name="Sophie", role="Email and calendar")

    assert rec.imported is True
    assert rec.home == str(home.resolve())
    assert rec.role == "Email and calendar"
    assert [a.id for a in provisioner.list_agents()] == ["sophie"]

    after = {p.name: p.read_text() for p in home.iterdir() if p.is_file()}
    assert after == before, "importing must not rewrite the agent's files"


def test_importing_the_same_home_twice_is_refused(provisioner, tmp_path):
    home = _make_home(tmp_path / "existing")
    provisioner.import_agent(home, name="Sophie")
    with pytest.raises(ProvisioningError, match="already imported"):
        provisioner.import_agent(home, name="Different Name")


def test_import_of_missing_directory_is_refused(provisioner, tmp_path):
    with pytest.raises(ProvisioningError, match="no such directory"):
        provisioner.import_agent(tmp_path / "nope")


def test_creating_a_new_agent_never_rewrites_an_imported_one(provisioner, tmp_path):
    from recons_orchestrator.models import AgentSpec

    home = _make_home(tmp_path / "existing")
    provisioner.import_agent(home, name="Sophie")
    before = (home / "config.yaml").read_text()

    provisioner.create_agent(AgentSpec(name="Scout", role="Research"))

    assert (home / "config.yaml").read_text() == before
    assert {a.id for a in provisioner.list_agents()} == {"sophie", "scout"}


def test_imported_agent_history_reaches_the_audit_log(provisioner, tmp_path):
    from recons_orchestrator.ledger import Ledger
    from tests.fixtures import make_state_db

    home = _make_home(tmp_path / "existing")
    make_state_db(home / "state.db", agent="sophie", base_ts=1_786_000_000)
    provisioner.import_agent(home, name="Sophie")

    settings = Settings(root=tmp_path / "recons", dashboard_dist=tmp_path / "d")
    events = Ledger(settings).collect()
    assert any(e.agent_id == "sophie" and e.kind == "message" for e in events)
