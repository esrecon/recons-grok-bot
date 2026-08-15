"""Provider setup tests — status detection, key saving, and the device-code
sign-in flow the dashboard drives (including its honest failure modes)."""

from __future__ import annotations

import time

import pytest

from recons_orchestrator.providers import ProviderService, ProviderState
from recons_orchestrator.secrets_store import SecretsStore


@pytest.fixture
def svc(tmp_path):
    secrets = SecretsStore(tmp_path / "secrets.env")
    secrets.ensure_file()
    # An empty Hermes home => no OAuth token present.
    return ProviderService(secrets, home=tmp_path / "hermes")


def _by_id(statuses, pid):
    return next(s for s in statuses if s.id == pid)


def test_all_unconfigured_initially(svc):
    statuses = svc.statuses()
    assert {s.id for s in statuses} == {"nous", "openai", "anthropic"}
    assert all(s.state is ProviderState.NOT_CONFIGURED for s in statuses)


def test_saving_nous_key_marks_configured(svc):
    status = svc.save_api_key("nous", "nous-key-123")
    assert status.state is ProviderState.CONFIGURED
    assert _by_id(svc.statuses(), "nous").state is ProviderState.CONFIGURED


def test_api_key_never_returned_in_status(svc):
    svc.save_api_key("nous", "super-secret-value")
    payload = [s.to_json() for s in svc.statuses()]
    assert "super-secret-value" not in str(payload)


def test_anthropic_key_flips_auth_mode(svc, tmp_path):
    svc.save_api_key("anthropic", "anthropic-test-key")
    text = (tmp_path / "secrets.env").read_text()
    assert "ANTHROPIC_AUTH_MODE=api-key" in text
    assert _by_id(svc.statuses(), "anthropic").state is ProviderState.CONFIGURED


def test_clearing_anthropic_returns_to_wrapper_mode(svc, tmp_path):
    svc.save_api_key("anthropic", "anthropic-test-key")
    svc.clear("anthropic")
    assert "ANTHROPIC_AUTH_MODE=wrapper" in (tmp_path / "secrets.env").read_text()
    assert _by_id(svc.statuses(), "anthropic").state is ProviderState.NOT_CONFIGURED


def test_openai_oauth_detected_from_hermes_auth_store(tmp_path):
    secrets = SecretsStore(tmp_path / "secrets.env")
    secrets.ensure_file()
    home = tmp_path / "hermes"
    home.mkdir()
    (home / "auth.json").write_text('{"providers": {"openai": {"token": "x"}}}')
    svc = ProviderService(secrets, home=home)
    assert _by_id(svc.statuses(), "openai").state is ProviderState.CONFIGURED


def test_empty_key_rejected(svc):
    with pytest.raises(ValueError):
        svc.save_api_key("nous", "   ")


def test_unknown_provider_rejected(svc):
    with pytest.raises(ValueError):
        svc.save_api_key("skynet", "k")


# --- device-code sign-in --------------------------------------------------
def _wait(session, predicate, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate(session):
            return True
        time.sleep(0.02)
    return False


def test_login_captures_url_and_code(svc, monkeypatch):
    """The app must be able to show the user a link and a code."""
    from recons_orchestrator import providers as mod

    monkeypatch.setattr(
        mod,
        "OPENAI_LOGIN_CMD",
        "printf 'Visit https://auth.example.com/device to continue\\nCode: WXYZ-1234\\n'",
    )
    # Re-read the patched module constant inside the service.
    session = svc.start_login("openai", runner=None)
    # The service captured the command at construction time; drive it directly.
    session.command = mod.OPENAI_LOGIN_CMD
    svc._run_login(session)

    assert session.url == "https://auth.example.com/device"
    assert session.code == "WXYZ-1234"
    assert session.status == "success"


def test_login_without_link_tells_user_the_manual_command(svc):
    session = svc.start_login("openai")
    session.command = "false"          # exits non-zero, prints nothing
    svc._run_login(session)
    assert session.status == "failed"
    assert "terminal" in session.message.lower()


def test_login_reports_missing_cli_clearly(svc):
    session = svc.start_login("openai")
    session.command = "definitely-not-a-real-binary-xyz"
    svc._run_login(session)
    assert session.status == "failed"
    assert "Hermes CLI" in session.message


def test_login_session_is_pollable(svc):
    session = svc.start_login("openai")
    assert svc.get_login(session.id) is not None
    assert svc.get_login("nope") is None


def test_non_oauth_provider_rejects_login(svc):
    with pytest.raises(ValueError):
        svc.start_login("nous")
