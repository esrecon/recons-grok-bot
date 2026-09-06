"""Server-side store for the shared secrets file.

All credentials live in one file (`shared/secrets.env`, chmod 600) that every
agent reads. The dashboard writes to it through this module so the user never
has to edit a file on the VPS by hand.

Two rules this module enforces:
  * **Write-only.** Values can be set and their presence reported, but the API
    never returns a stored secret. The dashboard shows "configured", not the key.
  * **Atomic.** Writes go to a temp file and are renamed into place, so an
    interrupted write can't truncate the file every agent depends on.
"""

from __future__ import annotations

import os
import re
import secrets as pysecrets
from pathlib import Path

# Only these keys may be written through the API. An allowlist keeps a bug (or a
# malicious request) from injecting arbitrary environment into every agent's
# systemd unit.
WRITABLE_KEYS = frozenset(
    {
        "NOUS_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_MODE",
        "CLAUDE_WRAPPER_BASE_URL",
        "CLAUDE_WRAPPER_API_KEY",
        "IMAGE_API_KEY",
        "IMAGE_API_BASE_URL",
        "IMAGE_API_MODEL",
        "RECONS_WEBHOOK_SECRET",
        "RECONS_OPERATOR_USER",
        "RECONS_OPERATOR_PASSWORD_HASH",
        "RECONS_SESSION_SECRET",
    }
)

# Keys minted by the custom-model registry (custom_models.py) are the one
# open-ended family: the CUSTOM_ prefix + _API_KEY suffix keeps the injectable
# surface to credentials the operator deliberately created, nothing else.
_CUSTOM_KEY = re.compile(r"^CUSTOM_[A-Z0-9_]+_API_KEY$")

_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def _writable(key: str) -> bool:
    return key in WRITABLE_KEYS or bool(_CUSTOM_KEY.match(key))


class SecretsError(ValueError):
    pass


class SecretsStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    # -- reading ---------------------------------------------------------------
    def _read_raw(self) -> list[str]:
        if not self._path.exists():
            return []
        return self._path.read_text("utf-8").splitlines()

    def _parsed(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for line in self._read_raw():
            if line.lstrip().startswith("#"):
                continue
            m = _LINE.match(line)
            if m:
                out[m.group(1)] = m.group(2).strip()
        return out

    def is_set(self, key: str) -> bool:
        """True when the key has a non-empty value. Never exposes the value."""
        return bool(self._parsed().get(key, "").strip())

    def get(self, key: str) -> str | None:
        """Internal use only — for talking to providers. Never return this over the API."""
        value = self._parsed().get(key, "").strip()
        return value or None

    def configured_keys(self) -> set[str]:
        return {k for k, v in self._parsed().items() if v.strip()}

    def as_env(self) -> dict[str, str]:
        """Non-empty values, for subprocess environments.

        Internal use only — same rule as get(): never returned over the API.
        """
        return {k: v.strip() for k, v in self._parsed().items() if v.strip()}

    # -- writing ---------------------------------------------------------------
    def set_many(self, values: dict[str, str]) -> None:
        """Set or replace keys, preserving comments, ordering and unknown keys."""
        for key in values:
            if not _writable(key):
                raise SecretsError(f"{key} is not a writable setting")
        for key, value in values.items():
            if "\n" in value or "\r" in value:
                raise SecretsError(f"{key} value must be a single line")

        lines = self._read_raw()
        remaining = dict(values)

        for i, line in enumerate(lines):
            if line.lstrip().startswith("#"):
                continue
            m = _LINE.match(line)
            if m and m.group(1) in remaining:
                key = m.group(1)
                lines[i] = f"{key}={remaining.pop(key)}"

        # Anything not already present gets appended.
        if remaining:
            if lines and lines[-1].strip():
                lines.append("")
            for key, value in remaining.items():
                lines.append(f"{key}={value}")

        self._write(lines)

    def unset(self, key: str) -> None:
        if not _writable(key):
            raise SecretsError(f"{key} is not a writable setting")
        self.set_many({key: ""})

    def _write(self, lines: list[str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".env.tmp")
        tmp.write_text("\n".join(lines).rstrip("\n") + "\n", "utf-8")
        tmp.chmod(0o600)
        os.replace(tmp, self._path)
        self._path.chmod(0o600)

    # -- first-boot bootstrap --------------------------------------------------
    def ensure_webhook_secret(self) -> bool:
        """Generate the audit-feed signing secret if it isn't set yet.

        The user should never have to run `openssl rand` for this: it is an
        internal shared secret between Hermes and the ledger, not an account
        credential. Returns True if one was generated.
        """
        if self.is_set("RECONS_WEBHOOK_SECRET"):
            return False
        self.set_many({"RECONS_WEBHOOK_SECRET": pysecrets.token_hex(32)})
        return True

    def ensure_file(self, template: Path | None = None) -> None:
        """Create the secrets file (from the template if given) if missing."""
        if self._path.exists():
            self._path.chmod(0o600)
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if template and template.exists():
            self._path.write_text(template.read_text("utf-8"), "utf-8")
        else:
            self._path.write_text(
                "# Shared secrets for ALL agents — managed from the dashboard.\n", "utf-8"
            )
        self._path.chmod(0o600)
