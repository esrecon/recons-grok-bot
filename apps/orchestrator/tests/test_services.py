"""Systemd scope tests.

Agents run as user services under a normal account, but root has no user-level
systemd instance on most VPS images, so the manager must fall back to system
scope rather than failing with "Failed to connect to bus".
"""

from __future__ import annotations

from dataclasses import dataclass

from recons_orchestrator.services import SystemdServiceManager, detect_scope


@dataclass
class FakeResult:
    returncode: int


class FakeRunner:
    """Records commands; returns a configurable status for the probe."""

    def __init__(self, user_scope_ok: bool = True) -> None:
        self.calls: list[list[str]] = []
        self._user_ok = user_scope_ok

    def __call__(self, cmd, check=False, capture_output=False):
        self.calls.append(list(cmd))
        if cmd[:3] == ["systemctl", "--user", "show-environment"]:
            return FakeResult(0 if self._user_ok else 1)
        return FakeResult(0)


def test_detects_user_scope_when_available(monkeypatch):
    monkeypatch.delenv("RECONS_SYSTEMD_SCOPE", raising=False)
    assert detect_scope(FakeRunner(user_scope_ok=True), euid=1000) == "user"


def test_falls_back_to_system_scope_when_user_bus_is_missing(monkeypatch):
    monkeypatch.delenv("RECONS_SYSTEMD_SCOPE", raising=False)
    assert detect_scope(FakeRunner(user_scope_ok=False), euid=1000) == "system"


def test_env_override_wins(monkeypatch):
    monkeypatch.setenv("RECONS_SYSTEMD_SCOPE", "system")
    assert detect_scope(FakeRunner(user_scope_ok=True)) == "system"
    monkeypatch.setenv("RECONS_SYSTEMD_SCOPE", "user")
    assert detect_scope(FakeRunner(user_scope_ok=False)) == "user"


def test_missing_systemctl_binary_falls_back_to_system(monkeypatch):
    monkeypatch.delenv("RECONS_SYSTEMD_SCOPE", raising=False)

    def explode(*_args, **_kwargs):
        raise OSError("no systemctl here")

    assert detect_scope(explode, euid=1000) == "system"


def test_recorded_scope_beats_probing(monkeypatch, tmp_path):
    """The scope file records what was actually installed. Probing gives
    different answers in different environments, so it must not override it."""
    monkeypatch.delenv("RECONS_SYSTEMD_SCOPE", raising=False)
    (tmp_path / "systemd-scope").write_text("system\n")
    # Probe would say "user"; the recorded scope must win.
    assert detect_scope(FakeRunner(user_scope_ok=True), root=tmp_path) == "system"


def test_recorded_scope_read_from_recons_root_env(monkeypatch, tmp_path):
    monkeypatch.delenv("RECONS_SYSTEMD_SCOPE", raising=False)
    monkeypatch.setenv("RECONS_ROOT", str(tmp_path))
    (tmp_path / "systemd-scope").write_text("user\n")
    assert detect_scope(FakeRunner(user_scope_ok=False)) == "user"


def test_env_override_beats_even_the_recorded_scope(monkeypatch, tmp_path):
    monkeypatch.setenv("RECONS_SYSTEMD_SCOPE", "user")
    (tmp_path / "systemd-scope").write_text("system\n")
    assert detect_scope(FakeRunner(user_scope_ok=False), root=tmp_path) == "user"


def test_garbage_in_scope_file_falls_back_to_probing(monkeypatch, tmp_path):
    monkeypatch.delenv("RECONS_SYSTEMD_SCOPE", raising=False)
    (tmp_path / "systemd-scope").write_text("banana\n")
    assert detect_scope(FakeRunner(user_scope_ok=False), root=tmp_path, euid=1000) == "system"
    assert detect_scope(FakeRunner(user_scope_ok=True), root=tmp_path, euid=1000) == "user"


def test_root_always_gets_system_scope_even_when_a_user_bus_exists(monkeypatch, tmp_path):
    """An interactive root session often has a user bus, but a system service
    run as root does not — and this code runs inside one. Trusting root's probe
    once produced two rival installs of the same service."""
    monkeypatch.delenv("RECONS_SYSTEMD_SCOPE", raising=False)
    assert detect_scope(FakeRunner(user_scope_ok=True), root=tmp_path, euid=0) == "system"


def test_scope_file_still_beats_the_root_rule(monkeypatch, tmp_path):
    monkeypatch.delenv("RECONS_SYSTEMD_SCOPE", raising=False)
    (tmp_path / "systemd-scope").write_text("user\n")
    assert detect_scope(FakeRunner(user_scope_ok=True), root=tmp_path, euid=0) == "user"


def test_user_scope_passes_the_user_flag():
    runner = FakeRunner()
    SystemdServiceManager(runner=runner, scope="user").enable_now("x.service")
    assert runner.calls[-1] == ["systemctl", "--user", "enable", "--now", "x.service"]


def test_system_scope_omits_the_user_flag():
    runner = FakeRunner()
    SystemdServiceManager(runner=runner, scope="system").enable_now("x.service")
    assert runner.calls[-1] == ["systemctl", "enable", "--now", "x.service"]


def test_all_operations_respect_scope():
    runner = FakeRunner()
    mgr = SystemdServiceManager(runner=runner, scope="system")
    mgr.daemon_reload()
    mgr.stop("a.service")
    mgr.restart("b.service")
    mgr.disable("c.service")
    assert runner.calls == [
        ["systemctl", "daemon-reload"],
        ["systemctl", "stop", "a.service"],
        ["systemctl", "restart", "b.service"],
        ["systemctl", "disable", "--now", "c.service"],
    ]
    assert mgr.scope == "system"
