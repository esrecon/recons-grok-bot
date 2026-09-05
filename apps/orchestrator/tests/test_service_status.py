"""Per-agent service health for Settings, through the ServiceManager seam so
no test touches systemd."""

from __future__ import annotations

from recons_orchestrator.models import AgentSpec
from recons_orchestrator.services import RecordingServiceManager, SystemdUserServiceManager

from tests.auth import build_app, login, make_client


def test_systemd_status_parses_is_active():
    calls = []

    class Result:
        def __init__(self, out):
            self.stdout = out
            self.returncode = 0 if out.strip() == "active" else 3

    def runner(cmd, **kw):
        calls.append(cmd)
        return Result("inactive\n" if "clerk" in cmd[-1] else "active\n")

    sm = SystemdUserServiceManager(runner=runner)
    assert sm.status("hermes-gateway@recon.service") == "active"
    assert sm.status("hermes-gateway@clerk.service") == "inactive"
    assert calls[0][:3] == ["systemctl", "--user", "is-active"]


def test_recording_status_defaults_and_overrides():
    sm = RecordingServiceManager()
    assert sm.status("x") == "active"
    sm.statuses["x"] = "failed"
    assert sm.status("x") == "failed"


def test_services_endpoint(settings):
    app = build_app(settings)
    c = make_client(app)
    login(c)
    c.post("/api/agents", json={"name": "Recon", "role": "x"})
    c.post("/api/agents", json={"name": "Scout", "role": "y"})
    app.state.services.statuses[settings.unit_name("scout")] = "failed"
    body = c.get("/api/settings/services").json()
    rows = {r["agent"]: r for r in body["services"]}
    assert rows["recon"]["status"] == "active"
    assert rows["recon"]["unit"] == settings.unit_name("recon")
    assert rows["recon"]["expected"] == "running"
    assert rows["scout"]["status"] == "failed"
    assert rows["scout"]["healthy"] is False
    assert rows["recon"]["healthy"] is True
    c.post("/api/agents/recon/pause")
    body = c.get("/api/settings/services").json()
    recon = next(r for r in body["services"] if r["agent"] == "recon")
    assert recon["expected"] == "paused"
