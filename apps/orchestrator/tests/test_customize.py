"""API tests for the Customize tab: PATCH agent, soul read/write, the
✨ Improve endpoint, avatar generation, and the image settings."""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient  # noqa: F401

from tests.auth import authed_client

from recons_orchestrator import assist, avatars, chat, team
from recons_orchestrator.app import (
    create_app,
    get_providers,
    get_provisioner,
    get_settings,
)
from recons_orchestrator.config import Settings
from recons_orchestrator.providers import ProviderService
from recons_orchestrator.provisioning import Provisioner
from recons_orchestrator.secrets_store import SecretsStore
from recons_orchestrator.services import RecordingServiceManager

# A 1x1 transparent PNG — small enough to inline, real enough to sniff.
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text="", content=b""):
        self.status_code = status_code
        self._json = json_data
        self.text = text
        self.content = content

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


@pytest.fixture
def env(tmp_path, monkeypatch):
    # No inherited workhorse model from the machine running the tests.
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


def _soul_text(env, agent_id: str) -> str:
    return (env.settings.home_dir(agent_id) / "SOUL.md").read_text("utf-8")


@pytest.fixture
def two_agents(env):
    env.client.post(
        "/api/agents", json={"name": "Recon", "role": "Fix consoles", "tier": "bulk"}
    )
    env.client.post(
        "/api/agents", json={"name": "Scout", "role": "Find parts", "tier": "bulk"}
    )
    env.services.calls.clear()
    return env


# --- PATCH /api/agents/{id} ---------------------------------------------------


