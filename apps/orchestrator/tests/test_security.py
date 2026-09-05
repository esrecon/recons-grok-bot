"""Operator authentication, sessions, CSRF, rate limiting, security headers and
SPA serving — the foundation a future public endpoint would stand on.

The rules under test:
  * nothing under /api except health, hooks and the auth endpoints answers
    without an operator session;
  * sessions are HttpOnly + Secure + SameSite=Strict signed cookies with a TTL;
  * every state-changing request needs the session's CSRF token and must not be
    cross-site;
  * login is rate limited and a missing operator credential locks the API
    rather than opening it;
  * a proxy/OIDC layer is trusted only with its shared secret and an
    allow-listed identity;
  * every response carries the security headers; the SPA is served with an
    index.html fallback and no path traversal.
"""

from __future__ import annotations

import time

import pytest

from recons_orchestrator.config import Settings
from recons_orchestrator.security import (
    RateLimiter,
    SessionManager,
    hash_password,
    verify_password,
)

from tests.auth import TEST_PASSWORD, TEST_USER, build_app, login, make_client, with_operator


# --- primitives ---------------------------------------------------------------
def test_password_hash_roundtrip():
    h = hash_password("s3cret", log_n=10)
    assert h.startswith("$scrypt$")
    assert "s3cret" not in h
    assert verify_password("s3cret", h)
    assert not verify_password("S3CRET", h)


@pytest.mark.parametrize("bad", ["", "plaintext", "$bcrypt$x$y", "$scrypt$ln=10$onlyone"])
def test_verify_rejects_malformed_hashes(bad):
    assert not verify_password("anything", bad)


def test_session_roundtrip_and_tamper():
    sm = SessionManager(secret="k", ttl_seconds=60)
    cookie, csrf = sm.issue("tony", via="password")
    sess = sm.load(cookie)
    assert sess is not None and sess.user == "tony" and sess.csrf == csrf
    assert sm.load(cookie[:-3] + "xyz") is None
    assert sm.load(None) is None


def test_session_expires():
    now = [1000.0]
    sm = SessionManager(secret="k", ttl_seconds=10, clock=lambda: now[0])
    cookie, _ = sm.issue("tony", via="password")
    now[0] += 11
    assert sm.load(cookie) is None


def test_rate_limiter_window():
    now = [0.0]
    rl = RateLimiter(limit=2, window_seconds=60, clock=lambda: now[0])
    assert rl.allow("ip") and rl.allow("ip")
    assert not rl.allow("ip")
    assert rl.retry_after("ip") > 0
    now[0] += 61
    assert rl.allow("ip")


# --- API contract -------------------------------------------------------------
@pytest.fixture
def client(settings):
    return make_client(build_app(settings))


