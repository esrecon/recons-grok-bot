"""Credentials behind the Settings surface.

The dashboard can see *whether* a key is configured, and set, replace or
remove it. It can never read one back: there is no reveal, no prefix, no
length, and the value is not echoed in errors or audit rows.

`CredentialStore` is the small interface a proper secret manager could
implement later. `EnvFileCredentialStore` is the initial adapter: it edits
`shared/secrets.env` in place (the file the systemd units already load), keeps
comments and order, writes atomically with mode 600, and keeps a sidecar
`secrets.meta.json` with *when/by whom* per key.

Only keys in the provider catalogue below can be touched, and the keys that
protect the operator login itself are visible but never writable through the
API — they are managed on the server (docs/10).
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol


class CredentialError(Exception):
    """Raised with an HTTP-ish status; the message never contains a value."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


# --- catalogue ----------------------------------------------------------------
@dataclass(frozen=True)
class CredentialKey:
    key: str
    label: str
    secret: bool = True      # a secret (vs. a plain setting) — both stay write-only
    writable: bool = True    # False: managed on the server, never via the API
    required: bool = False
    hint: str = ""


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    name: str
    description: str
    keys: tuple[CredentialKey, ...]
    # Env key whose value is a *local* base URL we may probe for liveness.
    probe_url_key: str | None = None
    # Keys that must all be configured for the provider to count as configured.
    health_keys: tuple[str, ...] = ()


PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        id="claude_wrapper",
        name="Claude (wrapper)",
        description="Lead tier. claude-code-openai-wrapper on this VPS, against your Claude login.",
        keys=(
            CredentialKey("CLAUDE_WRAPPER_BASE_URL", "Wrapper base URL", secret=False,
                          hint="e.g. http://127.0.0.1:8600/v1"),
            CredentialKey("CLAUDE_WRAPPER_API_KEY", "Wrapper API key",
                          hint="Anything non-empty for a local wrapper"),
        ),
        probe_url_key="CLAUDE_WRAPPER_BASE_URL",
        health_keys=("CLAUDE_WRAPPER_BASE_URL",),
    ),
    ProviderSpec(
        id="anthropic",
        name="Anthropic API",
        description="Optional pay-as-you-go fallback for the lead tier.",
        keys=(CredentialKey("ANTHROPIC_API_KEY", "API key"),),
        health_keys=("ANTHROPIC_API_KEY",),
    ),
    ProviderSpec(
        id="openai",
        name="OpenAI API",
        description="Workhorse tier. Prefer the ChatGPT subscription login (hermes model); "
                    "set a key only for pay-as-you-go.",
        keys=(CredentialKey("OPENAI_API_KEY", "API key"),),
        health_keys=("OPENAI_API_KEY",),
    ),
    ProviderSpec(
        id="nous",
        name="Nous Portal",
        description="Bulk tier.",
        keys=(CredentialKey("NOUS_API_KEY", "API key", required=True),),
        health_keys=("NOUS_API_KEY",),
    ),
    ProviderSpec(
        id="telegram",
        name="Telegram gateway",
        description="Optional messaging fallback (docs/30). Allow-list your own user id.",
        keys=(
            CredentialKey("TELEGRAM_BOT_TOKEN", "Bot token"),
            CredentialKey("TELEGRAM_ALLOWED_USERS", "Allowed user ids", secret=False,
                          hint="Comma-separated Telegram user ids"),
        ),
        health_keys=("TELEGRAM_BOT_TOKEN",),
    ),
    ProviderSpec(
        id="orchestrator",
        name="Orchestrator",
        description="Audit feed signing and the operator login.",
        keys=(
            CredentialKey("RECONS_WEBHOOK_SECRET", "Webhook signing secret", required=True,
                          hint="openssl rand -hex 32; agents re-read it on restart"),
            CredentialKey("RECONS_SESSION_SECRET", "Session signing secret", writable=False,
                          hint="Managed on the server (python -m recons_orchestrator.security session-secret)"),
            CredentialKey("RECONS_OPERATOR_USER", "Operator username", secret=False, writable=False,
                          hint="Managed on the server"),
            CredentialKey("RECONS_OPERATOR_PASSWORD_HASH", "Operator password hash", writable=False,
                          hint="Managed on the server (python -m recons_orchestrator.security hash-password)"),
            CredentialKey("RECONS_PROXY_SECRET", "Reverse-proxy shared secret", writable=False,
                          hint="Only for proxy auth mode (docs/65)"),
            CredentialKey("RECONS_OPERATOR_EMAILS", "Proxy operator allow-list", secret=False,
                          writable=False, hint="Only for proxy auth mode (docs/65)"),
        ),
        health_keys=("RECONS_WEBHOOK_SECRET",),
    ),
)

_KEY_INDEX: dict[str, tuple[ProviderSpec, CredentialKey]] = {
    k.key: (p, k) for p in PROVIDERS for k in p.keys
}


def provider_for_key(key: str) -> ProviderSpec | None:
    hit = _KEY_INDEX.get(key)
    return hit[0] if hit else None


def spec_for_key(key: str) -> CredentialKey | None:
    hit = _KEY_INDEX.get(key)
    return hit[1] if hit else None


# --- value rules ----------------------------------------------------------------
# Printable ASCII, no whitespace, and none of the characters that would need
# quoting/escaping in an env file (so what we write is exactly what every
# reader — systemd, python-dotenv, a shell — sees).
_VALUE_RE = re.compile(r"^[\x21-\x7e]{1,4096}$")
_FORBIDDEN = set('"\'`\\#')


