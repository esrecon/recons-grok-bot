"""Merged-ledger tests: reads Hermes state.db + a2a_audit.jsonl + cron runs,
merges them chronologically, and filters. This is the audit trail Grok Bot
doesn't ship, so its correctness matters."""

from __future__ import annotations

from recons_orchestrator.config import Settings
from recons_orchestrator.ledger import Ledger

from tests.fixtures import seed_two_agents

BASE = 1_786_000_000


def _ledger(tmp_path) -> Ledger:
    root = tmp_path / "recons"
    seed_two_agents(root, base_ts=BASE)
    return Ledger(Settings(root=root, dashboard_dist=tmp_path / "d"))


def test_collect_merges_all_sources_in_order(tmp_path):
    events = _ledger(tmp_path).collect()
    sources = {e.source for e in events}
    assert {"session", "a2a", "cron"} <= sources

    # Chronological, non-decreasing timestamps.
    ts = [e.ts for e in events]
    assert ts == sorted(ts)


def test_tool_calls_and_results_are_distinct_events(tmp_path):
    events = _ledger(tmp_path).collect()
    kinds = [e.kind for e in events]
    assert "tool_call" in kinds
    assert "tool_result" in kinds
    tool_call = next(e for e in events if e.kind == "tool_call")
    assert tool_call.extra.get("tool") == "browser_navigate"


def test_a2a_pairing_both_directions(tmp_path):
    events = _ledger(tmp_path).collect()
    a2a = [e for e in events if e.source == "a2a"]
    assert len(a2a) == 2
    out = next(e for e in a2a if e.extra.get("direction") == "outbound")
    inb = next(e for e in a2a if e.extra.get("direction") == "inbound")
    assert (out.peer_from, out.peer_to) == ("recon", "scout")
    assert (inb.peer_from, inb.peer_to) == ("scout", "recon")
    # Both halves share the session key so the UI can link them.
    assert out.session_key == inb.session_key == "agent:scout:job42"


def test_filter_a2a_only(tmp_path):
    rows = _ledger(tmp_path).query(a2a_only=True)
    assert rows and all(r["source"] == "a2a" for r in rows)


def test_filter_by_agent(tmp_path):
    rows = _ledger(tmp_path).query(agent="scout")
    assert rows and all(r["agent_id"] == "scout" for r in rows)


def test_search_matches_text_and_peers(tmp_path):
    led = _ledger(tmp_path)
    assert any("Acme" in r["text"] for r in led.query(search="acme"))
    # Search also matches the peer/agent fields.
    assert led.query(search="scout")


def test_missing_state_degrades_to_empty(tmp_path):
    # A roster agent with no on-disk Hermes state must not crash the ledger.
    root = tmp_path / "recons"
    seed_two_agents(root, base_ts=BASE)
    import json

    roster = json.loads((root / "roster.json").read_text())
    roster.append({"id": "ghost", "name": "Ghost", "role": "x", "tier": "bulk",
                   "avatar_color": "#4a4a52", "status": "running", "is_lead": False,
                   "created_at": "2026-08-15T12:00:00+00:00", "a2a_port": 9902})
    (root / "roster.json").write_text(json.dumps(roster))
    led = Ledger(Settings(root=root, dashboard_dist=tmp_path / "d"))
    assert led.collect()  # still returns recon+scout events, no crash
