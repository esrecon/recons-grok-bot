"""Operator authentication, sessions, CSRF, rate limiting and security headers.

This is the gate in front of the whole API. It is deliberately small and
dependency-light (scrypt from the standard library, `itsdangerous` for the
signed cookie) so it is easy to audit.

Two ways an operator can be identified:

* **password** — `RECONS_OPERATOR_USER` + `RECONS_OPERATOR_PASSWORD_HASH`. The
  hash is produced on the server by `python -m recons_orchestrator.security
  hash-password`; the plaintext is never stored anywhere.
* **proxy** — a reverse-proxy/OIDC layer (Cloudflare Access or similar) in
  front of the loopback port. Its identity header is trusted only when the
  request also carries a shared secret header the proxy injects, and only for
  allow-listed identities. Nothing about the proxy is configured here.

In both modes the browser gets a signed, HttpOnly, Secure, SameSite=Strict
session cookie that carries a CSRF token; every state-changing request must
echo that token in `X-CSRF-Token`, and cross-site requests are refused
outright. If no operator credential is configured the API is locked, not open.

Loopback binding is untouched: this module never listens on anything.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import urlsplit

from itsdangerous import BadSignature, URLSafeSerializer
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse

from .config import Settings

log = logging.getLogger("recons_orchestrator.security")

SESSION_COOKIE = "recons_session"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Reachable without an operator session. Health is the only "open" endpoint;
# hooks are HMAC-verified by the receiver; the two auth endpoints exist to
# establish a session in the first place.
PUBLIC_PATHS = frozenset({"/api/health", "/api/hooks", "/api/auth/login", "/api/auth/session"})
# State-changing requests that cannot carry a CSRF token by construction.
CSRF_EXEMPT = frozenset({"/api/auth/login", "/api/hooks"})

SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("x-content-type-options", "nosniff"),
    ("x-frame-options", "DENY"),
    ("referrer-policy", "same-origin"),
    ("permissions-policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()"),
    ("cross-origin-opener-policy", "same-origin"),
    ("cross-origin-resource-policy", "same-origin"),
    (
        "content-security-policy",
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; "
        "manifest-src 'self'; worker-src 'self'; frame-ancestors 'none'; "
        "base-uri 'self'; form-action 'self'; object-src 'none'",
    ),
)
HSTS_VALUE = "max-age=31536000; includeSubDomains"


# --- password hashing ---------------------------------------------------------
# PHC-style string: $scrypt$ln=<log2 N>,r=<r>,p=<p>$<salt>$<hash>, base64url
# without padding. Parameters travel with the hash so they can be raised later
# without invalidating existing credentials.
_MAX_LOG_N = 20


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _scrypt(password: str, salt: bytes, log_n: int, r: int, p: int) -> bytes:
    n = 1 << log_n
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32,
        maxmem=2 * 128 * n * r * p + (1 << 20),
    )


def hash_password(password: str, *, log_n: int = 15, r: int = 8, p: int = 1) -> str:
    if not password:
        raise ValueError("password must not be empty")
    if not 10 <= log_n <= _MAX_LOG_N:
        raise ValueError("log_n out of range")
    salt = secrets.token_bytes(16)
    digest = _scrypt(password, salt, log_n, r, p)
    return f"$scrypt$ln={log_n},r={r},p={p}${_b64e(salt)}${_b64e(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time verification of `password` against a hash_password string.
    Any malformed hash verifies false — never raises."""
    try:
        _, algo, params, salt_b64, hash_b64 = encoded.split("$")
        if algo != "scrypt":
            return False
        kv = dict(item.split("=", 1) for item in params.split(","))
        log_n, r, p = int(kv["ln"]), int(kv["r"]), int(kv["p"])
        if not 1 <= log_n <= _MAX_LOG_N or not 1 <= r <= 32 or not 1 <= p <= 16:
            return False
        salt, expected = _b64d(salt_b64), _b64d(hash_b64)
    except (ValueError, KeyError, TypeError):
        return False
    if not salt or not expected:
        return False
    actual = _scrypt(password, salt, log_n, r, p)
    return hmac.compare_digest(actual, expected)


# --- sessions -----------------------------------------------------------------
@dataclass(frozen=True)
class Session:
    user: str
    via: str  # "password" | "proxy"
    csrf: str
    issued_at: float


