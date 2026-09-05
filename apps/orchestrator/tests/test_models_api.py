"""Per-agent model selection: the /api/models catalogue, custom providers,
and PATCHing an agent's model (including the rendered providers block)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient  # noqa: F401

from tests.auth import authed_client

from recons_orchestrator.app import (
    create_app,
    get_providers,
    get_provisioner,
    get_settings,
)
from recons_orchestrator.config import TIERS, Settings
from recons_orchestrator.mesh import Mesh
from recons_orchestrator.providers import ProviderService
from recons_orchestrator.provisioning import Provisioner
from recons_orchestrator.roster import Roster
from recons_orchestrator.secrets_store import SecretsError, SecretsStore
from recons_orchestrator.services import RecordingServiceManager


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "no-default-home"))
    settings = Settings(root=tmp_path / "recons", dashboard_dist=tmp_path / "d")
    secrets = SecretsStore(settings.shared_secrets_env)
    secrets.ensure_file()
    services = RecordingServiceManager()
    prov = Provisioner(settings, services=services)

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_provisioner] = lambda: prov
    app.state.providers = ProviderService(secrets, home=tmp_path / "hermes")
    app.dependency_overrides[get_providers] = lambda: app.state.providers
    return SimpleNamespace(
        client=authed_client(app),
        settings=settings,
        secrets=secrets,
        services=services,
        prov=prov,
    )


def _config_of(env, agent_id: str) -> dict:
    return yaml.safe_load(
        (env.settings.home_dir(agent_id) / "config.yaml").read_text("utf-8")
    )


GROK = {
    "label": "Grok",
    "base_url": "https://api.x.ai/v1",
    "model": "grok-4",
    "api_key": "xai-secret-1",
}


# --- /api/models --------------------------------------------------------------


def test_models_lists_tiers_with_availability(env):
    options = env.client.get("/api/models").json()["options"]
    by_tier = {o["tier"]: o for o in options if o["source"] == "tier"}
    assert set(by_tier) == {"lead", "workhorse", "bulk"}
    assert by_tier["bulk"]["provider"] == TIERS["bulk"].provider
    assert by_tier["lead"]["label"] == "Claude"
    assert all(not o["available"] for o in options)  # nothing configured yet

    env.client.put("/api/providers/nous/key", json={"key": "nk"})
    options = env.client.get("/api/models").json()["options"]
    by_tier = {o["tier"]: o for o in options if o["source"] == "tier"}
    assert by_tier["bulk"]["available"] is True
    assert by_tier["lead"]["available"] is False


# --- custom providers ---------------------------------------------------------


def test_add_custom_model_registers_and_stores_key(env):
    resp = env.client.post("/api/models/custom", json=GROK)
    assert resp.status_code == 201
    entry = resp.json()["model"]
    assert entry["id"] == "grok"
    assert entry["key_env"] == "CUSTOM_GROK_API_KEY"
    assert env.secrets.is_set("CUSTOM_GROK_API_KEY")

    listing = env.client.get("/api/models")
    custom = [o for o in listing.json()["options"] if o["source"] == "custom"]
    assert custom == [
        {
            "provider": "grok",
            "model": "grok-4",
            "label": "Grok",
            "tier": None,
            "source": "custom",
            "available": True,
        }
    ]
    assert "xai-secret-1" not in listing.text


def test_add_custom_model_validation(env):
    assert env.client.post("/api/models/custom", json=GROK).status_code == 201
    # Duplicate label → same slug → refused.
    assert env.client.post("/api/models/custom", json=GROK).status_code == 400
    # Shadowing a built-in provider id is refused.
    nous = dict(GROK, label="Nous")
    assert env.client.post("/api/models/custom", json=nous).status_code == 400
    # Keys ride every request: plain http only on loopback.
    insecure = dict(GROK, label="Sketchy", base_url="http://api.example.com/v1")
    assert env.client.post("/api/models/custom", json=insecure).status_code == 400
    loopback = dict(GROK, label="Local", base_url="http://127.0.0.1:9999/v1")
    assert env.client.post("/api/models/custom", json=loopback).status_code == 201


def test_delete_custom_model_refuses_while_in_use(env):
    env.client.post("/api/models/custom", json=GROK)
    env.client.post("/api/agents", json={"name": "Recon", "role": "r", "tier": "bulk"})
    env.client.patch(
        "/api/agents/recon", json={"model_provider": "grok", "model_name": "grok-4"}
    )

    resp = env.client.delete("/api/models/custom/grok")
    assert resp.status_code == 409
    assert "Recon" in resp.json()["detail"]

    # Reset the agent to its tier default, then deletion succeeds and the key
    # is cleared with the registry entry.
    env.client.patch(
        "/api/agents/recon", json={"model_provider": "", "model_name": ""}
    )
    assert env.client.delete("/api/models/custom/grok").status_code == 200
    assert not env.secrets.is_set("CUSTOM_GROK_API_KEY")
    assert env.client.delete("/api/models/custom/grok").status_code == 404


# --- PATCHing an agent's model ------------------------------------------------


def test_patch_model_renders_provider_block_and_survives_boot_rewire(env):
    env.client.post("/api/models/custom", json=GROK)
    env.client.post("/api/agents", json={"name": "Recon", "role": "r", "tier": "bulk"})
    env.services.calls.clear()

    resp = env.client.patch(
        "/api/agents/recon", json={"model_provider": "grok", "model_name": "grok-4"}
    )
    assert resp.status_code == 200
    assert resp.json()["model_provider"] == "grok"

    cfg = _config_of(env, "recon")
    assert cfg["model"] == {"provider": "grok", "default": "grok-4"}
    assert cfg["providers"]["grok"]["base_url"] == "https://api.x.ai/v1"
    # The key is an env reference — the literal secret never lands in config.
    assert cfg["providers"]["grok"]["api_key"] == "${CUSTOM_GROK_API_KEY}"
    raw = (env.settings.home_dir("recon") / "config.yaml").read_text("utf-8")
    assert "xai-secret-1" not in raw
    assert ("restart", "hermes-gateway@recon.service") in env.services.calls

    # The boot-time rewire regenerates configs from the roster: the choice must
    # survive it (it is roster-persisted, not config-persisted).
    Mesh(env.settings).rewire(Roster(env.settings.roster_path).load())
    assert _config_of(env, "recon")["model"]["provider"] == "grok"


def test_patch_model_reset_returns_to_tier_default(env):
    env.client.post("/api/models/custom", json=GROK)
    env.client.post("/api/agents", json={"name": "Recon", "role": "r", "tier": "bulk"})
    env.client.patch(
        "/api/agents/recon", json={"model_provider": "grok", "model_name": "grok-4"}
    )

    resp = env.client.patch(
        "/api/agents/recon", json={"model_provider": "", "model_name": ""}
    )
    assert resp.status_code == 200
    assert resp.json()["model_provider"] is None

    cfg = _config_of(env, "recon")
    assert cfg["model"]["provider"] == TIERS["bulk"].provider
    assert "providers" not in cfg


def test_patch_model_validation(env):
    env.client.post("/api/agents", json={"name": "Recon", "role": "r", "tier": "bulk"})
    # The pair travels together.
    resp = env.client.patch("/api/agents/recon", json={"model_provider": "grok"})
    assert resp.status_code == 400
    assert "together" in resp.json()["detail"]
    # Unknown provider ids are refused (typo protection).
    resp = env.client.patch(
        "/api/agents/recon", json={"model_provider": "grok", "model_name": "grok-4"}
    )
    assert resp.status_code == 400
    # Tier providers are always legal.
    resp = env.client.patch(
        "/api/agents/recon",
        json={"model_provider": "nous", "model_name": "hermes-4-405b"},
    )
    assert resp.status_code == 200
    assert _config_of(env, "recon")["model"]["default"] == "hermes-4-405b"


def test_patch_model_refused_for_imported_agents(env, tmp_path):
    home = tmp_path / "adopted"
    home.mkdir()
    env.prov.import_agent(home, name="Buzz")
    resp = env.client.patch(
        "/api/agents/buzz", json={"model_provider": "nous", "model_name": "x"}
    )
    assert resp.status_code == 409
    assert "imported" in resp.json()["detail"]


# --- secrets allowlist --------------------------------------------------------


def test_custom_key_family_is_writable_nothing_else(env):
    env.secrets.set_many({"CUSTOM_GROK_API_KEY": "v"})  # pattern-accepted
    with pytest.raises(SecretsError):
        env.secrets.set_many({"GROQ_API_KEY": "v"})  # not in the family
    with pytest.raises(SecretsError):
        env.secrets.set_many({"CUSTOM_lower_API_KEY": "v"})  # case matters