def validate_value(value: str) -> str:
    if not isinstance(value, str):
        raise CredentialError(422, "value must be a string")
    value = value.strip()
    if not value:
        raise CredentialError(422, "value must not be empty")
    if len(value) > 4096:
        raise CredentialError(422, "value is too long (max 4096 characters)")
    if not _VALUE_RE.fullmatch(value) or any(ch in _FORBIDDEN for ch in value):
        raise CredentialError(
            422, "value contains unsupported characters (printable ASCII only; no spaces, "
                 "quotes, backslashes or #)",
        )
    return value


def _check_key(key: str, *, for_write: bool) -> CredentialKey:
    spec = spec_for_key(key)
    if spec is None:
        raise CredentialError(404, "unknown credential key")
    if for_write and not spec.writable:
        raise CredentialError(403, f"{key} is managed on the server, not through the dashboard")
    return spec


# --- the interface --------------------------------------------------------------
@dataclass(frozen=True)
class CredentialStatus:
    key: str
    configured: bool
    updated_at: str | None = None
    updated_by: str | None = None


class CredentialStore(Protocol):
    def status(self, key: str) -> CredentialStatus: ...
    def set(self, key: str, value: str, *, actor: str) -> str: ...
    def remove(self, key: str, *, actor: str) -> bool: ...
    def read(self, key: str) -> str | None: ...  # server-side use only (probes)


# --- env-file adapter -----------------------------------------------------------
_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=(.*)$")


def _unquote(raw: str) -> str:
    v = raw.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    return v.strip()


class EnvFileCredentialStore:
    def __init__(
        self, env_path: Path, meta_path: Path, *, clock: Callable[[], float] = time.time
    ) -> None:
        self._env = env_path
        self._meta = meta_path
        self._clock = clock

    # -- file helpers ------------------------------------------------------------
    def _lines(self) -> list[str]:
        if not self._env.exists():
            return []
        return self._env.read_text("utf-8").splitlines()

    def _write_lines(self, lines: list[str]) -> None:
        self._env.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._env.with_name(self._env.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + ("\n" if lines else ""))
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        os.chmod(tmp, 0o600)
        os.replace(tmp, self._env)
        os.chmod(self._env, 0o600)

    def _load_meta(self) -> dict[str, dict[str, str]]:
        if not self._meta.exists():
            return {}
        try:
            data = json.loads(self._meta.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_meta(self, meta: dict[str, dict[str, str]]) -> None:
        self._meta.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._meta.with_name(self._meta.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self._meta)
        os.chmod(self._meta, 0o600)

    def _touch_meta(self, key: str, actor: str, action: str) -> None:
        meta = self._load_meta()
        meta[key] = {
            "updated_at": datetime.fromtimestamp(self._clock(), tz=timezone.utc).isoformat(),
            "updated_by": actor,
            "action": action,
        }
        self._save_meta(meta)

    # -- interface ---------------------------------------------------------------
    def read(self, key: str) -> str | None:
        """Server-side only. Last assignment wins, like every env reader."""
        _check_key(key, for_write=False)
        value: str | None = None
        for line in self._lines():
            m = _LINE_RE.match(line)
            if m and m.group(1) == key:
                v = _unquote(m.group(2))
                value = v or None
        return value

    def status(self, key: str) -> CredentialStatus:
        _check_key(key, for_write=False)
        meta = self._load_meta().get(key, {})
        return CredentialStatus(
            key=key,
            configured=self.read(key) is not None,
            updated_at=meta.get("updated_at"),
            updated_by=meta.get("updated_by"),
        )

    def set(self, key: str, value: str, *, actor: str) -> str:
        _check_key(key, for_write=True)
        value = validate_value(value)
        existed = self.read(key) is not None
        lines = self._lines()
        replaced = False
        out: list[str] = []
        for line in lines:
            m = _LINE_RE.match(line)
            if m and m.group(1) == key and not replaced:
                out.append(f"{key}={value}")
                replaced = True
            elif m and m.group(1) == key:
                continue  # drop duplicate assignments
            else:
                out.append(line)
        if not replaced:
            out.append(f"{key}={value}")
        self._write_lines(out)
        action = "replaced" if existed else "created"
        self._touch_meta(key, actor, action)
        return action

    def remove(self, key: str, *, actor: str) -> bool:
        _check_key(key, for_write=True)
        lines = self._lines()
        kept = [l for l in lines if not (_LINE_RE.match(l) and _LINE_RE.match(l).group(1) == key)]
        if len(kept) == len(lines):
            return False
        self._write_lines(kept)
        self._touch_meta(key, actor, "removed")
        return True


# --- liveness probe for local services ------------------------------------------
class Prober(Protocol):
    def probe(self, url: str) -> str: ...  # "ok" | "unreachable"


class HttpProber:
    """GET <base>/models with a short timeout. Only ever pointed at loopback
    services from the catalogue; never sends a key."""

    def __init__(self, timeout: float = 1.5) -> None:
        self._timeout = timeout

    def probe(self, url: str) -> str:
        import httpx

        try:
            r = httpx.get(url.rstrip("/") + "/models", timeout=self._timeout)
        except httpx.HTTPError:
            return "unreachable"
        return "ok" if r.status_code < 500 else "unreachable"
