"""The merged audit ledger — the feature Grok Bot itself doesn't ship.

Hermes stores agent activity in several places and never merges them:
  * per-agent SQLite `state.db` — `sessions` + `messages` (with tool calls)
  * per-agent `a2a_audit.jsonl` — every agent-to-agent exchange, appended
  * per-agent `cron/executions.db` — routine run history
  * live signed webhooks — lifecycle events POSTed to the orchestrator
  * the orchestrator's own `audit/operator.jsonl` — what the human did

This module reads all of them read-only, normalizes each into one `LedgerEvent`
shape, and merges them into a single chronological, filterable timeline.

The exact Hermes column names are version-fragile (see docs/00 VERIFY policy), so
the SQLite reader introspects each table with PRAGMA and maps known aliases
rather than assuming a fixed schema. Missing tables/columns degrade to empty
rather than crashing.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import Settings
from .roster import Roster


@dataclass
class LedgerEvent:
    ts: float  # normalized epoch seconds for sorting
    ts_iso: str
    seq: int
    source: str  # session | a2a | cron | webhook | operator
    agent_id: str
    kind: str  # message | tool_call | tool_result | a2a | cron_run | lifecycle
    session_id: str | None = None
    session_key: str | None = None
    role: str | None = None
    peer_from: str | None = None
    peer_to: str | None = None
    text: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


# --- timestamp normalization --------------------------------------------------
def _norm_ts(value: Any) -> tuple[float, str]:
    """Accept epoch seconds, epoch millis, or ISO-8601; return (epoch, iso)."""
    if value is None:
        return (0.0, "")
    if isinstance(value, (int, float)):
        secs = float(value)
        if secs > 1e12:  # milliseconds
            secs /= 1000.0
        return (secs, datetime.fromtimestamp(secs, tz=timezone.utc).isoformat())
    s = str(value).strip()
    if not s:
        return (0.0, "")
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (dt.timestamp(), dt.isoformat())
    except ValueError:
        # Numeric-in-a-string?
        try:
            return _norm_ts(float(s))
        except ValueError:
            return (0.0, s)


def _first(row: dict[str, Any], *names: str) -> Any:
    for n in names:
        if n in row and row[n] is not None:
            return row[n]
    return None


# --- SQLite helpers -----------------------------------------------------------
def _open_ro(db_path: Path) -> sqlite3.Connection | None:
    if not db_path.exists():
        return None
    # Immutable/RO URI so we never take a write lock on the agent's live DB.
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    conn.row_factory = sqlite3.Row
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.Error:
        return set()
    return {r["name"] for r in rows}


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


# --- readers ------------------------------------------------------------------
def read_state_db(agent_id: str, db_path: Path) -> list[LedgerEvent]:
    """Read sessions + messages from a Hermes state.db (best-effort, tolerant)."""
    conn = _open_ro(db_path)
    if conn is None:
        return []
    events: list[LedgerEvent] = []
    try:
        if not _has_table(conn, "messages"):
            return []
        # Map session_id -> session_key for A2A pairing / grouping.
        session_keys: dict[str, str] = {}
        if _has_table(conn, "sessions"):
            scols = _table_columns(conn, "sessions")
            for row in conn.execute("SELECT * FROM sessions"):
                r = dict(row)
                sid = str(_first(r, "id", "session_id") or "")
                skey = _first(r, "session_key", "sessionKey", "key")
                if sid and skey:
                    session_keys[sid] = str(skey)
            _ = scols  # columns introspected for tolerance; not otherwise needed

        mcols = _table_columns(conn, "messages")
        order = "created_at" if "created_at" in mcols else (
            "ts" if "ts" in mcols else ("rowid")
        )
        for i, row in enumerate(conn.execute(f"SELECT * FROM messages ORDER BY {order} ASC")):
            r = dict(row)
            sid = str(_first(r, "session_id", "sessionId", "session") or "")
            ts, ts_iso = _norm_ts(_first(r, "created_at", "ts", "timestamp", "time"))
            role = _first(r, "role", "author")
            content = _first(r, "content", "text", "message") or ""
            tool_name = _first(r, "tool_name", "toolName")
            tool_calls_raw = _first(r, "tool_calls", "toolCalls")
            skey = session_keys.get(sid)

            # A tool result message.
            if tool_name and (role in (None, "tool") or _first(r, "tool_call_id", "toolCallId")):
                events.append(LedgerEvent(
                    ts=ts, ts_iso=ts_iso, seq=i, source="session", agent_id=agent_id,
                    kind="tool_result", session_id=sid, session_key=skey,
                    role="tool", text=str(content)[:4000],
                    extra={"tool": tool_name},
                ))
                continue

            # A normal message; emit any tool calls it carries as separate events.
            events.append(LedgerEvent(
                ts=ts, ts_iso=ts_iso, seq=i, source="session", agent_id=agent_id,
                kind="message", session_id=sid, session_key=skey,
                role=str(role) if role else None, text=str(content)[:8000],
            ))
            for tc in _parse_tool_calls(tool_calls_raw):
                events.append(LedgerEvent(
                    ts=ts, ts_iso=ts_iso, seq=i, source="session", agent_id=agent_id,
                    kind="tool_call", session_id=sid, session_key=skey,
                    role=str(role) if role else None,
                    text=tc.get("name", "tool"),
                    extra={"tool": tc.get("name"), "args": tc.get("args")},
                ))
    finally:
        conn.close()
    return events


def _parse_tool_calls(raw: Any) -> list[dict[str, Any]]:
    if not raw:
        return []
    data = raw
    if isinstance(raw, (str, bytes)):
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return []
    if isinstance(data, dict):
        data = [data]
    out: list[dict[str, Any]] = []
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            fn = item.get("function") if isinstance(item.get("function"), dict) else item
            out.append({"name": fn.get("name") or item.get("name") or "tool",
                        "args": fn.get("arguments") or item.get("args")})
    return out


def read_a2a_audit(agent_id: str, jsonl_path: Path) -> list[LedgerEvent]:
    """Read the append-only a2a_audit.jsonl — one agent-to-agent exchange per line."""
    if not jsonl_path.exists():
        return []
    events: list[LedgerEvent] = []
    for i, line in enumerate(jsonl_path.read_text("utf-8").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts, ts_iso = _norm_ts(_first(rec, "ts", "timestamp", "time", "created_at"))
        events.append(LedgerEvent(
            ts=ts, ts_iso=ts_iso, seq=i, source="a2a", agent_id=agent_id,
            kind="a2a", session_key=_first(rec, "key", "session_key"),
            peer_from=_first(rec, "from", "caller", "src"),
            peer_to=_first(rec, "to", "target", "dst"),
            text=str(_first(rec, "message", "text", "content", "summary") or "")[:8000],
            extra={"direction": _first(rec, "direction", "dir"),
                   "status": _first(rec, "status")},
        ))
    return events


def read_cron_db(agent_id: str, db_path: Path) -> list[LedgerEvent]:
    conn = _open_ro(db_path)
    if conn is None:
        return []
    events: list[LedgerEvent] = []
    try:
        table = next((t for t in ("executions", "runs", "cron_executions")
                      if _has_table(conn, t)), None)
        if not table:
            return []
        for i, row in enumerate(conn.execute(f"SELECT * FROM {table}")):
            r = dict(row)
            ts, ts_iso = _norm_ts(_first(r, "created_at", "ts", "started_at", "time"))
            events.append(LedgerEvent(
                ts=ts, ts_iso=ts_iso, seq=i, source="cron", agent_id=agent_id,
                kind="cron_run",
                text=str(_first(r, "job_id", "job", "name") or "routine"),
                extra={"status": _first(r, "status", "result")},
            ))
    finally:
        conn.close()
    return events


@dataclass
class SessionSummary:
    """One conversation (a Hermes session) as the Sessions view lists it."""

    agent_id: str
    session_id: str
    session_key: str | None
    started_ts: float
    last_ts: float
    started_at: str
    last_at: str
    message_count: int
    tool_calls: int
    preview: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


# --- the ledger ---------------------------------------------------------------
class Ledger:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._roster = Roster(settings.roster_path)

    @property
    def _webhook_store(self) -> Path:
        return self._s.root / "audit" / "webhooks.jsonl"

    def _read_webhooks(self) -> list[LedgerEvent]:
        path = self._webhook_store
        if not path.exists():
            return []
        events: list[LedgerEvent] = []
        for i, line in enumerate(path.read_text("utf-8").splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts, ts_iso = _norm_ts(_first(rec, "ts", "timestamp", "time"))
            events.append(LedgerEvent(
                ts=ts, ts_iso=ts_iso, seq=i, source="webhook",
                agent_id=str(_first(rec, "agent", "agent_id") or "unknown"),
                kind="lifecycle",
                session_id=_first(rec, "session", "session_id"),
                text=str(_first(rec, "event", "type") or "event"),
                extra={k: v for k, v in rec.items()
                       if k not in {"ts", "timestamp", "time", "agent", "agent_id", "event", "type"}},
            ))
        return events

    @property
    def _operator_store(self) -> Path:
        return self._s.root / "audit" / "operator.jsonl"

    def _read_operator(self) -> list[LedgerEvent]:
        path = self._operator_store
        if not path.exists():
            return []
        events: list[LedgerEvent] = []
        for i, line in enumerate(path.read_text("utf-8").splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts, ts_iso = _norm_ts(_first(rec, "ts"))
            extra = {k: v for k, v in rec.items()
                     if k not in {"ts", "ts_iso", "category", "action", "target"}}
            events.append(LedgerEvent(
                ts=ts, ts_iso=ts_iso, seq=i, source="operator", agent_id="orchestrator",
                kind=str(rec.get("category") or "operator"),
                role=str(rec.get("actor")) if rec.get("actor") else None,
                text=f"{rec.get('action', '')} {rec.get('target', '')}".strip(),
                extra=extra,
            ))
        return events

    def collect(self) -> list[LedgerEvent]:
        events: list[LedgerEvent] = []
        for rec in self._roster.load():
            home = self._s.home_dir(rec.id)
            events += read_state_db(rec.id, home / "state.db")
            events += read_a2a_audit(rec.id, home / "a2a_audit.jsonl")
            events += read_cron_db(rec.id, home / "cron" / "executions.db")
        events += self._read_webhooks()
        events += self._read_operator()
        # Stable chronological order; seq breaks ties within a source/agent.
        events.sort(key=lambda e: (e.ts, e.source, e.agent_id, e.seq))
        return events

    def query(
        self,
        *,
        agent: str | None = None,
        source: str | None = None,
        kind: str | None = None,
        a2a_only: bool = False,
        search: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        rows = self.collect()
        needle = search.lower() if search else None

        def keep(e: LedgerEvent) -> bool:
            if agent and e.agent_id != agent:
                return False
            if source and e.source != source:
                return False
            if kind and e.kind != kind:
                return False
            if a2a_only and e.source != "a2a":
                return False
            if since is not None and e.ts < since:
                return False
            if until is not None and e.ts > until:
                return False
            if needle and needle not in e.text.lower() and needle not in (
                f"{e.peer_from or ''} {e.peer_to or ''} {e.agent_id}".lower()
            ):
                return False
            return True

        filtered = [e for e in rows if keep(e)]
        window = filtered[offset : offset + limit]
        return [e.to_json() for e in window]

    def agents(self) -> list[str]:
        return [r.id for r in self._roster.load()]

    # -- sessions (conversations) ----------------------------------------------
    def sessions(self, *, agent: str | None = None) -> list[SessionSummary]:
        groups: dict[tuple[str, str], list[LedgerEvent]] = {}
        for e in self.collect():
            if e.source != "session" or not e.session_id:
                continue
            if agent and e.agent_id != agent:
                continue
            groups.setdefault((e.agent_id, e.session_id), []).append(e)
        out: list[SessionSummary] = []
        for (agent_id, session_id), events in groups.items():
            messages = [e for e in events if e.kind == "message"]
            first_user = next((e for e in messages if (e.role or "").lower() == "user"), None)
            preview_src = first_user or (messages[0] if messages else None)
            preview = (preview_src.text if preview_src else "").strip().replace("\n", " ")[:140]
            out.append(SessionSummary(
                agent_id=agent_id,
                session_id=session_id,
                session_key=next((e.session_key for e in events if e.session_key), None),
                started_ts=events[0].ts,
                last_ts=events[-1].ts,
                started_at=events[0].ts_iso,
                last_at=events[-1].ts_iso,
                message_count=len(messages),
                tool_calls=sum(1 for e in events if e.kind == "tool_call"),
                preview=preview,
            ))
        out.sort(key=lambda s: (-s.last_ts, s.agent_id, s.session_id))
        return out

    def session_events(self, agent_id: str, session_id: str) -> list[dict[str, Any]]:
        return [
            e.to_json() for e in self.collect()
            if e.source == "session" and e.agent_id == agent_id and e.session_id == session_id
        ]


def events_to_jsonl(events: Iterable[LedgerEvent]) -> str:
    return "\n".join(json.dumps(e.to_json()) for e in events)
