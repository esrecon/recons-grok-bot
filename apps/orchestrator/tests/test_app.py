"""API-surface tests via FastAPI TestClient, with provisioning pointed at a temp
root and systemd stubbed."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from recons_orchestrator.app import create_app, get_provisioner
from recons_orchestrator.provisioning import Provisioner
from recons_orchestrator.services import RecordingServiceManager


@pytest.fixture
def client(settings):
    app = create_app()
    app.dependency_overrides[get_provisioner] = lambda: Provisioner(
        settings, services=RecordingServiceManager()
    )
    return TestClient(app)


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_create_and_list_agent(client):
    resp = client.post(
        "/api/agents",
        json={"name": "Recon", "role": "Lead assistant", "tier": "lead"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == "recon"
    assert body["is_lead"] is True

    listing = client.get("/api/agents").json()
    assert [a["id"] for a in listing] == ["recon"]


def test_duplicate_name_conflicts(client):
    client.post("/api/agents", json={"name": "Recon", "role": "x"})
    dup = client.post("/api/agents", json={"name": "Recon", "role": "y"})
    assert dup.status_code == 409


def test_bad_name_rejected_by_validation(client):
    # Symbols-only name yields no slug.
    resp = client.post("/api/agents", json={"name": "!!!", "role": "x"})
    assert resp.status_code == 422


def test_pause_resume_delete(client):
    client.post("/api/agents", json={"name": "Scout", "role": "x"})

    assert client.post("/api/agents/scout/pause").json()["status"] == "paused"
    assert client.post("/api/agents/scout/resume").json()["status"] == "running"

    assert client.delete("/api/agents/scout").status_code == 204
    assert client.get("/api/agents/scout").status_code == 404


def test_get_missing_agent_404(client):
    assert client.get("/api/agents/nope").status_code == 404


def test_import_the_default_hermes_profile_by_name(client, tmp_path):
    """The exact payload the dashboard sends for ~/.hermes (name "Hermes") must
    import cleanly — this was rejected as a reserved name until the migration
    work."""
    from tests.fixtures import make_state_db

    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "config.yaml").write_text("model:\n  provider: nous\n  default: hermes-4-70b\n")
    make_state_db(home / "state.db", agent="hermes", base_ts=1_786_000_000)

    resp = client.post("/api/import", json={"home": str(home), "name": "Hermes"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == "hermes"
    assert body["imported"] is True

    # The copy's history is immediately readable through the API — the exact
    # contract scripts/migrate-hermes.sh relies on after registering.
    hist = client.get("/api/agents/hermes/history").json()
    assert hist["messages"], "expected turns from the imported state.db"


def test_make_lead_endpoint_moves_the_badge(client):
    client.post("/api/agents", json={"name": "Recon", "role": "x"})
    client.post("/api/agents", json={"name": "Scout", "role": "y"})

    resp = client.post("/api/agents/scout/lead")
    assert resp.status_code == 200
    assert resp.json()["is_lead"] is True

    listing = client.get("/api/agents").json()
    assert [a["id"] for a in listing if a["is_lead"]] == ["scout"]

    assert client.post("/api/agents/nope/lead").status_code == 404


def test_pause_and_resume_of_imported_agents_conflict(client, tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "SOUL.md").write_text("# Hermes\n")
    assert (
        client.post("/api/import", json={"home": str(home), "name": "Hermes"}).status_code
        == 201
    )
    assert client.post("/api/agents/hermes/pause").status_code == 409
    assert client.post("/api/agents/hermes/resume").status_code == 409
