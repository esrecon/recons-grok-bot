"""API-level tests for the audit + webhook endpoints."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from recons_orchestrator.config import Settings
from recons_orchestrator.webhooks import WebhookReceiver

from tests.auth import build_app, login, make_client, with_operator
from tests.fixtures import seed_two_agents

SECRET = "shared-webhook-secret"


@pytest.fixture
def client(tmp_path):
    root = tmp_path / "recons"
    seed_two_agents(root)
    settings = with_operator(Settings(root=root, dashboard_dist=tmp_path / "d"))
    app = build_app(settings)
    app.state.receiver = WebhookReceiver(settings, secret=SECRET)
    c = make_client(app)
    login(c)
    return c


def test_audit_returns_merged_events(client):
    body = client.get("/api/audit").json()
    assert body["count"] > 0
    sources = {e["source"] for e in body["events"]}
    assert {"session", "a2a", "cron"} <= sources


def test_audit_a2a_only_filter(client):
    body = client.get("/api/audit", params={"a2a_only": True}).json()
    assert body["events"] and all(e["source"] == "a2a" for e in body["events"])


def test_audit_agents_endpoint(client):
    assert set(client.get("/api/audit/agents").json()["agents"]) == {"recon", "scout"}


def test_audit_export_jsonl(client):
    resp = client.get("/api/audit/export.jsonl")
    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]
    first = resp.text.splitlines()[0]
    json.loads(first)  # each line is valid JSON


def test_webhook_endpoint_accepts_signed_event(client):
    payload = json.dumps({"event": "post_tool_call", "agent": "scout", "ts": 1_786_000_500}).encode()
    sig = "sha256=" + hmac.new(SECRET.encode(), payload, hashlib.sha256).hexdigest()
    resp = client.post(
        "/api/hooks",
        content=payload,
        headers={"X-Hermes-Signature-256": sig, "X-Hermes-Delivery": "abc"},
    )
    assert resp.status_code == 200
    # And it now shows up in the ledger via the API.
    events = client.get("/api/audit", params={"source": "webhook"}).json()["events"]
    assert any(e["text"] == "post_tool_call" for e in events)


def test_webhook_endpoint_rejects_bad_signature(client):
    resp = client.post(
        "/api/hooks",
        content=b"{}",
        headers={"X-Hermes-Signature-256": "sha256=nope", "X-Hermes-Delivery": "z"},
    )
    assert resp.status_code == 401