class SessionManager:
    """Signed, stateless session cookie with an absolute TTL.

    Stateless keeps the orchestrator restart-safe and avoids a session store;
    rotating `RECONS_SESSION_SECRET` (or restarting with an ephemeral one)
    invalidates every session at once, which is the revocation story.
    """

    def __init__(
        self,
        secret: str,
        ttl_seconds: int,
        *,
        cookie_secure: bool = True,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not secret:
            raise ValueError("session secret must not be empty")
        self._ser = URLSafeSerializer(secret, salt="recons-operator-session")
        self.ttl = int(ttl_seconds)
        self.cookie_secure = cookie_secure
        self._clock = clock

    def issue(self, user: str, via: str) -> tuple[str, str]:
        csrf = secrets.token_urlsafe(32)
        payload = {"u": user, "v": via, "c": csrf, "iat": self._clock()}
        return self._ser.dumps(payload), csrf

    def load(self, cookie_value: str | None) -> Session | None:
        if not cookie_value:
            return None
        try:
            data = self._ser.loads(cookie_value)
        except BadSignature:
            return None
        if not isinstance(data, dict):
            return None
        try:
            issued = float(data["iat"])
            user, via, csrf = str(data["u"]), str(data["v"]), str(data["c"])
        except (KeyError, TypeError, ValueError):
            return None
        if not user or not csrf or self._clock() - issued > self.ttl:
            return None
        return Session(user=user, via=via, csrf=csrf, issued_at=issued)

    def set_cookie(self, response, value: str) -> None:
        response.set_cookie(
            SESSION_COOKIE, value, max_age=self.ttl, path="/", secure=self.cookie_secure,
            httponly=True, samesite="strict",
        )

    def clear_cookie(self, response) -> None:
        response.delete_cookie(
            SESSION_COOKIE, path="/", secure=self.cookie_secure, httponly=True, samesite="strict",
        )


# --- rate limiting ------------------------------------------------------------
class RateLimiter:
    """In-memory sliding window: at most `limit` events per key per window."""

    def __init__(
        self, limit: int, window_seconds: float, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit = max(1, int(limit))
        self.window = float(window_seconds)
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}

    def _prune(self, key: str, now: float) -> deque[float]:
        q = self._hits.setdefault(key, deque())
        cutoff = now - self.window
        while q and q[0] <= cutoff:
            q.popleft()
        if len(self._hits) > 10_000:  # keep memory bounded under churn
            for k in [k for k, v in self._hits.items() if not v]:
                del self._hits[k]
        return q

    def allow(self, key: str) -> bool:
        now = self._clock()
        q = self._prune(key, now)
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True

    def retry_after(self, key: str) -> int:
        now = self._clock()
        q = self._prune(key, now)
        if not q or len(q) < self.limit:
            return 0
        return max(1, int(q[0] + self.window - now) + 1)


# --- operator identity --------------------------------------------------------
@dataclass(frozen=True)
class Operator:
    name: str
    via: str


class OperatorAuth:
    """Decides who the operator is, in whichever mode is configured."""

    def __init__(self, settings: Settings) -> None:
        self._s = settings

    @property
    def mode(self) -> str:
        return self._s.auth_mode

    @property
    def reason(self) -> str | None:
        """Why the API is locked, or None when an operator can sign in."""
        s = self._s
        if s.auth_mode == "password":
            if not s.operator_user or not s.operator_password_hash:
                return ("operator login is not configured: set RECONS_OPERATOR_USER and "
                        "RECONS_OPERATOR_PASSWORD_HASH (python -m recons_orchestrator.security hash-password)")
            if not s.operator_password_hash.startswith("$scrypt$"):
                return "RECONS_OPERATOR_PASSWORD_HASH is not a hash-password string"
            return None
        if s.auth_mode == "proxy":
            if not s.proxy_secret or not s.operator_emails:
                return ("proxy auth is not configured: set RECONS_PROXY_SECRET and "
                        "RECONS_OPERATOR_EMAILS")
            return None
        return f"unknown RECONS_AUTH_MODE '{s.auth_mode}' (use password or proxy)"

    @property
    def configured(self) -> bool:
        return self.reason is None

    def check_password(self, username: str, password: str) -> Operator | None:
        if self.mode != "password" or not self.configured:
            return None
        s = self._s
        # Always run the hash so a wrong username costs the same as a wrong password.
        ok = verify_password(password, s.operator_password_hash)
        if ok and hmac.compare_digest(username.encode(), s.operator_user.encode()):
            return Operator(name=s.operator_user, via="password")
        return None

    def check_proxy(self, headers: Mapping[str, str]) -> Operator | None:
        if self.mode != "proxy" or not self.configured:
            return None
        s = self._s
        presented = headers.get(s.proxy_secret_header, "")
        if not presented or not hmac.compare_digest(presented.encode(), s.proxy_secret.encode()):
            return None
        identity = headers.get(s.proxy_identity_header, "").strip().lower()
        if not identity:
            return None
        for allowed in s.operator_emails:
            if hmac.compare_digest(identity.encode(), allowed.strip().lower().encode()):
                return Operator(name=identity, via="proxy")
        return None


# --- everything the app needs, in one place -----------------------------------
class SecurityContext:
    def __init__(self, settings: Settings, *, clock: Callable[[], float] = time.time) -> None:
        self.settings = settings
        self.auth = OperatorAuth(settings)
        secret = settings.session_secret
        if not secret:
            secret = secrets.token_urlsafe(48)
            log.warning(
                "RECONS_SESSION_SECRET is not set; using an ephemeral secret "
                "(operator sessions will not survive a restart)"
            )
        self.sessions = SessionManager(
            secret, settings.session_ttl_seconds, cookie_secure=settings.cookie_secure, clock=clock
        )
        w = settings.rate_window_seconds
        self.login_limiter = RateLimiter(settings.login_rate_limit, w)
        self.login_limiter_global = RateLimiter(settings.login_rate_limit * 10, w)
        self.api_limiter = RateLimiter(settings.api_rate_limit, w)


class SecurityMiddleware:
    """Pure ASGI middleware (no body buffering, so SSE streams pass through).

    Order of checks for /api requests: general rate limit → identify the
    operator → login rate limit → authentication → same-origin + CSRF for
    state-changing methods. Security headers go on every response.
    """

    def __init__(self, app, *, security: SecurityContext) -> None:
        self.app = app
        self.sec = security

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        is_api = path == "/api" or path.startswith("/api/")
        send = self._wrap_send(send, is_api)
        if not is_api:
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        method = scope.get("method", "GET").upper()
        sec = self.sec
        operator, session = self._identify(request)
        scope.setdefault("state", {})
        scope["state"]["operator"] = operator
        scope["state"]["session"] = session

        client_key = self._client_key(request, session)
        if path != "/api/health" and not sec.api_limiter.allow(client_key):
            await self._reject(scope, receive, send, 429, "too many requests",
                               retry_after=sec.api_limiter.retry_after(client_key))
            return

        if path == "/api/auth/login" and method == "POST":
            if not sec.login_limiter.allow(client_key) or not sec.login_limiter_global.allow("*"):
                await self._reject(scope, receive, send, 429, "too many login attempts",
                                   retry_after=max(sec.login_limiter.retry_after(client_key), 1))
                return

        if path not in PUBLIC_PATHS and operator is None:
            await self._reject(scope, receive, send, 401, "authentication required")
            return

        if method not in SAFE_METHODS and path not in CSRF_EXEMPT:
            if not self._same_origin(request):
                await self._reject(scope, receive, send, 403, "cross-site request blocked")
                return
            token = request.headers.get(CSRF_HEADER, "")
            if session is None or not token or not hmac.compare_digest(token, session.csrf):
                await self._reject(scope, receive, send, 403, "csrf token missing or invalid")
                return

        await self.app(scope, receive, send)

    # -- helpers ---------------------------------------------------------------
    def _identify(self, request: Request) -> tuple[Operator | None, Session | None]:
        sec = self.sec
        session = sec.sessions.load(request.cookies.get(SESSION_COOKIE))
        if sec.auth.mode == "proxy":
            # The proxy must vouch on every request; the cookie only binds CSRF.
            operator = sec.auth.check_proxy(request.headers)
            if operator is None:
                return None, None
            if session is not None and session.user != operator.name:
                session = None
            return operator, session
        if session is not None:
            return Operator(name=session.user, via=session.via), session
        return None, None

    def _client_key(self, request: Request, session: Session | None) -> str:
        if session is not None:
            return "s:" + session.csrf[:16]
        hdr = self.sec.settings.client_ip_header
        if hdr:
            forwarded = request.headers.get(hdr, "").split(",")[0].strip()
            if forwarded:
                return "ip:" + forwarded
        client = request.client
        return "ip:" + (client.host if client else "unknown")

    def _same_origin(self, request: Request) -> bool:
        h = request.headers
        site = h.get("sec-fetch-site", "").lower()
        if site and site not in ("same-origin", "none"):
            return False
        origin = h.get("origin")
        if not origin:
            return True
        origin = origin.strip().rstrip("/")
        allowed = self.sec.settings.allowed_origins
        if allowed:
            return origin.lower() in {a.rstrip("/").lower() for a in allowed}
        host = (h.get("x-forwarded-host") or h.get("host") or "").split(",")[0].strip().lower()
        return bool(host) and urlsplit(origin).netloc.lower() == host

    async def _reject(self, scope, receive, send, status: int, detail: str, *, retry_after: int = 0):
        headers = {"retry-after": str(retry_after)} if retry_after else None
        response = JSONResponse({"detail": detail}, status_code=status, headers=headers)
        await response(scope, receive, send)

    def _wrap_send(self, send, is_api: bool):
        hsts = self.sec.settings.hsts

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS:
                    headers[name] = value
                if hsts:
                    headers["strict-transport-security"] = HSTS_VALUE
                if is_api and "cache-control" not in headers:
                    # Default API responses to no-store; a route that sets its
                    # own policy (versioned, immutable assets) keeps it.
                    headers["cache-control"] = "no-store"
            await send(message)

        return send_with_headers


# --- CLI: python -m recons_orchestrator.security ------------------------------
def _read_password(argv: list[str]) -> str | None:
    """Prompt twice, or read one line from stdin with --password-stdin."""
    import getpass
    import sys

    if "--password-stdin" in argv:
        pw = sys.stdin.readline().rstrip("\r\n")
    else:
        pw = getpass.getpass("Dashboard operator password: ")
        if pw != getpass.getpass("Repeat: "):
            print("passwords do not match", file=sys.stderr)
            return None
    if len(pw) < 12:
        print("use at least 12 characters", file=sys.stderr)
        return None
    return pw


def set_operator(user: str, password: str) -> Path:
    """Write the operator login into the shared secrets file (never the
    plaintext). Used by vps-quickstart.sh; the orchestrator picks it up on
    its next restart because the unit loads that file as its environment."""
    from .config import Settings
    from .secrets_store import SecretsStore

    if not user or not user.strip():
        raise ValueError("operator user must not be empty")
    settings = Settings()
    store = SecretsStore(settings.shared_secrets_env)
    store.ensure_file()
    store.set_many({
        "RECONS_OPERATOR_USER": user.strip(),
        "RECONS_OPERATOR_PASSWORD_HASH": hash_password(password),
    })
    return settings.shared_secrets_env


def _cli(argv: list[str]) -> int:
    import getpass
    import sys

    cmd = argv[0] if argv else ""
    if cmd == "set-operator":
        user = ""
        if "--user" in argv:
            i = argv.index("--user")
            user = argv[i + 1] if i + 1 < len(argv) else ""
        user = user or os.environ.get("RECONS_OPERATOR_USER", "") or "operator"
        pw = _read_password(argv)
        if pw is None:
            return 1
        try:
            path = set_operator(user, pw)
        except (OSError, ValueError) as exc:
            print(f"could not write the operator login: {exc}", file=sys.stderr)
            return 1
        print(f"operator '{user}' set in {path}; restart the orchestrator to apply")
        return 0
    if cmd == "hash-password":
        pw = getpass.getpass("New operator password: ")
        again = getpass.getpass("Repeat: ")
        if pw != again:
            print("passwords do not match", file=sys.stderr)
            return 1
        if len(pw) < 12:
            print("use at least 12 characters", file=sys.stderr)
            return 1
        print(f"RECONS_OPERATOR_PASSWORD_HASH={hash_password(pw)}")
        return 0
    if cmd == "session-secret":
        print(f"RECONS_SESSION_SECRET={secrets.token_urlsafe(48)}")
        return 0
    if cmd == "proxy-secret":
        print(f"RECONS_PROXY_SECRET={secrets.token_urlsafe(32)}")
        return 0
    print("usage: python -m recons_orchestrator.security "
          "{set-operator [--user NAME] [--password-stdin]|hash-password|session-secret|proxy-secret}",
          file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(_cli(sys.argv[1:]))
