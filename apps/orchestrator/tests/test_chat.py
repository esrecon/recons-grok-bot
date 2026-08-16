"""Chat backend tests — the bridge between the chat pane and the Hermes CLI.

Real Hermes isn't present in CI, so the command template is pointed at stub
executables. What's under test is the contract: output streams as tokens, the
agent's own HERMES_HOME is used, failures carry the command and the override
hint, and message text can never be interpreted as shell or options.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from recons_orchestrator.app import create_app, get_provisioner, get_settings
from recons_orchestrator.chat import ChatBackend, chat_command
from recons_orchestrator.config import Settings
from recons_orchestrator.models import AgentRecord, ModelTier
from recons_orchestrator.provisioning import Provisioner
from recons_orchestrator.services import RecordingServiceManager


def _record(tmp_path, agent_id="sophie") -> AgentRecord:
    return AgentRecord(
        id=agent_id, name=agent_id.title(), role="x", tier=ModelTier.WORKHORSE,
        a2a_port=9900, created_at="2026-08-16T10:00:00+00:00",
    )


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(root=tmp_path / "recons", dashboard_dist=tmp_path / "d")


# --- command template ---------------------------------------------------------
def test_default_command_appends_text_via_placeholder(monkeypatch):
    monkeypatch.delenv("RECONS_CHAT_CMD", raising=False)
    assert chat_command("hello world") == ["hermes", "-p", "hello world"]


def test_text_is_one_argv_element_never_shell(monkeypatch):
    monkeypatch.delenv("RECONS_CHAT_CMD", raising=False)
    hostile = "hello; rm -rf / && echo $(whoami) | tee x"
    argv = chat_command(hostile)
    assert argv == ["hermes", "-p", hostile]  # intact, unsplit, uninterpreted


def test_custom_template_honoured(monkeypatch):
    monkeypatch.setenv("RECONS_CHAT_CMD", "hermes run --prompt {text} --json")
    assert chat_command("hi") == ["hermes", "run", "--prompt", "hi", "--json"]


def test_template_without_placeholder_appends(monkeypatch):
    monkeypatch.setenv("RECONS_CHAT_CMD", "hermes ask")
    assert chat_command("hi") == ["hermes", "ask", "hi"]


# --- streaming ----------------------------------------------------------------
def test_stream_yields_tokens_then_done(settings, monkeypatch):
    monkeypatch.setenv("RECONS_CHAT_CMD", "printf 'first line\\nsecond line\\n' --ignore {text}")
    events = list(ChatBackend(settings).stream(_record(settings.root), "hi"))
    kinds = [e["type"] for e in events]
    assert kinds[-1] == "done"
    text = "".join(e["text"] for e in events if e["type"] == "token")
    assert "first line" in text and "second line" in text


def test_stream_uses_the_agents_own_home(settings, monkeypatch, tmp_path):
    probe = tmp_path / "probe.sh"
    probe.write_text("#!/bin/sh\necho \"HOME=$HERMES_HOME\"\n")
    probe.chmod(0o755)
    monkeypatch.setenv("RECONS_CHAT_CMD", f"{probe} {{text}}")

    rec = _record(settings.root)
    events = list(ChatBackend(settings).stream(rec, "hi"))
    text = "".join(e.get("text", "") for e in events)
    assert f"HOME={settings.home_dir(rec.id)}" in text


def test_imported_agent_keeps_its_original_home(settings, monkeypatch, tmp_path):
    probe = tmp_path / "probe.sh"
    probe.write_text("#!/bin/sh\necho \"HOME=$HERMES_HOME\"\n")
    probe.chmod(0o755)
    monkeypatch.setenv("RECONS_CHAT_CMD", f"{probe} {{text}}")

    rec = _record(settings.root)
    rec.home = str(tmp_path / "existing-hermes")
    rec.imported = True
    events = list(ChatBackend(settings).stream(rec, "hi"))
    assert f"HOME={rec.home}" in "".join(e.get("text", "") for e in events)


def test_missing_cli_reports_the_override(settings, monkeypatch):
    monkeypatch.setenv("RECONS_CHAT_CMD", "definitely-not-hermes-xyz {text}")
    events = list(ChatBackend(settings).stream(_record(settings.root), "hi"))
    assert events[-1]["type"] == "error"
    assert "RECONS_CHAT_CMD" in events[-1]["message"]


def test_nonzero_exit_reports_stderr_and_override(settings, monkeypatch, tmp_path):
    fail = tmp_path / "fail.sh"
    fail.write_text("#!/bin/sh\necho 'unknown option -p' >&2\nexit 2\n")
    fail.chmod(0o755)
    monkeypatch.setenv("RECONS_CHAT_CMD", f"{fail} {{text}}")

    events = list(ChatBackend(settings).stream(_record(settings.root), "hi"))
    err = events[-1]
    assert err["type"] == "error"
    assert "unknown option -p" in err["message"]
    assert "RECONS_CHAT_CMD" in err["message"]


# --- the endpoint -------------------------------------------------------------
@pytest.fixture
def client(settings):
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_provisioner] = lambda: Provisioner(
        settings, services=RecordingServiceManager()
    )
    return TestClient(app)


def _sse_events(body: str):
    out = []
    for line in body.splitlines():
        if line.startswith("data: "):
            out.append(json.loads(line[6:]))
    return out


def test_endpoint_streams_sse(client, settings, monkeypatch):
    from recons_orchestrator.models import AgentSpec

    monkeypatch.setenv("RECONS_CHAT_CMD", "printf 'hello from sophie\\n' --ignore {text}")
    prov = Provisioner(settings, services=RecordingServiceManager())
    prov.create_agent(AgentSpec(name="Sophie", role="Email"))

    resp = client.post("/api/agents/sophie/messages", json={"text": "hi"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = _sse_events(resp.text)
    assert events[-1]["type"] == "done"
    assert any("hello from sophie" in e.get("text", "") for e in events)


def test_endpoint_404_for_unknown_agent(client):
    assert client.post("/api/agents/nope/messages", json={"text": "hi"}).status_code == 404


def test_endpoint_rejects_empty_message(client, settings):
    from recons_orchestrator.models import AgentSpec

    prov = Provisioner(settings, services=RecordingServiceManager())
    prov.create_agent(AgentSpec(name="Sophie", role="Email"))
    assert client.post("/api/agents/sophie/messages", json={"text": "  "}).status_code == 422


def test_ansi_escapes_are_stripped(settings, monkeypatch, tmp_path):
    """Terminal colour codes must not reach the chat bubble."""
    script = tmp_path / "colour.sh"
    script.write_text("#!/bin/sh\nprintf '\\033[31mred alert\\033[0m plain\\n'\n")
    script.chmod(0o755)
    monkeypatch.setenv("RECONS_CHAT_CMD", f"{script} {{text}}")

    events = list(ChatBackend(settings).stream(_record(settings.root), "hi"))
    text = "".join(e.get("text", "") for e in events if e["type"] == "token")
    assert "red alert" in text and "plain" in text
    assert "\x1b" not in text


def test_child_gets_no_tty_and_unbuffered_env(settings, monkeypatch, tmp_path):
    """stdin is EOF (a TUI cannot hang us) and buffering is discouraged."""
    probe = tmp_path / "probe.sh"
    probe.write_text(
        "#!/bin/sh\n"
        "if [ -t 0 ]; then echo stdin=tty; else echo stdin=notty; fi\n"
        "read -r line && echo \"got:$line\" || echo stdin=eof\n"
        "echo \"unbuf=$PYTHONUNBUFFERED term=$TERM\"\n"
    )
    probe.chmod(0o755)
    monkeypatch.setenv("RECONS_CHAT_CMD", f"{probe} {{text}}")

    events = list(ChatBackend(settings).stream(_record(settings.root), "hi"))
    text = "".join(e.get("text", "") for e in events if e["type"] == "token")
    assert "stdin=notty" in text
    assert "stdin=eof" in text          # read hit EOF instantly instead of hanging
    assert "unbuf=1" in text and "term=dumb" in text
    assert events[-1]["type"] == "done"


# --- history ------------------------------------------------------------------
def test_history_endpoint_returns_the_agents_recorded_turns(client, settings):
    from recons_orchestrator.models import AgentSpec
    from tests.fixtures import make_state_db

    prov = Provisioner(settings, services=RecordingServiceManager())
    prov.create_agent(AgentSpec(name="Sophie", role="Email"))
    make_state_db(settings.home_dir("sophie") / "state.db", agent="sophie",
                  base_ts=1_786_000_000)

    body = client.get("/api/agents/sophie/history").json()
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["user", "assistant", "assistant"]
    assert body["messages"][0]["text"] == "Find three suppliers"


def test_history_empty_when_agent_has_no_db_yet(client, settings):
    from recons_orchestrator.models import AgentSpec

    prov = Provisioner(settings, services=RecordingServiceManager())
    prov.create_agent(AgentSpec(name="Sophie", role="Email"))
    assert client.get("/api/agents/sophie/history").json() == {"messages": []}


def test_history_404_for_unknown_agent(client):
    assert client.get("/api/agents/nope/history").status_code == 404


# --- shared credentials -------------------------------------------------------
def test_new_agent_links_the_shared_auth_store(settings, monkeypatch, tmp_path):
    from recons_orchestrator.models import AgentSpec

    default_home = tmp_path / "default-hermes"
    default_home.mkdir()
    (default_home / "auth.json").write_text('{"providers": {"openai": {}}}')
    monkeypatch.setenv("HERMES_HOME", str(default_home))

    prov = Provisioner(settings, services=RecordingServiceManager())
    prov.create_agent(AgentSpec(name="Sophie", role="Email"))

    link = settings.home_dir("sophie") / "auth.json"
    assert link.is_symlink()
    assert link.resolve() == (default_home / "auth.json").resolve()
    # Shared, not copied: a token refresh in the default store is visible here.
    (default_home / "auth.json").write_text('{"providers": {"openai": {"t": "new"}}}')
    assert "new" in link.read_text()
