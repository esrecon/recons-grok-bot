"""Sessions view: the ledger's per-agent state.db events grouped into
conversations, listable across agents and readable as a transcript."""

from __future__ import annotations

import pytest

from recons_orchestrator.config import Settings
from recons_orchestrator.ledger import Ledger

from tests.auth import build_app, login, make_client, with_operator
from tests.fixtures import seed_two_agents


@pytest.fixture
def settings(tmp_path):
    root = tmp_path / "recons"
    seed_two_agents(root)
    return with_operator(Settings(root=root, dashboard_dist=tmp_path / "d"))


def test_sessions_are_grouped_and_summarised(settings):
    sessions = Ledger(settings).sessions()
    assert {(s.agent_id, s.session_id) for s in sessions} == {
        ("recon", "recon-sess-1"), ("scout", "scout-sess-1"),
    }
    recon = next(s for s in sessions if s.agent_id == "recon")
    assert recon.session_key == "agent:recon:main"
    assert recon.message_count == 3
    assert recon.tool_calls == 1
    assert recon.preview == "Find three suppliers"
    assert recon.started_ts < recon.last_ts
    assert recon.started_at and recon.last_at


def test_sessions_filter_by_agent_and_sorted_latest_first(settings):
    ledger = Ledger(settings)
    assert [s.agent_id for s in ledger.sessions(agent="scout")] == ["scout"]
    allsess = ledger.sessions()
    assert allsess == sorted(allsess, key=lambda s: s.last_ts, reverse=True)


def test_session_transcript(settings):
    events = Ledger(settings).session_events("recon", "recon-sess-1")
    assert [e["kind"] for e in events] == ["message", "message", "tool_call", "tool_result", "message"]
    assert events[0]["role"] == "user"
    assert Ledger(settings).session_events("recon", "nope") == []


@pytest.fixture
def client(settings):
    c = make_client(build_app(settings))
    login(c)
    return c


def test_sessions_api(client):
    body = client.get("/api/sessions").json()
    assert len(body["sessions"]) == 2
    body = client.get("/api/sessions", params={"agent": "scout"}).json()
    assert [s["agent_id"] for s in body["sessions"]] == ["scout"]

    detail = client.get("/api/sessions/recon/recon-sess-1").json()
    assert detail["session"]["session_id"] == "recon-sess-1"
    assert detail["events"][0]["text"] == "Find three suppliers"
    assert client.get("/api/sessions/recon/nope").status_code == 404
    assert client.get("/api/sessions/nobody/x").status_code == 404
