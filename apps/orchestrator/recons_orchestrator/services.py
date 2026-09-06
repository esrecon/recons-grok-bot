"""Service-manager abstraction.

Provisioning must be unit-testable without a real init system, so all systemd
interaction goes through this interface. The production implementation shells
out to `systemctl`; tests inject a fake that records calls.

**Scope.** Agents normally run as user services (`systemctl --user`) owned by an
unprivileged account with lingering enabled. But root has no user-level systemd
instance on most VPS images — `systemctl --user` there fails with "Failed to
connect to bus: No medium found" — so when we're root we drive system scope
instead. `RECONS_SYSTEMD_SCOPE=user|system` overrides the detection.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Literal, Protocol

Scope = Literal["user", "system"]


def detect_scope(
    runner=subprocess.run, root: Path | None = None, euid: int | None = None
) -> Scope:
    """Which systemd scope this install uses.

    Order matters. The scope file is written by vps-bootstrap.sh at install time
    and is authoritative: probing gives different answers in different
    environments (an interactive root session has a user bus, the same account
    under `sudo` does not), so two components that each probe can disagree and
    install units in one scope while driving them in the other.

    Root skips the probe entirely: an interactive root session often has a user
    bus, but a system service run as root does not — and this code runs inside
    one. Trusting root's probe once produced two rival installs of the same
    service. Probing is the last resort, for unprivileged accounts only.
    """
    override = os.environ.get("RECONS_SYSTEMD_SCOPE", "").strip().lower()
    if override in ("user", "system"):
        return override  # type: ignore[return-value]

    base = root or Path(os.environ.get("RECONS_ROOT", "/opt/recons"))
    try:
        recorded = (base / "systemd-scope").read_text("utf-8").strip().lower()
        if recorded in ("user", "system"):
            return recorded  # type: ignore[return-value]
    except OSError:
        pass

    if (euid if euid is not None else os.geteuid()) == 0:
        return "system"

    try:
        result = runner(
            ["systemctl", "--user", "show-environment"],
            check=False,
            capture_output=True,
        )
    except (OSError, ValueError):
        return "system"
    return "user" if getattr(result, "returncode", 1) == 0 else "system"


class ServiceManager(Protocol):
    def enable_now(self, unit: str) -> None: ...
    def stop(self, unit: str) -> None: ...
    def restart(self, unit: str) -> None: ...
    def disable(self, unit: str) -> None: ...
    def daemon_reload(self) -> None: ...
    def status(self, unit: str) -> str: ...  # active | inactive | failed | activating | unknown


class SystemdServiceManager:
    """Drives systemctl in whichever scope this install uses.

    User scope requires `loginctl enable-linger <user>` so the units survive
    logout/reboot (see docs/10). System scope needs root and has no such caveat.
    """

    def __init__(self, runner=subprocess.run, scope: Scope | None = None) -> None:
        self._run = runner
        self._scope: Scope = scope or detect_scope(runner)

    @property
    def scope(self) -> Scope:
        return self._scope

    def _systemctl(self, *args: str) -> None:
        cmd = ["systemctl"]
        if self._scope == "user":
            cmd.append("--user")
        self._run([*cmd, *args], check=True)

    def enable_now(self, unit: str) -> None:
        self._systemctl("enable", "--now", unit)

    def stop(self, unit: str) -> None:
        self._systemctl("stop", unit)

    def restart(self, unit: str) -> None:
        self._systemctl("restart", unit)

    def disable(self, unit: str) -> None:
        self._systemctl("disable", "--now", unit)

    def daemon_reload(self) -> None:
        self._systemctl("daemon-reload")

    def status(self, unit: str) -> str:
        """`systemctl --user is-active` prints one word and exits non-zero when
        the unit isn't active, so this never uses check=True."""
        try:
            result = self._run(
                ["systemctl", "--user", "is-active", unit], capture_output=True, text=True
            )
        except (OSError, subprocess.SubprocessError):
            return "unknown"
        out = (getattr(result, "stdout", "") or "").strip().splitlines()
        return out[0].strip() if out else "unknown"


class RecordingServiceManager:
    """Test double: records the sequence of operations instead of running them."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.statuses: dict[str, str] = {}

    def enable_now(self, unit: str) -> None:
        self.calls.append(("enable_now", unit))

    def stop(self, unit: str) -> None:
        self.calls.append(("stop", unit))

    def restart(self, unit: str) -> None:
        self.calls.append(("restart", unit))

    def disable(self, unit: str) -> None:
        self.calls.append(("disable", unit))

    def daemon_reload(self) -> None:
        self.calls.append(("daemon_reload", ""))

    def status(self, unit: str) -> str:
        return self.statuses.get(unit, "active")

    def status(self, unit: str) -> str:
        return self.statuses.get(unit, "active")
