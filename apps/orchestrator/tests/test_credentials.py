"""Credentials: the Settings surface can tell whether a key is configured and
let the operator set, replace or remove it — and nothing, anywhere, ever
returns the value.

Covers the env-file adapter (shared/secrets.env), the provider catalogue, the
audit event a change produces, and the API contract."""

from __future__ import annotations

import json
import os

import pytest

from recons_orchestrator.credentials import (
    PROVIDERS,
    CredentialError,
    EnvFileCredentialStore,
    provider_for_key,
)
from recons_orchestrator.operator_audit import OperatorAudit

from tests.auth import TEST_USER, build_app, login, make_client

SECRET_VALUE = "nous-live-key-9f8e7d6c5b4a-DO-NOT-LEAK"


# --- the env-file adapter -----------------------------------------------------
@pytest.fixture
def store(settings):
    return EnvFileCredentialStore(settings.shared_secrets_env, settings.shared_secrets_meta)


def test_set_creates_file_with_mode_600_and_meta(store, settings):
    assert store.status("NOUS_API_KEY").configured is False
    action = store.set("NOUS_API_KEY", SECRET_VALUE, actor="tony")
    assert action == "created"
    path = settings.shared_secrets_env
    assert path.exists()
    assert path.stat().st_mode & 0o777 == 0o600
    assert "NOUS_API_KEY=" + SECRET_VALUE in path.read_text()
    st = store.status("NOUS_API_KEY")
    assert st.configured is True
    assert st.updated_by == "tony"
    assert st.updated_at
    # The sidecar records who/when, never what.
    meta = json.loads(settings.shared_secrets_meta.read_text())
    assert meta["NOUS_API_KEY"]["updated_by"] == "tony"
    assert SECRET_VALUE not in settings.shared_secrets_meta.read_text()
    assert settings.shared_secrets_meta.stat().st_mode & 0o777 == 0o600


def test_replace_preserves_comments_and_order(store, settings):
    settings.shared_secrets_env.parent.mkdir(parents=True)
    settings.shared_secrets_env.write_text(
        "# Shared secrets\n"
        "CLAUDE_WRAPPER_BASE_URL=http://127.0.0.1:8600/v1\n"
        "\n"
        "# Nous\n"
        "NOUS_API_KEY=old-value\n"
        "RECONS_WEBHOOK_SECRET=\"quoted\"\n"
    )
    assert store.status("NOUS_API_KEY").configured is True
    assert store.status("RECONS_WEBHOOK_SECRET").configured is True
    assert store.set("NOUS_API_KEY", SECRET_VALUE, actor="tony") == "replaced"
    lines = settings.shared_secrets_env.read_text().splitlines()
    assert lines[0] == "# Shared secrets"
    assert lines[1] == "CLAUDE_WRAPPER_BASE_URL=http://127.0.0.1:8600/v1"
    assert lines[3] == "# Nous"
    assert lines[4] == "NOUS_API_KEY=" + SECRET_VALUE
    assert "old-value" not in settings.shared_secrets_env.read_text()


def test_empty_assignment_counts_as_not_configured(store, settings):
    settings.shared_secrets_env.parent.mkdir(parents=True)
    settings.shared_secrets_env.write_text("NOUS_API_KEY=\nANTHROPIC_API_KEY=   \n")
    assert store.status("NOUS_API_KEY").configured is False
    assert store.status("ANTHROPIC_API_KEY").configured is False


def test_remove_deletes_the_line(store, settings):
    store.set("NOUS_API_KEY", SECRET_VALUE, actor="tony")
    store.set("OPENAI_API_KEY", "other", actor="tony")
    assert store.remove("NOUS_API_KEY", actor="tony") is True
    text = settings.shared_secrets_env.read_text()
    assert "NOUS_API_KEY" not in text
    assert "OPENAI_API_KEY=other" in text
    assert store.status("NOUS_API_KEY").configured is False
    assert store.remove("NOUS_API_KEY", actor="tony") is False


@pytest.mark.parametrize("bad", ["", "   ", "has space", "new\nline", 'quo"te', "back\\slash",
                                 "hash#tag", "x" * 4097, "tab\tbed", "ünïcode"])
def test_values_are_validated_without_being_echoed(store, bad):
    with pytest.raises(CredentialError) as exc:
        store.set("NOUS_API_KEY", bad, actor="tony")
    assert bad.strip() not in str(exc.value) or not bad.strip()


def test_only_catalogue_keys_are_accepted(store):
    with pytest.raises(CredentialError):
        store.set("LD_PRELOAD", "/tmp/evil.so", actor="tony")
    with pytest.raises(CredentialError):
        store.set("nous_api_key", "lowercase", actor="tony")
    # Auth-related keys are managed on the server, never through the API.
    with pytest.raises(CredentialError):
        store.set("RECONS_OPERATOR_PASSWORD_HASH", "$scrypt$x", actor="tony")
    with pytest.raises(CredentialError):
        store.remove("RECONS_SESSION_SECRET", actor="tony")


