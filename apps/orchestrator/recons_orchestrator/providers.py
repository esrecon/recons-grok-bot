"""Provider setup, driven from the dashboard instead of the VPS terminal.

Three kinds of provider, three ways to connect:

  * **API key** (Nous, and Anthropic if you choose the key route) — the user
    pastes a key into the app; it is written to the shared secrets file.
  * **Subscription sign-in** (ChatGPT/Codex) — a device-code flow. The app asks
    the orchestrator to start it, shows the verification URL and user code, and
    polls until the CLI reports success. No terminal required.
  * **Local service** (Claude via the Agent SDK wrapper) — the app reports
    whether the wrapper is answering on loopback.

The device-code login shells out to the Hermes CLI, whose exact subcommand is
version-fragile (VERIFY). Rather than pretend otherwise, the flow captures the
CLI's output, and if it cannot find a URL and code it surfaces the raw output
plus the exact command, so the user can finish it manually and is never left
staring at a spinner.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .secrets_store import SecretsStore


class ProviderState(str, Enum):
    CONFIGURED = "configured"
    NOT_CONFIGURED = "not_configured"
    ERROR = "error"


@dataclass
class ProviderStatus:
    id: str
    label: str
    tier: str
    method: str  # api_key | oauth | service
    state: ProviderState
    detail: str = ""
    docs: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "tier": self.tier,
            "method": self.method,
            "state": self.state.value,
            "detail": self.detail,
            "docs": self.docs,
        }


# VERIFY these against the installed Hermes version. Overridable by env so a
# CLI change never requires a code change on the VPS.
HERMES_BIN = os.environ.get("RECONS_HERMES_BIN", "hermes")
OPENAI_LOGIN_CMD = os.environ.get(
    "RECONS_OPENAI_LOGIN_CMD", f"{HERMES_BIN} auth add openai"
)

_URL_RE = re.compile(r"https?://[^\s\"'<>]+")
# Device codes are conventionally short, upper-case, often hyphenated (ABCD-EFGH).
_CODE_RE = re.compile(r"\b([A-Z0-9]{4,8}-[A-Z0-9]{4,8}|[A-Z0-9]{6,10})\b")


@dataclass
class LoginSession:
    id: str
    provider: str
    command: str
    status: str = "starting"  # starting | awaiting_user | success | failed
    url: str | None = None
    code: str | None = None
    message: str = ""
    output: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "status": self.status,
            "url": self.url,
            "code": self.code,
            "message": self.message,
            "command": self.command,
            # Tail only: enough to diagnose, not a firehose.
            "output": self.output[-40:],
        }


class ProviderService:
    def __init__(self, secrets: SecretsStore, home: Path | None = None) -> None:
        self._secrets = secrets
        self._home = home or Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
        self._logins: dict[str, LoginSession] = {}
        self._lock = threading.Lock()

    # -- status ----------------------------------------------------------------
    def _openai_oauth_present(self) -> bool:
        """Hermes stores subscription OAuth tokens in its own auth store."""
        auth = self._home / "auth.json"  # VERIFY path/filename
        if not auth.exists():
            return False
        try:
            text = auth.read_text("utf-8")
        except OSError:
            return False
        return "openai" in text.lower() or "chatgpt" in text.lower()

    def _wrapper_alive(self) -> bool:
        base = self._secrets.get("CLAUDE_WRAPPER_BASE_URL") or "http://127.0.0.1:8600/v1"
        try:
            import httpx

            root = base.rsplit("/v1", 1)[0]
            resp = httpx.get(f"{root}/health", timeout=1.5)
            return resp.status_code < 500
        except Exception:
            return False

    def statuses(self) -> list[ProviderStatus]:
        out: list[ProviderStatus] = []

        # --- Nous: the cheap bulk tier, and the fastest way to a working bot ---
        nous_set = self._secrets.is_set("NOUS_API_KEY")
        out.append(
            ProviderStatus(
                id="nous",
                label="Nous Portal",
                tier="bulk",
                method="api_key",
                state=ProviderState.CONFIGURED if nous_set else ProviderState.NOT_CONFIGURED,
                detail="Cheap, high-volume work and background tasks."
                if nous_set
                else "Paste an API key from portal.nousresearch.com. Cheapest way to get running.",
                docs="https://portal.nousresearch.com",
            )
        )

        # --- OpenAI: subscription sign-in preferred, API key accepted ---------
        if self._openai_oauth_present():
            openai_state, openai_detail = (
                ProviderState.CONFIGURED,
                "Signed in with your ChatGPT subscription.",
            )
        elif self._secrets.is_set("OPENAI_API_KEY"):
            openai_state, openai_detail = (
                ProviderState.CONFIGURED,
                "Using a pay-as-you-go API key.",
            )
        else:
            openai_state, openai_detail = (
                ProviderState.NOT_CONFIGURED,
                "Sign in with the ChatGPT subscription you already pay for.",
            )
        out.append(
            ProviderStatus(
                id="openai",
                label="ChatGPT",
                tier="workhorse",
                method="oauth",
                state=openai_state,
                detail=openai_detail,
            )
        )

        # --- Anthropic: wrapper (Agent SDK) or a Console API key --------------
        mode = self._secrets.get("ANTHROPIC_AUTH_MODE") or "wrapper"
        if mode == "api-key" and self._secrets.is_set("ANTHROPIC_API_KEY"):
            anth_state, anth_detail = (
                ProviderState.CONFIGURED,
                "Using a Claude Console API key (pay-as-you-go).",
            )
        elif mode != "api-key" and self._wrapper_alive():
            anth_state, anth_detail = (
                ProviderState.CONFIGURED,
                "Agent SDK wrapper is running against your Claude login.",
            )
        elif mode != "api-key":
            anth_state, anth_detail = (
                ProviderState.NOT_CONFIGURED,
                "Optional. Start the wrapper service, or switch to a Console API key.",
            )
        else:
            anth_state, anth_detail = (
                ProviderState.NOT_CONFIGURED,
                "Optional. Paste a Claude Console API key.",
            )
        out.append(
            ProviderStatus(
                id="anthropic",
                label="Claude",
                tier="lead",
                method="service",
                state=anth_state,
                detail=anth_detail,
                docs="docs/40-providers-and-tos.md",
            )
        )
        return out

    # -- api-key providers -----------------------------------------------------
    def save_api_key(self, provider: str, key: str) -> ProviderStatus:
        key = key.strip()
        if not key:
            raise ValueError("key must not be empty")
        if provider == "nous":
            self._secrets.set_many({"NOUS_API_KEY": key})
        elif provider == "openai":
            self._secrets.set_many({"OPENAI_API_KEY": key})
        elif provider == "anthropic":
            # Choosing the key route also flips the mode, so the lead agent
            # stops using the wrapper — the documented one-line exit.
            self._secrets.set_many(
                {"ANTHROPIC_API_KEY": key, "ANTHROPIC_AUTH_MODE": "api-key"}
            )
        else:
            raise ValueError(f"unknown provider: {provider}")
        return next(s for s in self.statuses() if s.id == provider)

    def clear(self, provider: str) -> ProviderStatus:
        mapping = {
            "nous": ["NOUS_API_KEY"],
            "openai": ["OPENAI_API_KEY"],
            "anthropic": ["ANTHROPIC_API_KEY"],
        }
        if provider not in mapping:
            raise ValueError(f"unknown provider: {provider}")
        for key in mapping[provider]:
            self._secrets.unset(key)
        if provider == "anthropic":
            self._secrets.set_many({"ANTHROPIC_AUTH_MODE": "wrapper"})
        return next(s for s in self.statuses() if s.id == provider)

    # -- subscription sign-in (device code) ------------------------------------
    def start_login(self, provider: str, runner=None) -> LoginSession:
        if provider != "openai":
            raise ValueError(f"{provider} does not use subscription sign-in")
        session = LoginSession(
            id=uuid.uuid4().hex[:12], provider=provider, command=OPENAI_LOGIN_CMD
        )
        with self._lock:
            self._logins[session.id] = session

        target = runner or self._run_login
        thread = threading.Thread(target=target, args=(session,), daemon=True)
        thread.start()
        return session

    def _run_login(self, session: LoginSession) -> None:
        try:
            proc = subprocess.Popen(  # noqa: S603 - command is operator-configured
                shlex.split(session.command),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError:
            session.status = "failed"
            session.message = (
                f"Could not run `{session.command}`. Is the Hermes CLI installed on this "
                f"machine? Set RECONS_OPENAI_LOGIN_CMD if the subcommand differs in your version."
            )
            return
        except Exception as exc:  # pragma: no cover - defensive
            session.status = "failed"
            session.message = str(exc)
            return

        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip()
            if not line:
                continue
            session.output.append(line)
            if session.url is None:
                m = _URL_RE.search(line)
                if m:
                    session.url = m.group(0).rstrip(".,)")
            if session.code is None:
                # Only look for a code on lines that aren't just the URL.
                candidate = _CODE_RE.search(_URL_RE.sub("", line))
                if candidate:
                    session.code = candidate.group(1)
            if session.url and session.status == "starting":
                session.status = "awaiting_user"

        proc.wait()
        if proc.returncode == 0:
            session.status = "success"
            session.message = "Signed in. Your agents can use this subscription now."
        else:
            session.status = "failed"
            if not session.url:
                session.message = (
                    "The sign-in helper exited without giving a link. Run this in a terminal "
                    f"on the VPS to finish: {session.command}"
                )
            else:
                session.message = "Sign-in did not complete. You can try again."

    def get_login(self, login_id: str) -> LoginSession | None:
        with self._lock:
            return self._logins.get(login_id)
