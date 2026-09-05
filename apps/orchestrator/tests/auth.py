"""Test helper: log a client in as the operator and arm it with the CSRF token.

Every API test that touches a protected endpoint goes through here so the
auth contract is exercised the same way the dashboard uses it: POST the login,
read the CSRF token off the session, and send it on every mutating request.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from recons_orchestrator.app import create_app, get_provisioner
from recons_orchestrator.config import Settings
from recons_orchestrator.provisioning import Provisioner
from recons_orchestrator.security import hash_password
from recons_orchestrator.services import RecordingServiceManager

TEST_USER = "tony"
TEST_PASSWORD = "correct horse battery staple"
# Low scrypt cost so the suite stays fast; the verifier reads the params back
# out of the hash so production strength is independent of this.
TEST_HASH = hash_password(TEST_PASSWORD, log_n=10)


def with_operator(settings: Settings) -> Settings:
    settings.auth_mode = "password"
    settings.operator_user = TEST_USER
    settings.operator_password_hash = TEST_HASH
    settings.session_secret = "unit-test-session-secret"
    settings.cookie_secure = True
    return settings


def build_app(settings: Settings):
    """create_app with systemd stubbed out so agent lifecycle calls are safe."""
    app = create_app(settings)
    app.state.services = RecordingServiceManager()
    app.dependency_overrides[get_provisioner] = lambda: Provisioner(
        settings, services=app.state.services
    )
    return app


def make_client(app) -> TestClient:
    # https so the Secure session cookie is stored and replayed by the jar.
    return TestClient(app, base_url="https://testserver")


def authed_client(app) -> TestClient:
    """make_client + login: for tests that build their own app."""
    c = make_client(app)
    login(c)
    return c


def login(client: TestClient, user: str = TEST_USER, password: str = TEST_PASSWORD) -> str:
    resp = client.post("/api/auth/login", json={"username": user, "password": password})
    assert resp.status_code == 200, resp.text
    csrf = resp.json()["csrf_token"]
    client.headers["X-CSRF-Token"] = csrf
    return csrf