def test_catalogue_is_consistent():
    keys = [k.key for p in PROVIDERS for k in p.keys]
    assert len(keys) == len(set(keys)), "duplicate key across providers"
    assert provider_for_key("NOUS_API_KEY").id == "nous"
    assert provider_for_key("NOPE") is None
    # Every key the example file ships is in the catalogue.
    example = (
        __import__("pathlib").Path(__file__).resolve().parents[3]
        / "config" / "shared-secrets.env.example"
    ).read_text()
    for line in example.splitlines():
        line = line.lstrip("# ").strip()
        if "=" in line and line.split("=")[0].isupper():
            assert line.split("=")[0] in keys, line


# --- operator audit -----------------------------------------------------------
def test_operator_audit_credential_event_has_only_allowed_fields(settings):
    audit = OperatorAudit(settings, clock=lambda: 1_786_000_000.0)
    audit.credential(actor="tony", key="NOUS_API_KEY", action="replaced", result="ok")
    rows = [json.loads(l) for l in (settings.root / "audit" / "operator.jsonl").read_text().splitlines()]
    assert rows == [{
        "ts": 1_786_000_000.0,
        "ts_iso": __import__("datetime").datetime.fromtimestamp(
            1_786_000_000, tz=__import__("datetime").timezone.utc).isoformat(),
        "actor": "tony",
        "category": "credential",
        "action": "replaced",
        "target": "NOUS_API_KEY",
        "provider": "nous",
        "result": "ok",
    }]


def test_operator_audit_events_surface_in_ledger(settings):
    from recons_orchestrator.ledger import Ledger

    (settings.root).mkdir(parents=True)
    (settings.root / "roster.json").write_text("[]")
    OperatorAudit(settings).credential(actor="tony", key="NOUS_API_KEY", action="created", result="ok")
    OperatorAudit(settings).record(actor="tony", category="auth", action="login", target="tony")
    rows = Ledger(settings).query(source="operator")
    assert [r["kind"] for r in rows] == ["credential", "auth"]
    assert rows[0]["agent_id"] == "orchestrator"
    assert rows[0]["text"] == "created NOUS_API_KEY"
    assert rows[0]["extra"]["actor"] == "tony"


# --- API contract -------------------------------------------------------------
class FakeProber:
    def __init__(self, result="ok"):
        self.result = result
        self.urls: list[str] = []

    def probe(self, url: str) -> str:
        self.urls.append(url)
        return self.result


@pytest.fixture
def client(settings):
    (settings.root).mkdir(parents=True, exist_ok=True)
    (settings.root / "roster.json").write_text("[]")
    app = build_app(settings)
    app.state.prober = FakeProber()
    c = make_client(app)
    login(c)
    c.app = app  # type: ignore[attr-defined]
    return c


def test_providers_listing_masks_everything(client, settings):
    settings.shared_secrets_env.parent.mkdir(parents=True, exist_ok=True)
    settings.shared_secrets_env.write_text(f"NOUS_API_KEY={SECRET_VALUE}\n")
    body = client.get("/api/settings/providers").json()
    ids = [p["id"] for p in body["providers"]]
    assert {"claude_wrapper", "anthropic", "openai", "nous", "telegram", "orchestrator"} <= set(ids)
    nous = next(p for p in body["providers"] if p["id"] == "nous")
    key = next(k for k in nous["keys"] if k["key"] == "NOUS_API_KEY")
    assert key["configured"] is True
    assert key["writable"] is True
    assert "value" not in key
    assert SECRET_VALUE not in json.dumps(body)
    assert SECRET_VALUE[:6] not in json.dumps(body)
    orch = next(p for p in body["providers"] if p["id"] == "orchestrator")
    hash_key = next(k for k in orch["keys"] if k["key"] == "RECONS_OPERATOR_PASSWORD_HASH")
    assert hash_key["writable"] is False
    assert hash_key["configured"] is True  # from the running process environment
    assert body["restart_required"] is False


def test_health_probe_for_local_wrapper(client, settings):
    settings.shared_secrets_env.parent.mkdir(parents=True, exist_ok=True)
    body = client.get("/api/settings/providers").json()
    wrapper = next(p for p in body["providers"] if p["id"] == "claude_wrapper")
    assert wrapper["health"] == "not_configured"
    settings.shared_secrets_env.write_text("CLAUDE_WRAPPER_BASE_URL=http://127.0.0.1:8600/v1\n")
    body = client.get("/api/settings/providers").json()
    wrapper = next(p for p in body["providers"] if p["id"] == "claude_wrapper")
    assert wrapper["health"] == "ok"
    assert client.app.state.prober.urls == ["http://127.0.0.1:8600/v1"]
    client.app.state.prober.result = "unreachable"
    body = client.get("/api/settings/providers").json()
    assert next(p for p in body["providers"] if p["id"] == "claude_wrapper")["health"] == "unreachable"
    # Remote providers are never probed with the key.
    assert next(p for p in body["providers"] if p["id"] == "nous")["health"] in ("not_configured", "unknown")


