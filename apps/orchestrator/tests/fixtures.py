"""Synthetic Hermes state for ledger tests.

Builds a realistic-enough `~/.hermes`-style tree for a set of agents: a
`state.db` with sessions + messages (including tool calls), an `a2a_audit.jsonl`
with an agent-to-agent exchange, and a cron `executions.db`. Column names follow
the documented Hermes schema; the ledger reader is tolerant of variations.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def make_state_db(path: Path, *, agent: str, base_ts: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            source TEXT,
            model TEXT,
            session_key TEXT,
            parent_session_id TEXT,
            created_at INTEGER
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY,
            session_id TEXT,
            role TEXT,
            content TEXT,
            tool_calls TEXT,
            tool_name TEXT,
            tool_call_id TEXT,
            created_at INTEGER
        );
        """
    )
    sid = f"{agent}-sess-1"
    conn.execute(
        "INSERT INTO sessions (id, source, model, session_key, created_at) VALUES (?,?,?,?,?)",
        (sid, "cli", "gpt-5.6-sol", f"agent:{agent}:main", base_ts),
    )
    rows = [
        (1, sid, "user", "Find three suppliers", None, None, None, base_ts + 1),
        (
            2, sid, "assistant", "Searching now.",
            json.dumps([{"function": {"name": "browser_navigate", "arguments": {"url": "x"}}}]),
            None, None, base_ts + 2,
        ),
        (3, sid, "tool", "OK 200", None, "browser_navigate", "call_1", base_ts + 3),
        (4, sid, "assistant", "Here are three suppliers.", None, None, None, base_ts + 4),
    ]
    conn.executemany(
        "INSERT INTO messages (id, session_id, role, content, tool_calls, tool_name, tool_call_id, created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


def make_a2a_audit(path: Path, *, base_ts: int, frm: str, to: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        {"ts": base_ts + 5, "from": frm, "to": to, "key": f"agent:{to}:job42",
         "direction": "outbound", "message": "Please price these three suppliers.",
         "status": "sent"},
        {"ts": base_ts + 9, "from": to, "to": frm, "key": f"agent:{to}:job42",
         "direction": "inbound", "message": "Priced. Cheapest is Acme.", "status": "ok"},
    ]
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", "utf-8")


def make_cron_db(path: Path, *, base_ts: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE executions (id INTEGER PRIMARY KEY, job_id TEXT, status TEXT, created_at INTEGER)"
    )
    conn.execute(
        "INSERT INTO executions (job_id, status, created_at) VALUES (?,?,?)",
        ("morning-brief", "ok", base_ts + 6),
    )
    conn.commit()
    conn.close()


def seed_two_agents(root: Path, *, base_ts: int = 1_786_000_000) -> None:
    """Populate a temp $RECONS_ROOT with recon + scout Hermes state and a roster."""
    roster = [
        {"id": "recon", "name": "Recon", "role": "Lead", "tier": "lead",
         "avatar_color": "#8b5cf6", "status": "running", "is_lead": True,
         "created_at": "2026-08-15T12:00:00+00:00", "a2a_port": 9900},
        {"id": "scout", "name": "Scout", "role": "Research", "tier": "workhorse",
         "avatar_color": "#3b82f6", "status": "running", "is_lead": False,
         "created_at": "2026-08-15T12:00:00+00:00", "a2a_port": 9901},
    ]
    (root).mkdir(parents=True, exist_ok=True)
    (root / "roster.json").write_text(json.dumps(roster), "utf-8")

    for a in ("recon", "scout"):
        home = root / "agents" / a / "home"
        make_state_db(home / "state.db", agent=a, base_ts=base_ts)
        make_cron_db(home / "cron" / "executions.db", base_ts=base_ts)
    # A2A exchange recorded on the caller (recon) side.
    make_a2a_audit(root / "agents" / "recon" / "home" / "a2a_audit.jsonl",
                   base_ts=base_ts, frm="recon", to="scout")
