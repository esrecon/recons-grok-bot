"""Live chat: the orchestrator streams a turn to the browser as SSE through a
pluggable ChatClient. Production talks A2A to the agent's loopback port with
an orchestrator-only edge token; tests use a fake client and a mocked
transport. Approval decisions travel the same way and are audited."""

from __future__ import annotations

import json

import httpx
import pytest

from recons_orchestrator.chat import (
    A2AChatClient,
    a2a_result_to_events,
    parse_sse,
)
from recons_orchestrator.models import AgentRecord, AgentSpec, AgentStatus

from tests.auth import build_app, login, make_client


# --- mesh: the orchestrator is a caller ---------------------------------------
def test_mesh_mints_orchestrator_edge_tokens(provisioner, settings):
    provisioner.create_agent(AgentSpec(name="Recon", role="x"))
    tokens = json.loads((settings.shared_dir / "a2a-tokens.json").read_text())
    tok = tokens["orchestrator->recon"]
    env = settings.service_env("recon").read_text()
    assert f"orchestrator:{tok}" in env
    # Stable across rewires; pruned with the agent.
    provisioner.create_agent(AgentSpec(name="Scout", role="x"))
    assert json.loads((settings.shared_dir / "a2a-tokens.json").read_text())["orchestrator->recon"] == tok
    provisioner.remove_agent("recon")
    tokens = json.loads((settings.shared_dir / "a2a-tokens.json").read_text())
    assert "orchestrator->recon" not in tokens
    assert "orchestrator->scout" in tokens


# --- A2A event mapping (VERIFY against the installed Hermes) -------------------
def test_map_message_parts_to_tokens():
    res = {"kind": "message", "role": "agent",
           "parts": [{"kind": "text", "text": "Hello "}, {"kind": "text", "text": "world"}]}
    assert a2a_result_to_events(res) == [
        {"type": "token", "text": "Hello "}, {"type": "token", "text": "world"},
    ]


def test_map_status_update_and_final():
    res = {"kind": "status-update", "final": True,
           "status": {"state": "completed",
                      "message": {"parts": [{"kind": "text", "text": "done."}]}}}
    assert a2a_result_to_events(res) == [{"type": "token", "text": "done."}, {"type": "done"}]


def test_map_input_required_to_approval():
    res = {"kind": "status-update", "taskId": "t-9",
           "status": {"state": "input-required",
                      "message": {"parts": [{"kind": "text", "text": "Send the email?"}]}}}
    ev = a2a_result_to_events(res)
    assert ev[0]["type"] == "approval"
    assert ev[0]["id"] == "t-9"
    assert "Send the email?" in ev[0]["body"]


def test_map_failed_state_to_error():
    res = {"kind": "status-update", "final": True, "status": {"state": "failed"}}
    assert a2a_result_to_events(res) == [{"type": "error", "message": "agent reported failure"},
                                         {"type": "done"}]


def test_parse_sse_frames():
    raw = "event: x\ndata: {\"a\": 1}\n\n: keepalive\n\ndata: {\"b\":\ndata: 2}\n\n"
    assert list(parse_sse(raw.splitlines())) == [{"a": 1}, {"b": 2}]


# --- the A2A client against a mocked transport ---------------------------------
def _agent(port=9900):
    return AgentRecord(id="recon", name="Recon", role="x", a2a_port=port,
                       created_at="2026-08-15T12:00:00+00:00")


async def test_a2a_client_streams_and_authenticates(settings):
    settings.shared_dir.mkdir(parents=True)
    (settings.shared_dir / "a2a-tokens.json").write_text(json.dumps({"orchestrator->recon": "tok-orch"}))
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        body = (
            'data: {"jsonrpc":"2.0","id":1,"result":{"kind":"message","role":"agent",'
            '"parts":[{"kind":"text","text":"Hi"}]}}\n\n'
            'data: {"jsonrpc":"2.0","id":1,"result":{"kind":"status-update","final":true,'
            '"status":{"state":"completed"}}}\n\n'
        )
        return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})

    client = A2AChatClient(settings, transport=httpx.MockTransport(handler))
    events = [e async for e in client.stream(_agent(), "hello there")]
    assert events == [{"type": "token", "text": "Hi"}, {"type": "done"}]
    assert seen["auth"] == "Bearer tok-orch"
    assert seen["url"] == "http://127.0.0.1:9900/"
    assert seen["body"]["method"] == "message/stream"
    assert seen["body"]["params"]["message"]["parts"][0]["text"] == "hello there"