def test_set_replace_remove_via_api_with_audit(client, settings):
    r = client.put("/api/settings/credentials/NOUS_API_KEY", json={"value": SECRET_VALUE})
    assert r.status_code == 200, r.text
    assert r.json()["action"] == "created"
    assert r.json()["configured"] is True
    assert r.json()["restart_required"] is True
    assert SECRET_VALUE not in r.text

    r = client.put("/api/settings/credentials/NOUS_API_KEY", json={"value": SECRET_VALUE + "-2"})
    assert r.json()["action"] == "replaced"

    r = client.delete("/api/settings/credentials/NOUS_API_KEY")
    assert r.status_code == 200
    assert r.json()["action"] == "removed"
    assert r.json()["configured"] is False
    assert "NOUS_API_KEY" not in settings.shared_secrets_env.read_text()

    events = client.get("/api/audit", params={"source": "operator", "kind": "credential"}).json()["events"]
    assert [e["text"] for e in events] == [
        "created NOUS_API_KEY", "replaced NOUS_API_KEY", "removed NOUS_API_KEY",
    ]
    assert all(e["extra"]["actor"] == TEST_USER for e in events)
    assert all(e["extra"]["provider"] == "nous" for e in events)


def test_secret_value_never_appears_anywhere(client, settings):
    client.put("/api/settings/credentials/NOUS_API_KEY", json={"value": SECRET_VALUE})
    surfaces = [
        client.get("/api/settings/providers").text,
        client.get("/api/audit").text,
        client.get("/api/audit/export.jsonl").text,
        client.get("/api/settings/security").text,
        (settings.root / "audit" / "operator.jsonl").read_text(),
        settings.shared_secrets_meta.read_text(),
    ]
    for text in surfaces:
        assert SECRET_VALUE not in text
    # There is no reveal endpoint.
    assert client.get("/api/settings/credentials/NOUS_API_KEY").status_code in (404, 405)
    # The only place the value lives is the 600 secrets file.
    assert SECRET_VALUE in settings.shared_secrets_env.read_text()


def test_api_rejects_bad_keys_and_values_without_echo(client):
    assert client.put("/api/settings/credentials/LD_PRELOAD", json={"value": "x"}).status_code == 404
    r = client.put("/api/settings/credentials/RECONS_OPERATOR_PASSWORD_HASH", json={"value": "x"})
    assert r.status_code == 403
    bad = "has a space and-a-marker-XYZ"
    r = client.put("/api/settings/credentials/NOUS_API_KEY", json={"value": bad})
    assert r.status_code == 422
    assert "XYZ" not in r.text
    r = client.put("/api/settings/credentials/NOUS_API_KEY", json={"value": ""})
    assert r.status_code == 422
    r = client.put("/api/settings/credentials/NOUS_API_KEY", json={"nope": 1})
    assert r.status_code == 422
    assert client.delete("/api/settings/credentials/NOUS_API_KEY").status_code == 404


def test_failed_credential_write_is_audited_as_error(client, settings):
    settings.shared_secrets_env.parent.mkdir(parents=True, exist_ok=True)
    settings.shared_secrets_env.write_text("NOUS_API_KEY=old\n")
    os.chmod(settings.shared_secrets_env.parent, 0o500)
    try:
        r = client.put("/api/settings/credentials/NOUS_API_KEY", json={"value": SECRET_VALUE})
    finally:
        os.chmod(settings.shared_secrets_env.parent, 0o700)
    if r.status_code == 200:  # running as root: the write cannot be made to fail
        pytest.skip("filesystem permissions not enforced for this user")
    assert r.status_code == 500
    assert SECRET_VALUE not in r.text
    rows = [json.loads(l) for l in (settings.root / "audit" / "operator.jsonl").read_text().splitlines()]
    assert rows[-1]["result"] == "error"
    assert rows[-1]["target"] == "NOUS_API_KEY"
    assert SECRET_VALUE not in json.dumps(rows)


def test_security_settings_endpoint_describes_posture_without_secrets(client, settings):
    body = client.get("/api/settings/security").json()
    assert body["mode"] == "password"
    assert body["operator"] == TEST_USER
    assert body["cookie_secure"] is True
    assert body["hsts"] is False
    assert body["session_ttl_seconds"] == settings.session_ttl_seconds
    assert body["csrf_protection"] is True
    assert body["rate_limits"]["login_per_minute"] == settings.login_rate_limit
    assert settings.session_secret not in json.dumps(body)
    assert settings.operator_password_hash not in json.dumps(body)


def test_auth_events_are_audited(client, settings):
    client.post("/api/auth/login", json={"username": TEST_USER, "password": "wrong"})
    client.post("/api/auth/logout")
    rows = [json.loads(l) for l in (settings.root / "audit" / "operator.jsonl").read_text().splitlines()]
    actions = [(r["category"], r["action"], r["result"]) for r in rows]
    assert ("auth", "login", "ok") in actions
    assert ("auth", "login", "denied") in actions
    assert ("auth", "logout", "ok") in actions
    assert "wrong" not in json.dumps(rows)
