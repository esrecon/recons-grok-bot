"""The orchestrator must serve the dashboard, not just the API.

Without this the tailnet URL returns {"detail":"Not Found"} — the API answers
but there is no UI, which is what a user actually opens.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from recons_orchestrator.app import create_app
from recons_orchestrator.config import Settings

from tests.auth import authed_client, build_app, with_operator


@pytest.fixture
def dist(tmp_path):
    d = tmp_path / "dashboard"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<!doctype html><title>Recons</title>")
    (d / "assets" / "index-abc.js").write_text("console.log('app')")
    (d / "sw.js").write_text("// service worker")
    (d / "manifest.webmanifest").write_text('{"name":"Recons"}')
    return d


@pytest.fixture
def client(tmp_path, dist):
    settings = with_operator(Settings(root=tmp_path / "recons", dashboard_dist=dist))
    return authed_client(build_app(settings))


def test_root_serves_the_dashboard(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "<title>Recons</title>" in resp.text


def test_built_assets_are_served(client):
    assert client.get("/assets/index-abc.js").text == "console.log('app')"


def test_pwa_files_are_served_as_themselves_not_index(client):
    # A service worker that returns HTML silently breaks installation.
    assert client.get("/sw.js").text == "// service worker"
    assert client.get("/manifest.webmanifest").text == '{"name":"Recons"}'


def test_client_routes_fall_back_to_index(client):
    # Refreshing on a client-side route must not 404.
    assert "<title>Recons</title>" in client.get("/settings").text
    assert "<title>Recons</title>" in client.get("/agents/scout/chat").text


def test_api_routes_still_win(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/agents").status_code == 200


def test_unknown_api_path_404s_rather_than_serving_html(client):
    resp = client.get("/api/nope")
    assert resp.status_code == 404
    assert "<title>" not in resp.text


def test_path_traversal_is_refused(client, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("do not serve me")
    for attack in ("../secret.txt", "../../etc/passwd", "assets/../../secret.txt"):
        resp = client.get(f"/{attack}")
        assert "do not serve me" not in resp.text
        assert "root:" not in resp.text


def test_missing_build_explains_how_to_fix_it(tmp_path):
    settings = Settings(root=tmp_path / "recons", dashboard_dist=tmp_path / "nothing")
    resp = TestClient(create_app(settings)).get("/")
    assert resp.status_code == 503
    assert "npm run build" in resp.json()["detail"]
