"""Live chat proxy: browser ⇄ orchestrator (SSE) ⇄ agent (A2A over loopback).

The dashboard never talks to an agent directly. It POSTs a turn to the
orchestrator, which streams Server-Sent Events back (`token`, `tool_call`,
`approval`, `done`, `error` — the shape `apps/dashboard/src/types.ts`
expects) while talking A2A to the agent's loopback port with the
orchestrator's own edge token (minted by mesh.py, stored in
`shared/a2a-tokens.json`, never sent to the browser).

`ChatClient` is the seam: the production adapter below speaks the A2A
JSON-RPC `message/stream` method; tests inject a fake. The exact A2A event
shapes Hermes emits are `VERIFY` points and the mapping is deliberately
tolerant — unknown events are skipped, never fatal.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Iterable, Iterator, Protocol

import httpx

from .config import Settings
from .mesh import ORCHESTRATOR_ID
from .models import AgentRecord

ChatEvent = dict[str, Any]


class ChatClient(Protocol):
    def stream(
        self, agent: AgentRecord, text: str, *, session_id: str | None = None
    ) -> AsyncIterator[ChatEvent]: ...

    async def decide(self, agent: AgentRecord, approval_id: str, decision: str) -> None: ...


# --- SSE parsing ----------------------------------------------------------------
class SSEParser:
    """Incremental `data:` frame parser (multi-line data, comments ignored)."""

    def __init__(self) -> None:
        self._data: list[str] = []

    def feed(self, line: str) -> list[dict]:
        line = line.rstrip("\r\n")
        if line == "":
            return self.flush()
        if line.startswith(":"):
            return []
        if line.startswith("data:"):
            payload = line[5:]
            if payload.startswith(" "):
                payload = payload[1:]
            self._data.append(payload)
        return []

    def flush(self) -> list[dict]:
        if not self._data:
            return []
        raw = "\n".join(self._data)
        self._data = []
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return [obj] if isinstance(obj, dict) else []


def parse_sse(lines: Iterable[str]) -> Iterator[dict]:
    parser = SSEParser()
    for line in lines:
        yield from parser.feed(line)
    yield from parser.flush()


# --- A2A → dashboard events (VERIFY against the installed Hermes) ---------------
def _texts(parts: Any) -> list[str]:
    out: list[str] = []
    if not isinstance(parts, list):
        return out
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("kind", "text") == "text" and isinstance(part.get("text"), str):
            out.append(part["text"])
    return out


def a2a_result_to_events(result: Any) -> list[ChatEvent]:
    """Map one JSON-RPC `result` object from `message/stream` to chat events."""
    if not isinstance(result, dict):
        return []
    events: list[ChatEvent] = []
    kind = result.get("kind")

    if kind == "message" or ("parts" in result and "role" in result):
        events.extend({"type": "token", "text": t} for t in _texts(result.get("parts")))
        return events

    if kind == "artifact-update":
        artifact = result.get("artifact") or {}
        events.extend({"type": "token", "text": t} for t in _texts(artifact.get("parts")))
        return events

    if kind == "status-update" or kind == "task" or "status" in result:
        status = result.get("status") or {}
        state = str(status.get("state") or "").lower()
        message = status.get("message") or {}
        texts = _texts(message.get("parts"))
        if state == "input-required":
            events.append({
                "type": "approval",
                "id": str(result.get("taskId") or result.get("id") or "task"),
                "title": "Needs your approval",
                "body": " ".join(texts) or "The agent is waiting for your decision.",
                "kind": "input",
            })
        elif state == "failed":
            events.append({"type": "error", "message": "agent reported failure"})
        else:
            events.extend({"type": "token", "text": t} for t in texts)
        if result.get("final") or state in ("completed", "failed", "canceled", "rejected"):
            events.append({"type": "done"})
        return events

    if "error" in result and isinstance(result["error"], dict):
        events.append({"type": "error", "message": str(result["error"].get("message") or "agent error")})
    return events


# --- production adapter ----------------------------------------------------------
class A2AChatClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 180.0,
    ) -> None:
        self._s = settings
        self._transport = transport
        self._timeout = timeout

    @property
    def _token_store(self) -> Path:
        return self._s.shared_dir / "a2a-tokens.json"

    def _token(self, agent_id: str) -> str | None:
        if not self._token_store.exists():
            return None
        try:
            tokens = json.loads(self._token_store.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        tok = tokens.get(f"{ORCHESTRATOR_ID}->{agent_id}") if isinstance(tokens, dict) else None
        return str(tok) if tok else None

    @staticmethod
    def _url(agent: AgentRecord) -> str:
        return f"http://127.0.0.1:{agent.a2a_port}/"  # loopback only, by construction

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self._transport,
            timeout=httpx.Timeout(self._timeout, connect=5.0),
        )

    @staticmethod
    def _message(text: str, session_id: str | None) -> dict[str, Any]:
        msg: dict[str, Any] = {
            "role": "user",
            "messageId": uuid.uuid4().hex,
            "parts": [{"kind": "text", "text": text}],
        }
        if session_id:
            msg["contextId"] = session_id  # VERIFY: A2A contextId ≈ Hermes session
        return msg

    async def stream(
        self, agent: AgentRecord, text: str, *, session_id: str | None = None
    ) -> AsyncIterator[ChatEvent]:
        token = self._token(agent.id)
        if not token:
            yield {"type": "error",
                   "message": "no orchestrator A2A token for this agent — re-run provisioning"}
            yield {"type": "done"}
            return
        payload = {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": "message/stream",  # VERIFY A2A method name for the Hermes version
            "params": {"message": self._message(text, session_id)},
        }
        headers = {"authorization": f"Bearer {token}", "accept": "text/event-stream"}
        done = False
        try:
            async with self._client() as http:
                async with http.stream("POST", self._url(agent), json=payload, headers=headers) as resp:
                    if resp.status_code != 200:
                        yield {"type": "error", "message": f"agent answered HTTP {resp.status_code}"}
                        yield {"type": "done"}
                        return
                    if "text/event-stream" in resp.headers.get("content-type", ""):
                        parser = SSEParser()
                        async for line in resp.aiter_lines():
                            for frame in parser.feed(line):
                                for ev in self._frame_events(frame):
                                    done = done or ev["type"] == "done"
                                    yield ev
                        for frame in parser.flush():
                            for ev in self._frame_events(frame):
                                done = done or ev["type"] == "done"
                                yield ev
                    else:
                        body = await resp.aread()
                        try:
                            frame = json.loads(body)
                        except json.JSONDecodeError:
                            frame = {}
                        for ev in self._frame_events(frame):
                            done = done or ev["type"] == "done"
                            yield ev
        except httpx.HTTPError as exc:
            yield {"type": "error", "message": f"agent unreachable ({type(exc).__name__})"}
            yield {"type": "done"}
            return
        if not done:
            yield {"type": "done"}

    @staticmethod
    def _frame_events(frame: Any) -> list[ChatEvent]:
        if not isinstance(frame, dict):
            return []
        if "result" in frame:
            return a2a_result_to_events(frame["result"])
        if "error" in frame:
            err = frame["error"] if isinstance(frame["error"], dict) else {}
            return [{"type": "error", "message": str(err.get("message") or "agent error")},
                    {"type": "done"}]
        return a2a_result_to_events(frame)

    async def decide(self, agent: AgentRecord, approval_id: str, decision: str) -> None:
        """Forward an approve/deny to the agent's pending prompt.

        VERIFY: Hermes approval semantics over A2A. This sends the decision as
        a follow-up message (`/approve <id>` | `/deny <id>`) via `message/send`.
        """
        token = self._token(agent.id)
        if not token:
            raise RuntimeError("no orchestrator A2A token for this agent")
        payload = {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": "message/send",
            "params": {"message": self._message(f"/{decision} {approval_id}", None)},
        }
        async with self._client() as http:
            resp = await http.post(self._url(agent), json=payload,
                                   headers={"authorization": f"Bearer {token}"})
            resp.raise_for_status()
