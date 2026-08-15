"""Signed-webhook receiver tests: HMAC verify, dedupe, and the ledger picking up
the live feed. Tampered deliveries must be rejected AND logged, never dropped."""

from __future__ import annotations

import hashlib
import hmac
import json

from recons_orchestrator.config import Settings
from recons_orchestrator.ledger import Ledger
from recons_orchestrator.webhooks import WebhookReceiver

SECRET = "shared-webhook-secret"


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def _receiver(tmp_path) -> WebhookReceiver:
    return WebhookReceiver(Settings(root=tmp_path / "recons", dashboard_dist=tmp_path / "d"), secret=SECRET)


def test_valid_signature_accepted_and_stored(tmp_path):
    rec = _receiver(tmp_path)
    body = json.dumps({"event": "post_tool_call", "agent": "scout", "ts": 1_786_000_100}).encode()
    status, resp = rec.handle(body, {"X-Hermes-Signature-256": _sign(body), "X-Hermes-Delivery": "d1"})
    assert status == 200 and resp["status"] == "accepted"
    stored = (tmp_path / "recons" / "audit" / "webhooks.jsonl").read_text()
    assert "post_tool_call" in stored


def test_bad_signature_rejected_and_logged(tmp_path):
    rec = _receiver(tmp_path)
    body = json.dumps({"event": "x", "agent": "scout"}).encode()
    status, resp = rec.handle(body, {"X-Hermes-Signature-256": "sha256=deadbeef", "X-Hermes-Delivery": "d2"})
    assert status == 401
    # Rejected, but recorded in the reject log — not silently dropped.
    rejects = (tmp_path / "recons" / "audit" / "webhook-rejects.jsonl").read_text()
    assert "bad_signature" in rejects
    assert not (tmp_path / "recons" / "audit" / "webhooks.jsonl").exists()


def test_missing_signature_rejected(tmp_path):
    rec = _receiver(tmp_path)
    body = b"{}"
    status, _ = rec.handle(body, {})
    assert status == 401


def test_duplicate_delivery_not_restored(tmp_path):
    rec = _receiver(tmp_path)
    body = json.dumps({"event": "on_session_end", "agent": "recon", "ts": 1}).encode()
    headers = {"X-Hermes-Signature-256": _sign(body), "X-Hermes-Delivery": "same"}
    rec.handle(body, headers)
    status, resp = rec.handle(body, headers)
    assert status == 200 and resp["status"] == "duplicate"
    lines = (tmp_path / "recons" / "audit" / "webhooks.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1  # stored once


def test_webhook_events_appear_in_ledger(tmp_path):
    root = tmp_path / "recons"
    (root).mkdir(parents=True)
    (root / "roster.json").write_text(json.dumps([
        {"id": "scout", "name": "Scout", "role": "x", "tier": "workhorse",
         "avatar_color": "#3b82f6", "status": "running", "is_lead": False,
         "created_at": "2026-08-15T12:00:00+00:00", "a2a_port": 9900}
    ]))
    rec = WebhookReceiver(Settings(root=root, dashboard_dist=tmp_path / "d"), secret=SECRET)
    body = json.dumps({"event": "pre_tool_call", "agent": "scout", "ts": 1_786_000_200}).encode()
    rec.handle(body, {"X-Hermes-Signature-256": _sign(body), "X-Hermes-Delivery": "d9"})

    led = Ledger(Settings(root=root, dashboard_dist=tmp_path / "d"))
    hooks = [e for e in led.collect() if e.source == "webhook"]
    assert len(hooks) == 1
    assert hooks[0].text == "pre_tool_call"
    assert hooks[0].agent_id == "scout"
