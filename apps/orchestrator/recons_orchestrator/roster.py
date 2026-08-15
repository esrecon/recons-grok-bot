"""The roster store — the source of truth for which agents exist.

A single JSON file (`<root>/roster.json`) holds one AgentRecord per agent.
Writes are atomic (temp file + rename) so a crash mid-provision never leaves a
half-written roster. Deliberately tiny and dependency-free: for a handful of
agents this is simpler and more transparent than a database.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .models import AgentRecord


class Roster:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> list[AgentRecord]:
        if not self._path.exists():
            return []
        raw = json.loads(self._path.read_text("utf-8"))
        return [AgentRecord.model_validate(r) for r in raw]

    def save(self, records: list[AgentRecord]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            [r.model_dump(mode="json") for r in records], indent=2, sort_keys=False
        )
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(payload, "utf-8")
        os.replace(tmp, self._path)  # atomic on POSIX

    def get(self, agent_id: str) -> AgentRecord | None:
        return next((r for r in self.load() if r.id == agent_id), None)

    def upsert(self, record: AgentRecord) -> None:
        records = self.load()
        for i, r in enumerate(records):
            if r.id == record.id:
                records[i] = record
                break
        else:
            records.append(record)
        self.save(records)

    def remove(self, agent_id: str) -> None:
        self.save([r for r in self.load() if r.id != agent_id])

    def next_a2a_port(self, base: int) -> int:
        """Lowest free port at or above `base`, filling gaps left by deletions."""
        used = {r.a2a_port for r in self.load()}
        port = base
        while port in used:
            port += 1
        return port
