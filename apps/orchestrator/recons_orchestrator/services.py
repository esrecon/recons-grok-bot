"""Service-manager abstraction.

Provisioning must be unit-testable without a real init system, so all systemd
interaction goes through this interface. The production implementation shells
out to `systemctl --user`; tests inject a fake that records calls.
"""

from __future__ import annotations

import subprocess
from typing import Protocol


class ServiceManager(Protocol):
    def enable_now(self, unit: str) -> None: ...
    def stop(self, unit: str) -> None: ...
    def restart(self, unit: str) -> None: ...
    def disable(self, unit: str) -> None: ...
    def daemon_reload(self) -> None: ...


class SystemdUserServiceManager:
    """Drives `systemctl --user`. Requires `loginctl enable-linger <user>` so
    the units survive logout/reboot (see docs/10)."""

    def __init__(self, runner=subprocess.run) -> None:
        self._run = runner

    def _systemctl(self, *args: str) -> None:
        self._run(["systemctl", "--user", *args], check=True)

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


class RecordingServiceManager:
    """Test double: records the sequence of operations instead of running them."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

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
