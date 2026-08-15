"""Routines — per-agent scheduled/triggered automations (Hermes cron).

Hermes stores cron jobs per agent in `HERMES_HOME/cron/jobs.json` and run
history in `cron/executions.db` (the ledger already surfaces the history). This
module reads and writes the jobs store so the dashboard can list, create, enable/
pause and delete routines across all agents.

NOTE (VERIFY): writing jobs.json is the documented store, but a running gateway
may need a reload to pick up changes made out-of-band. The deployment runbook
(docs) covers triggering that; for a paused/one-shot agent the file write is
sufficient. Creating via the gateway's Jobs API is the alternative and is noted
inline for when the gateway is live.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings
from .roster import Roster


@dataclass
class Routine:
    id: str
    agent: str
    schedule: str
    instruction: str
    enabled: bool = True
    deliver: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent": self.agent,
            "schedule": self.schedule,
            "instruction": self.instruction,
            "enabled": self.enabled,
            "deliver": self.deliver,
        }


class RoutineStore:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._roster = Roster(settings.roster_path)

    def _jobs_path(self, agent_id: str) -> Path:
        return self._s.home_dir(agent_id) / "cron" / "jobs.json"

    def _load_jobs(self, agent_id: str) -> list[dict[str, Any]]:
        path = self._jobs_path(agent_id)
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text("utf-8"))
        except json.JSONDecodeError:
            return []
        # Tolerate either a bare list or {"jobs": [...]}.
        if isinstance(data, dict):
            data = data.get("jobs", [])
        return data if isinstance(data, list) else []

    def _save_jobs(self, agent_id: str, jobs: list[dict[str, Any]]) -> None:
        path = self._jobs_path(agent_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(jobs, indent=2), "utf-8")
        import os

        os.replace(tmp, path)

    @staticmethod
    def _to_routine(agent_id: str, job: dict[str, Any]) -> Routine:
        return Routine(
            id=str(job.get("id") or job.get("job_id") or ""),
            agent=agent_id,
            schedule=str(job.get("schedule") or job.get("cron") or ""),
            instruction=str(job.get("instruction") or job.get("prompt") or ""),
            enabled=bool(job.get("enabled", True)),
            deliver=job.get("deliver"),
        )

    def list_all(self) -> list[Routine]:
        out: list[Routine] = []
        for rec in self._roster.load():
            for job in self._load_jobs(rec.id):
                if isinstance(job, dict):
                    out.append(self._to_routine(rec.id, job))
        return out

    def list_for(self, agent_id: str) -> list[Routine]:
        return [self._to_routine(agent_id, j) for j in self._load_jobs(agent_id)]

    def create(self, agent_id: str, schedule: str, instruction: str,
               deliver: str | None = None) -> Routine:
        if self._roster.get(agent_id) is None:
            raise ValueError(f"no such agent: {agent_id}")
        jobs = self._load_jobs(agent_id)
        # Deterministic id from the current count; unique within the agent.
        existing_ids = {str(j.get("id")) for j in jobs if isinstance(j, dict)}
        n = len(jobs) + 1
        rid = f"routine-{n}"
        while rid in existing_ids:
            n += 1
            rid = f"routine-{n}"
        job = {"id": rid, "schedule": schedule, "instruction": instruction,
               "enabled": True, "deliver": deliver}
        jobs.append(job)
        self._save_jobs(agent_id, jobs)
        return self._to_routine(agent_id, job)

    def set_enabled(self, agent_id: str, routine_id: str, enabled: bool) -> Routine:
        jobs = self._load_jobs(agent_id)
        for j in jobs:
            if isinstance(j, dict) and str(j.get("id")) == routine_id:
                j["enabled"] = enabled
                self._save_jobs(agent_id, jobs)
                return self._to_routine(agent_id, j)
        raise KeyError(f"no routine '{routine_id}' for agent '{agent_id}'")

    def delete(self, agent_id: str, routine_id: str) -> None:
        jobs = self._load_jobs(agent_id)
        remaining = [j for j in jobs if not (isinstance(j, dict) and str(j.get("id")) == routine_id)]
        if len(remaining) == len(jobs):
            raise KeyError(f"no routine '{routine_id}' for agent '{agent_id}'")
        self._save_jobs(agent_id, remaining)