def test_health_is_public(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_api_requires_operator_session(client):
    resp = client.get("/api/agents")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "authentication required"


def test_login_rejects_bad_password_without_cookie(client):
    resp = client.post("/api/auth/login", json={"username": TEST_USER, "password": "nope"})
    assert resp.status_code == 401
    assert "set-cookie" not in resp.headers
    assert client.get("/api/agents").status_code == 401


def test_login_sets_hardened_cookie_and_returns_csrf(client):
    resp = client.post(
        "/api/auth/login", json={"username": TEST_USER, "password": TEST_PASSWORD}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["authenticated"] is True
    assert body["operator"] == TEST_USER
    assert len(body["csrf_token"]) >= 32
    cookie = resp.headers["set-cookie"].lower()
    assert "recons_session=" in cookie
    assert "httponly" in cookie
    assert "secure" in cookie
    assert "samesite=strict" in cookie
    assert "path=/" in cookie
    # The password never comes back in any form.
    assert TEST_PASSWORD not in resp.text


def test_session_endpoint_reflects_state(client):
    before = client.get("/api/auth/session").json()
    assert before["authenticated"] is False
    assert before["mode"] == "password"
    assert before["configured"] is True
    login(client)
    after = client.get("/api/auth/session").json()
    assert after["authenticated"] is True
    assert after["operator"] == TEST_USER
    assert after["csrf_token"]


def test_mutation_requires_csrf_token(client):
    login(client)
    del client.headers["X-CSRF-Token"]
    resp = client.post("/api/agents", json={"name": "Recon", "role": "x"})
    assert resp.status_code == 403
    assert "csrf" in resp.json()["detail"].lower()
    resp = client.post(
        "/api/agents", json={"name": "Recon", "role": "x"}, headers={"X-CSRF-Token": "wrong"}
    )
    assert resp.status_code == 403


def test_mutation_with_csrf_token_succeeds(client):
    login(client)
    resp = client.post("/api/agents", json={"name": "Recon", "role": "x"})
    assert resp.status_code == 201


def test_cross_site_mutation_blocked_even_with_token(client):
    login(client)
    resp = client.post(
        "/api/agents", json={"name": "Recon", "role": "x"},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert resp.status_code == 403
    resp = client.post(
        "/api/agents", json={"name": "Recon", "role": "x"},
        headers={"Origin": "https://evil.example"},
    )
    assert resp.status_code == 403
    # Same-origin is fine.
    resp = client.post(
        "/api/agents", json={"name": "Recon", "role": "x"},
        headers={"Origin": "https://testserver", "Sec-Fetch-Site": "same-origin"},
    )
    assert resp.status_code == 201


def test_logout_clears_session(client):
    login(client)
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/agents").status_code == 401


def test_login_is_rate_limited(client):
    for _ in range(10):
        client.post("/api/auth/login", json={"username": TEST_USER, "password": "nope"})
    resp = client.post("/api/auth/login", json={"username": TEST_USER, "password": TEST_PASSWORD})
    assert resp.status_code == 429
    assert resp.headers.get("retry-after")


def test_login_requires_json_body(client):
    resp = client.post(
        "/api/auth/login",
        data={"username": TEST_USER, "password": TEST_PASSWORD},
    )
    assert resp.status_code in (403, 415, 422)
    assert client.get("/api/agents").status_code == 401


def test_api_locked_when_no_operator_configured(tmp_path):
    s = with_operator(Settings(root=tmp_path / "r", dashboard_dist=tmp_path / "d"))
    s.operator_password_hash = ""
    c = make_client(build_app(s))
    sess = c.get("/api/auth/session").json()
    assert sess["configured"] is False
    assert sess["authenticated"] is False
    resp = c.post("/api/auth/login", json={"username": TEST_USER, "password": TEST_PASSWORD})
    assert resp.status_code == 503
    assert c.get("/api/agents").status_code == 401


def test_session_cookie_rejected_after_secret_rotation(tmp_path):
    s = with_operator(Settings(root=tmp_path / "r", dashboard_dist=tmp_path / "d"))
    c = make_client(build_app(s))
    login(c)
    cookie = c.cookies.get("recons_session")
    s2 = with_operator(Settings(root=tmp_path / "r", dashboard_dist=tmp_path / "d"))
    s2.session_secret = "rotated"
    c2 = make_client(build_app(s2))
    c2.cookies.set("recons_session", cookie)
    assert c2.get("/api/agents").status_code == 401


def test_security_headers_on_every_response(client):
    for path in ("/api/health", "/api/agents"):
        h = client.get(path).headers
        assert h["x-content-type-options"] == "nosniff"
        assert h["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in h["content-security-policy"]
        assert "default-src 'self'" in h["content-security-policy"]
        assert h["referrer-policy"] == "same-origin"
        assert "camera=()" in h["permissions-policy"]
    assert client.get("/api/health").headers["cache-control"] == "no-store"
    # HSTS is opt-in (only meaningful behind TLS).
    assert "strict-transport-security" not in client.get("/api/health").headers


def test_hsts_opt_in(tmp_path):
    s = with_operator(Settings(root=tmp_path / "r", dashboard_dist=tmp_path / "d"))
    s.hsts = True
    c = make_client(build_app(s))
    assert "max-age=" in c.get("/api/health").headers["strict-transport-security"]


def test_general_api_rate_limit(tmp_path):
    s = with_operator(Settings(root=tmp_path / "r", dashboard_dist=tmp_path / "d"))
    s.api_rate_limit = 5
    c = make_client(build_app(s))
    login(c)
    codes = [c.get("/api/agents").status_code for _ in range(8)]
    assert 429 in codes
    # Health checks are never throttled.
    assert c.get("/api/health").status_code == 200


# --- proxy / OIDC mode --------------------------------------------------------
@pytest.fixture
def proxy_settings(tmp_path):
    s = Settings(root=tmp_path / "r", dashboard_dist=tmp_path / "d")
    s.auth_mode = "proxy"
    s.session_secret = "proxy-test-secret"
    s.proxy_secret = "shared-proxy-secret"
    s.operator_emails = ("tony@example.com",)
    return s


def test_proxy_mode_needs_secret_and_allowlisted_identity(proxy_settings):
    c = make_client(build_app(proxy_settings))
    ident = {"Cf-Access-Authenticated-User-Email": "tony@example.com"}
    # Identity alone is spoofable: refused.
    assert c.get("/api/agents", headers=ident).status_code == 401
    # Secret alone carries no identity: refused.
    assert c.get("/api/agents", headers={"X-Recons-Proxy-Secret": "shared-proxy-secret"}).status_code == 401
    # Wrong secret: refused.
    assert c.get("/api/agents", headers={**ident, "X-Recons-Proxy-Secret": "bad"}).status_code == 401
    # Unlisted identity: refused.
    assert c.get(
        "/api/agents",
        headers={"Cf-Access-Authenticated-User-Email": "mallory@example.com",
                 "X-Recons-Proxy-Secret": "shared-proxy-secret"},
    ).status_code == 401
    # Both, allow-listed: accepted.
    ok = {**ident, "X-Recons-Proxy-Secret": "shared-proxy-secret"}
    assert c.get("/api/agents", headers=ok).status_code == 200


def test_proxy_mode_issues_session_for_csrf(proxy_settings):
    c = make_client(build_app(proxy_settings))
    ok = {"Cf-Access-Authenticated-User-Email": "tony@example.com",
          "X-Recons-Proxy-Secret": "shared-proxy-secret"}
    sess = c.get("/api/auth/session", headers=ok)
    assert sess.json()["authenticated"] is True
    assert sess.json()["via"] == "proxy"
    csrf = sess.json()["csrf_token"]
    # Mutation without the token is refused even though the proxy vouches for us.
    assert c.post("/api/agents", json={"name": "R", "role": "x"}, headers=ok).status_code == 403
    assert c.post(
        "/api/agents", json={"name": "R", "role": "x"}, headers={**ok, "X-CSRF-Token": csrf}
    ).status_code == 201
    # Password login is not available in proxy mode.
    assert c.post("/api/auth/login", json={"username": "x", "password": "y"}).status_code == 404


def test_proxy_mode_unconfigured_is_locked(tmp_path):
    s = Settings(root=tmp_path / "r", dashboard_dist=tmp_path / "d")
    s.auth_mode = "proxy"
    s.session_secret = "k"
    s.proxy_secret = ""  # nothing to verify against
    c = make_client(build_app(s))
    hdr = {"Cf-Access-Authenticated-User-Email": "tony@example.com", "X-Recons-Proxy-Secret": ""}
    assert c.get("/api/agents", headers=hdr).status_code == 401
    assert c.get("/api/auth/session").json()["configured"] is False


# --- SPA serving --------------------------------------------------------------
def test_spa_served_with_fallback_and_no_traversal(settings):
    dist = settings.dashboard_dist
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Recons</title>")
    (dist / "assets" / "app-abc123.js").write_text("console.log(1)")
    (dist.parent / "outside.txt").write_text("nope")
    c = make_client(build_app(settings))

    assert "<title>Recons" in c.get("/").text
    # Deep links fall back to the shell so the SPA router can take over.
    assert "<title>Recons" in c.get("/settings").text
    js = c.get("/assets/app-abc123.js")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    assert "immutable" in js.headers["cache-control"]
    assert c.get("/index.html").headers["cache-control"] == "no-cache"
    # Unknown API paths are API 404s, never the shell.
    assert c.get("/api/definitely-not-a-route").status_code in (401, 404)
    assert "<title>" not in c.get("/api/definitely-not-a-route").text
    # Traversal never escapes dist.
    for p in ("/../outside.txt", "/assets/../../outside.txt", "/%2e%2e/outside.txt"):
        r = c.get(p)
        assert "nope" not in r.text
    # Security headers apply to the shell too.
    assert c.get("/").headers["x-frame-options"] == "DENY"


def test_spa_missing_build_is_a_clear_404(settings):
    c = make_client(build_app(settings))
    r = c.get("/")
    assert r.status_code == 404
    assert "not built" in r.json()["detail"]