def test_rename_is_display_only_and_reaches_souls(two_agents):
    env = two_agents
    resp = env.client.patch("/api/agents/recon", json={"name": "Ranger"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "recon"  # the id is load-bearing and never changes
    assert body["name"] == "Ranger"

    mine = _soul_text(env, "recon")
    assert mine.startswith("# Ranger\n")
    assert "You are **Ranger**" in mine
    # The peer's managed team section names teammates: it must show the new name.
    assert "Ranger" in _soul_text(env, "scout")
    # Both running managed agents restart (personas are read at start).
    restarted = {u for op, u in env.services.calls if op == "restart"}
    assert restarted == {"hermes-gateway@recon.service", "hermes-gateway@scout.service"}


def test_role_change_updates_soul_and_peers_preserving_user_content(two_agents):
    env = two_agents
    # Simulate a user edit outside the managed pieces.
    path = env.settings.home_dir("recon") / "SOUL.md"
    path.write_text(
        _soul_text(env, "recon") + "\n## My notes\n\nHand-written.\n", "utf-8"
    )
    before = _soul_text(env, "recon")
    fence = before[before.index(team.TEAM_BEGIN) : before.index(team.TEAM_END)]

    resp = env.client.patch("/api/agents/recon", json={"role": "Repair controllers"})
    assert resp.status_code == 200

    after = _soul_text(env, "recon")
    assert "## Your job\n\nRepair controllers\n" in after
    assert "Hand-written." in after
    assert fence in after  # fence bytes untouched by the section edit
    assert "Repair controllers" in _soul_text(env, "scout")


def test_personality_insert_replace_clear_restarts_only_target(two_agents):
    env = two_agents
    env.client.patch("/api/agents/recon", json={"personality": "Be brief."})
    assert "## How you work\n\nBe brief.\n" in _soul_text(env, "recon")
    assert {u for op, u in env.services.calls if op == "restart"} == {
        "hermes-gateway@recon.service"
    }

    env.client.patch("/api/agents/recon", json={"personality": "Be thorough."})
    assert "Be thorough." in _soul_text(env, "recon")

    env.client.patch("/api/agents/recon", json={"personality": ""})
    assert "## How you work" not in _soul_text(env, "recon")


def test_color_only_change_restarts_nothing(two_agents):
    env = two_agents
    resp = env.client.patch("/api/agents/recon", json={"avatar_color": "#AABB01"})
    assert resp.status_code == 200
    assert resp.json()["avatar_color"] == "#aabb01"
    assert env.services.calls == []


def test_patch_validation_and_conflicts(two_agents):
    env = two_agents
    assert env.client.patch("/api/agents/nope", json={"role": "x"}).status_code == 404
    assert (
        env.client.patch("/api/agents/recon", json={"name": "scout"}).status_code == 409
    )  # case-insensitive display-name collision
    assert (
        env.client.patch("/api/agents/recon", json={"avatar_color": "red"}).status_code
        == 422
    )
    assert (
        env.client.patch("/api/agents/recon", json={"name": "api"}).status_code == 422
    )  # reserved name, validator parity with create
    # A no-op PATCH succeeds and changes nothing.
    assert env.client.patch("/api/agents/recon", json={}).status_code == 200
    assert env.services.calls == []


def test_patch_imported_agent_touches_roster_only(env, tmp_path):
    home = tmp_path / "adopted"
    home.mkdir()
    (home / "SOUL.md").write_text("# Buzz\n\nOriginal soul.\n", "utf-8")
    env.prov.import_agent(home, name="Buzz", role="Old role")
    env.services.calls.clear()

    resp = env.client.patch("/api/agents/buzz", json={"role": "New role"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "New role"
    # The imported home is never written, and no unit of ours restarts.
    assert (home / "SOUL.md").read_text("utf-8") == "# Buzz\n\nOriginal soul.\n"
    assert env.services.calls == []


# --- soul endpoints -----------------------------------------------------------


def test_soul_roundtrip_and_fence_repair(two_agents):
    env = two_agents
    body = env.client.get("/api/agents/recon/soul").json()
    assert body["exists"] is True
    assert body["content"].startswith("# Recon\n")
    assert team.TEAM_BEGIN in body["content"]

    # Save an edit that (deliberately) deleted the managed fence entirely.
    edited = "# Recon\n\nMy own words.\n\n## Your job\n\nFix consoles\n"
    resp = env.client.put("/api/agents/recon/soul", json={"content": edited})
    assert resp.status_code == 200
    final = resp.json()["content"]
    assert "My own words." in final
    assert final.count(team.TEAM_BEGIN) == 1  # repaired, exactly once
    assert final == _soul_text(env, "recon")  # response is the file as written
    assert ("restart", "hermes-gateway@recon.service") in env.services.calls


def test_soul_put_never_consented_import_gets_no_fence(env, tmp_path):
    home = tmp_path / "adopted"
    home.mkdir()
    (home / "SOUL.md").write_text("# Buzz\n", "utf-8")
    env.prov.import_agent(home, name="Buzz")
    # Second managed agent exists so a fence would have teammates to name.
    env.client.post("/api/agents", json={"name": "Recon", "role": "r", "tier": "bulk"})

    resp = env.client.put("/api/agents/buzz/soul", json={"content": "# Buzz\n\nEdited.\n"})
    assert resp.status_code == 200
    assert team.TEAM_BEGIN not in resp.json()["content"]

    # But an imported soul that kept its fence keeps it refreshed.
    with_fence = "# Buzz\n\n" + team.TEAM_BEGIN + " -->\nstale\n" + team.TEAM_END + "\n"
    resp = env.client.put("/api/agents/buzz/soul", json={"content": with_fence})
    assert resp.json()["content"].count(team.TEAM_BEGIN) == 1


def test_soul_unknown_agent_404(env):
    assert env.client.get("/api/agents/ghost/soul").status_code == 404
    assert (
        env.client.put("/api/agents/ghost/soul", json={"content": "x"}).status_code
        == 404
    )


# --- POST /api/assist/improve -------------------------------------------------


def test_improve_uses_nous_when_key_is_set(two_agents, monkeypatch):
    env = two_agents
    env.secrets.set_many({"NOUS_API_KEY": "nk-1"})
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.update(url=url, headers=headers, payload=json)
        return FakeResponse(
            json_data={"choices": [{"message": {"content": "  Polished text.  "}}]}
        )

    monkeypatch.setattr(assist.httpx, "post", fake_post)
    resp = env.client.post(
        "/api/assist/improve",
        json={
            "field": "personality",
            "text": "be nice",
            "agent_context": {"name": "Recon", "role": "Fix consoles"},
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"text": "Polished text."}
    assert seen["url"] == f"{assist.NOUS_BASE_URL}/chat/completions"
    assert seen["headers"]["authorization"] == "Bearer nk-1"
    assert seen["payload"]["model"] == "hermes-4-70b"
    assert "Recon" in seen["payload"]["messages"][0]["content"]
    assert seen["payload"]["messages"][1]["content"] == "be nice"


def test_improve_clamps_to_field_limits(two_agents, monkeypatch):
    env = two_agents
    env.secrets.set_many({"NOUS_API_KEY": "nk-1"})
    long = "word " * 60  # 300 chars, far over the 120-char role limit
    monkeypatch.setattr(
        assist.httpx,
        "post",
        lambda *a, **k: FakeResponse(
            json_data={"choices": [{"message": {"content": long}}]}
        ),
    )
    resp = env.client.post(
        "/api/assist/improve", json={"field": "role", "text": "fix stuff"}
    )
    assert resp.status_code == 200
    out = resp.json()["text"]
    assert len(out) <= 120
    assert not out.endswith(" ")


def test_improve_uses_hermes_cli_when_no_key_is_stored(two_agents, monkeypatch):
    env = two_agents  # no NOUS key: the normal setup signs in through Hermes
    seen = {}

    def fake_run(argv, stdin=None, capture_output=None, text=None, timeout=None, env=None):
        seen.update(argv=argv, env=env)
        return SimpleNamespace(returncode=0, stdout="  Polished by Hermes.  \n", stderr="")

    monkeypatch.setattr(chat.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")
    monkeypatch.setattr(assist.subprocess, "run", fake_run)
    monkeypatch.setattr(
        assist.httpx, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("HTTP used"))
    )

    resp = env.client.post(
        "/api/assist/improve",
        json={"field": "personality", "text": "be nice", "agent_context": {"name": "Recon", "role": "Fix consoles"}},
    )
    assert resp.status_code == 200
    assert resp.json() == {"text": "Polished by Hermes."}
    # One argv element carries the whole composed prompt (chat_command shape).
    prompt = seen["argv"][-1]
    assert "Recon" in prompt and "be nice" in prompt
    assert seen["env"]["HERMES_HOME"]  # runs against the default home


def test_improve_cli_failure_is_502_with_stderr(two_agents, monkeypatch):
    env = two_agents
    monkeypatch.setattr(chat.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")
    monkeypatch.setattr(
        assist.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="auth expired: boom"),
    )
    resp = env.client.post("/api/assist/improve", json={"field": "name", "text": "x"})
    assert resp.status_code == 502
    assert "boom" in resp.json()["detail"]  # the CLI's own words reach the UI


def test_resolve_cmd_finds_the_cli_off_path(tmp_path, monkeypatch):
    # A systemd unit's minimal PATH misses the venv entry point; resolve_cmd
    # probes the known install locations and returns an absolute argv[0].
    fake_bin = tmp_path / "venv-bin"
    fake_bin.mkdir()
    cli = fake_bin / "hermes"
    cli.write_text("#!/bin/sh\n", "utf-8")
    cli.chmod(0o755)

    monkeypatch.setattr(chat.shutil, "which", lambda cmd: None)
    monkeypatch.setattr(assist.shutil, "which", lambda cmd: None)
    monkeypatch.setattr(chat, "_CLI_CANDIDATE_DIRS", (fake_bin,))
    assert chat.resolve_cmd(["hermes", "-z", "hi"]) == [str(cli), "-z", "hi"]
    assert assist.hermes_cli_available() is True

    # Explicit paths and PATH-resolvable commands come back untouched.
    assert chat.resolve_cmd(["/usr/bin/env", "x"]) == ["/usr/bin/env", "x"]
    monkeypatch.setattr(chat, "_CLI_CANDIDATE_DIRS", ())
    assert chat.resolve_cmd(["hermes"]) == ["hermes"]
    assert assist.hermes_cli_available() is False


def test_improve_falls_back_to_wrapper_then_503(two_agents, monkeypatch):
    env = two_agents  # no NOUS key stored
    monkeypatch.setattr(assist, "hermes_cli_available", lambda: False)

    monkeypatch.setattr(assist, "wrapper_alive", lambda secrets: True)
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["url"] = url
        return FakeResponse(json_data={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(assist.httpx, "post", fake_post)
    resp = env.client.post("/api/assist/improve", json={"field": "name", "text": "bob"})
    assert resp.status_code == 200
    assert seen["url"] == f"{assist.DEFAULT_WRAPPER_BASE}/chat/completions"

    monkeypatch.setattr(assist, "wrapper_alive", lambda secrets: False)
    resp = env.client.post("/api/assist/improve", json={"field": "name", "text": "bob"})
    assert resp.status_code == 503
    assert "Hermes CLI" in resp.json()["detail"]


def test_improve_upstream_error_is_502(two_agents, monkeypatch):
    env = two_agents
    env.secrets.set_many({"NOUS_API_KEY": "nk-1"})
    monkeypatch.setattr(
        assist.httpx, "post", lambda *a, **k: FakeResponse(status_code=500)
    )
    resp = env.client.post("/api/assist/improve", json={"field": "name", "text": "x"})
    assert resp.status_code == 502


# --- avatars ------------------------------------------------------------------


def test_avatar_requires_key_then_generates_and_serves(two_agents, monkeypatch):
    env = two_agents
    assert env.client.get("/api/agents/recon/avatar").status_code == 404
    resp = env.client.post("/api/agents/recon/avatar")
    assert resp.status_code == 503  # no IMAGE_API_KEY yet

    env.secrets.set_many({"IMAGE_API_KEY": "img-1"})
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.update(url=url, payload=json)
        return FakeResponse(
            json_data={"data": [{"b64_json": base64.b64encode(PNG_1PX).decode()}]}
        )

    monkeypatch.setattr(avatars.httpx, "post", fake_post)
    resp = env.client.post("/api/agents/recon/avatar")
    assert resp.status_code == 200
    version = resp.json()["avatar_version"]
    assert isinstance(version, int)
    assert seen["url"] == f"{avatars.DEFAULT_IMAGE_BASE_URL}/images/generations"
    assert seen["payload"]["model"] == avatars.DEFAULT_IMAGE_MODEL
    assert "Fix consoles" in seen["payload"]["prompt"]
    assert (env.settings.agents_dir / "recon" / "avatar.png").read_bytes() == PNG_1PX

    got = env.client.get("/api/agents/recon/avatar")
    assert got.status_code == 200
    assert got.headers["content-type"] == "image/png"
    assert "immutable" in got.headers["cache-control"]
    assert got.content == PNG_1PX


def test_avatar_response_format_retry_and_url_branch(two_agents, monkeypatch):
    env = two_agents
    env.secrets.set_many({"IMAGE_API_KEY": "img-1"})
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(dict(json))
        if "response_format" in json:
            # gpt-image-1 style rejection of the parameter
            return FakeResponse(status_code=400, text="Unknown parameter: response_format")
        return FakeResponse(json_data={"data": [{"url": "https://img.example/x.jpg"}]})

    jpeg = b"\xff\xd8\xff\xe0" + b"0" * 8
    monkeypatch.setattr(avatars.httpx, "post", fake_post)
    monkeypatch.setattr(
        avatars.httpx, "get", lambda url, timeout=None: FakeResponse(content=jpeg)
    )
    resp = env.client.post("/api/agents/scout/avatar")
    assert resp.status_code == 200
    assert len(calls) == 2 and "response_format" not in calls[1]
    # JPEG bytes are served with the sniffed media type despite the .png name.
    got = env.client.get("/api/agents/scout/avatar")
    assert got.headers["content-type"] == "image/jpeg"


def test_avatar_unknown_agent_404(env):
    assert env.client.post("/api/agents/ghost/avatar").status_code == 404
    assert env.client.get("/api/agents/ghost/avatar").status_code == 404


def test_stale_stored_model_is_remapped(env, two_agents, monkeypatch):
    # A preset once wrote grok-2-image; xAI retired it. The stored value heals
    # at read time — reported by the settings API and used by generate().
    env.secrets.set_many(
        {"IMAGE_API_KEY": "img-1", "IMAGE_API_MODEL": "grok-2-image"}
    )
    assert env.client.get("/api/settings/image").json()["model"] == (
        avatars.DEFAULT_IMAGE_MODEL
    )

    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["model"] = json["model"]
        return FakeResponse(
            json_data={"data": [{"b64_json": base64.b64encode(PNG_1PX).decode()}]}
        )

    monkeypatch.setattr(avatars.httpx, "post", fake_post)
    assert env.client.post("/api/agents/recon/avatar").status_code == 200
    assert seen["model"] == avatars.DEFAULT_IMAGE_MODEL


def test_unknown_model_retries_once_with_default(env, two_agents, monkeypatch):
    # A custom id the provider has since retired: 404 → one retry with the
    # current default, and the upstream error text survives if that fails too.
    env.secrets.set_many(
        {"IMAGE_API_KEY": "img-1", "IMAGE_API_MODEL": "grok-9-imaginary"}
    )
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json["model"])
        if json["model"] == "grok-9-imaginary":
            return FakeResponse(status_code=404, text='{"error":"model not found"}')
        return FakeResponse(
            json_data={"data": [{"b64_json": base64.b64encode(PNG_1PX).decode()}]}
        )

    monkeypatch.setattr(avatars.httpx, "post", fake_post)
    assert env.client.post("/api/agents/recon/avatar").status_code == 200
    assert calls == ["grok-9-imaginary", avatars.DEFAULT_IMAGE_MODEL]


def test_image_error_carries_the_providers_words(env, two_agents, monkeypatch):
    env.secrets.set_many({"IMAGE_API_KEY": "img-1"})
    monkeypatch.setattr(
        avatars.httpx,
        "post",
        lambda *a, **k: FakeResponse(
            status_code=403, text='{"error":"credits exhausted, top up at console"}'
        ),
    )
    resp = env.client.post("/api/agents/recon/avatar")
    assert resp.status_code == 502
    assert "credits exhausted" in resp.json()["detail"]


# --- image settings -----------------------------------------------------------


def test_image_settings_roundtrip_key_never_echoed(env):
    body = env.client.get("/api/settings/image").json()
    assert body == {
        "key_set": False,
        "base_url": avatars.DEFAULT_IMAGE_BASE_URL,
        "model": avatars.DEFAULT_IMAGE_MODEL,
    }

    resp = env.client.put(
        "/api/settings/image",
        json={"key": "sk-image-secret", "base_url": "https://api.openai.com/v1/", "model": "gpt-image-1"},
    )
    body = resp.json()
    assert body["key_set"] is True
    assert body["base_url"] == "https://api.openai.com/v1"  # trailing slash trimmed
    assert body["model"] == "gpt-image-1"
    assert "sk-image-secret" not in resp.text

    # "" clears back to defaults.
    body = env.client.put(
        "/api/settings/image", json={"key": "", "base_url": "", "model": ""}
    ).json()
    assert body == {
        "key_set": False,
        "base_url": avatars.DEFAULT_IMAGE_BASE_URL,
        "model": avatars.DEFAULT_IMAGE_MODEL,
    }
