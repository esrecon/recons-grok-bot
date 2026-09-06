"""API tests for the in-app setup surface: what still needs doing, saving keys
from the dashboard, and driving subscription sign-in without a terminal."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient  # noqa: F401

from tests.auth import authed_client

from recons_orchestrator.app import create_app, get_provisioner, get_settings
from recons_orchestrator.config import Settings
from recons_orchestrator.providers import ProviderService
from recons_orchestrator.provisioning import Provisioner
from recons_orchestrator.secrets_store import SecretsStore
from recons_orchestrator.services import RecordingServiceManager


@pytest.fixture
def client(tmp_path):
    settings = Settings(root=tmp_path / "recons", dashboard_dist=tmp_path / "d")
    secrets = SecretsStore(settings.shared_secrets_env)
    secrets.ensure_file()

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_provisioner] = lambda: Provisioner(
        settings, services=RecordingServiceManager()
    )
    app.state.providers = ProviderService(secrets, home=tmp_path / "hermes")
    from recons_orchestrator.app import get_providers

    app.dependency_overrides[get_providers] = lambda: app.state.providers
    return authed_client(app)


def test_setup_starts_incomplete(client):
    body = client.get("/api/setup").json()
    assert body["complete"] is False
    assert body["has_provider"] is False
    assert body["has_agents"] is False
    assert {p["id"] for p in body["providers"]} == {"nous", "openai", "anthropic"}


def test_saving_a_key_through_the_api_configures_a_provider(client):
    resp = client.put("/api/providers/nous/key", json={"key": "nous-abc"})
    assert resp.status_code == 200
    assert resp.json()["state"] == "configured"
    assert client.get("/api/setup").json()["has_provider"] is True


def test_setup_completes_once_a_provider_and_an_agent_exist(client):
    client.put("/api/providers/nous/key", json={"key": "nous-abc"})
    client.post("/api/agents", json={"name": "Recon", "role": "Lead", "tier": "bulk"})
    body = client.get("/api/setup").json()
    assert body["has_agents"] is True
    assert body["complete"] is True


def test_secret_values_never_come_back_over_the_api(client):
    client.put("/api/providers/nous/key", json={"key": "top-secret-key-value"})
    assert "top-secret-key-value" not in client.get("/api/providers").text
    assert "top-secret-key-value" not in client.get("/api/setup").text


def test_empty_key_rejected(client):
    assert client.put("/api/providers/nous/key", json={"key": ""}).status_code == 400


def test_unknown_provider_rejected(client):
    assert client.put("/api/providers/nope/key", json={"key": "x"}).status_code == 400


def test_clearing_a_key(client):
    client.put("/api/providers/nous/key", json={"key": "k"})
    assert client.delete("/api/providers/nous/key").json()["state"] == "not_configured"


def test_login_can_be_started_and_polled(client, monkeypatch):
    from recons_orchestrator import providers as mod

    monkeypatch.setattr(mod, "OPENAI_LOGIN_CMD", "true")
    started = client.post("/api/providers/openai/login")
    assert started.status_code == 201
    login_id = started.json()["id"]
    polled = client.get(f"/api/providers/login/{login_id}")
    assert polled.status_code == 200
    assert polled.json()["id"] == login_id


def test_polling_unknown_login_404s(client):
    assert client.get("/api/providers/login/nope").status_code == 404


def test_login_rejected_for_non_oauth_provider(client):
    assert client.post("/api/providers/nous/login").status_code == 400
