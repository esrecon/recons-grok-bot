"""Signed-webhook receiver — the live feed into the audit ledger.

Hermes POSTs an HMAC-SHA256-signed JSON body on every lifecycle event
(GitHub-style `X-Hermes-Signature-256: sha256=<hex>` over the raw body). We
verify the signature against the shared secret, dedupe on the delivery id, and
append accepted events to `<root>/audit/webhooks.jsonl` (the ledger's "webhook"
source). Rejected deliveries are logged to `<root>/audit/webhook-rejects.jsonl`
— never silently dropped — so tampering is visible in the audit trail itself.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from pathlib import Path

from .config import Settings

SIG_HEADER = "x-hermes-signature-256"  # VERIFY exact header name against Hermes
DELIVERY_HEADER = "x-hermes-delivery"  # VERIFY


class WebhookReceiver:
    def __init__(self, settings: Settings, secret: str | None = None) -> None:
        self._s = settings
        # Secret comes from the shared env (RECONS_WEBHOOK_SECRET). Injected for
        # tests; falls back to the environment in production.
        self._secret = (secret if secret is not None
                        else os.environ.get(settings.webhook_secret_env, "")).encode()
        self._seen: set[str] = set()

    @property
    def _audit_dir(self) -> Path:
        return self._s.root / "audit"

    @property
    def _store(self) -> Path:
        return self._audit_dir / "webhooks.jsonl"

    @property
    def _rejects(self) -> Path:
        return self._audit_dir / "webhook-rejects.jsonl"

    def _expected_sig(self, body: bytes) -> str:
        return "sha256=" + hmac.new(self._secret, body, hashlib.sha256).hexdigest()

    def verify(self, body: bytes, signature: str | None) -> bool:
        if not self._secret or not signature:
            return False
        return hmac.compare_digest(self._expected_sig(body), signature.strip())

    def _append(self, path: Path, obj: dict) -> None:
        self._audit_dir.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj) + "\n")

    def handle(self, body: bytes, headers: dict[str, str]) -> tuple[int, dict]:
        """Process one delivery. Returns (http_status, response_json)."""
        norm = {k.lower(): v for k, v in headers.items()}
        signature = norm.get(SIG_HEADER)
        delivery = norm.get(DELIVERY_HEADER)

        if not self.verify(body, signature):
            self._append(self._rejects, {
                "ts": _now(), "reason": "bad_signature",
                "delivery": delivery,
                "body_preview": body[:500].decode("utf-8", "replace"),
            })
            return 401, {"detail": "invalid signature"}

        # Idempotent: a repeated delivery id is accepted but not re-stored.
        if delivery and delivery in self._seen:
            return 200, {"status": "duplicate"}
        if delivery:
            self._seen.add(delivery)

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self._append(self._rejects, {"ts": _now(), "reason": "bad_json", "delivery": delivery})
            return 400, {"detail": "invalid json"}

        if "ts" not in payload:
            payload["ts"] = _now()
        payload.setdefault("_delivery", delivery)
        self._append(self._store, payload)
        return 200, {"status": "accepted"}


def _now() -> float:
    return time.time()