async def test_a2a_client_reports_unreachable_agent(settings):
    settings.shared_dir.mkdir(parents=True)
    (settings.shared_dir / "a2a-tokens.json").write_text(json.dumps({"orchestrator->recon": "tok-orch"}))

    def handler(request):
        raise httpx.ConnectError("refused")

    client = A2AChatClient(settings, transport=httpx.MockTransport(handler))
    events = [e async for e in client.stream(_agent(), "hi")]
    assert events[0]["type"] == "error" and "unreachable" in events[0]["message"]
    assert events[-1] == {"type": "done"}


async def test_a2a_client_missing_token_is_an_error_not_a_crash(settings):
    client = A2AChatClient(settings, transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    events = [e async for e in client.stream(_agent(), "hi")]
    assert events[0]["type"] == "error" and "token" in events[0]["message"]


# --- API -------------------------------------------------------------------------
class FakeChatClient:
    def __init__(self):
        self.calls = []
        self.decisions = []

    async def stream(self, agent, text, *, session_id=None):
        self.calls.append((agent.id, text, session_id))
        yield {"type": "tool_call", "name": "browser_navigate"}
        yield {"type": "token", "text": "On it: " + text}
        yield {"type": "done"}

    async def decide(self, agent, approval_id, decision):
        self.decisions.append((agent.id, approval_id, decision))


@pytest.fixture
def client(settings):
    app = build_app(settings)
    app.state.chat_client = FakeChatClient()
    c = make_client(app)
    login(c)
    c.post("/api/agents", json={"name": "Recon", "role": "x"})
    c.app = app  # type: ignore[attr-defined]
    return c


def _frames(text: str) -> list[dict]:
    return list(parse_sse(text.splitlines()))


def test_chat_streams_sse(client, settings):
    with client.stream("POST", "/api/agents/recon/messages", json={"text": "find suppliers"}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        assert r.headers["cache-control"] == "no-store"
        body = "".join(r.iter_text())
    events = _frames(body)
    assert events[0] == {"type": "tool_call", "name": "browser_navigate"}
    assert events[1]["text"] == "On it: find suppliers"
    assert events[-1] == {"type": "done"}
    assert client.app.state.chat_client.calls == [("recon", "find suppliers", None)]
    # Audited without the message text.
    rows = (settings.root / "audit" / "operator.jsonl").read_text()
    assert '"category": "chat"' in rows
    assert "find suppliers" not in rows


def test_chat_requires_running_agent_and_valid_body(client):
    assert client.post("/api/agents/nope/messages", json={"text": "x"}).status_code == 404
    assert client.post("/api/agents/recon/messages", json={"text": ""}).status_code == 422
    assert client.post("/api/agents/recon/messages", json={"text": "x" * 20_001}).status_code == 422
    client.post("/api/agents/recon/pause")
    assert client.post("/api/agents/recon/messages", json={"text": "x"}).status_code == 409


def test_chat_is_gated_like_everything_else(settings):
    app = build_app(settings)
    app.state.chat_client = FakeChatClient()
    c = make_client(app)
    assert c.post("/api/agents/recon/messages", json={"text": "x"}).status_code == 401


def test_approval_decision_forwarded_and_audited(client, settings):
    r = client.post("/api/agents/recon/approvals/appr-1", json={"decision": "approve"})
    assert r.status_code == 200
    assert client.app.state.chat_client.decisions == [("recon", "appr-1", "approve")]
    assert client.post("/api/agents/recon/approvals/appr-1", json={"decision": "maybe"}).status_code == 422
    rows = (settings.root / "audit" / "operator.jsonl").read_text()
    assert '"action": "approval_approve"' in rows
    assert '"target": "recon/appr-1"' in rows
