"""Operator audit: what the human did through the dashboard.

Appends one JSON line per action to `<root>/audit/operator.jsonl`; the ledger
merges it as source `operator` so logins, credential changes, skill approvals,
routine edits and agent lifecycle actions sit in the same timeline as what the
agents did. Credential events record *only* actor, key, provider, action,
timestamp and result — there is no code path that can put a value here.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .config import Settings

CATEGORIES = frozenset({"auth", "credential", "skill", "routine", "agent", "chat"})


class OperatorAudit:
    def __init__(self, settings: Settings, *, clock: Callable[[], float] = time.time) -> None:
        self._s = settings
        self._clock = clock

    @property
    def path(self) -> Path:
        return self._s.root / "audit" / "operator.jsonl"

    def _append(self, row: dict[str, Any]) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=False) + "\n")
        return row

    def record(
        self,
        *,
        actor: str,
        category: str,
        action: str,
        target: str,
        result: str = "ok",
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if category not in CATEGORIES:
            raise ValueError(f"unknown audit category {category!r}")
        if category == "credential":
            raise ValueError("use credential() so only the allowed fields are recorded")
        ts = self._clock()
        row: dict[str, Any] = {
            "ts": ts,
            "ts_iso": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
            "actor": actor,
            "category": category,
            "action": action,
            "target": target,
            "result": result,
        }
        if extra:
            row["extra"] = extra
        return self._append(row)

    def credential(self, *, actor: str, key: str, action: str, result: str) -> dict[str, Any]:
        """The credential-change event. Fixed field set by design: no values."""
        from .credentials import provider_for_key

        if action not in ("created", "replaced", "removed"):
            raise ValueError(f"unknown credential action {action!r}")
        ts = self._clock()
        provider = provider_for_key(key)
        return self._append({
            "ts": ts,
            "ts_iso": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
            "actor": actor,
            "category": "credential",
            "action": action,
            "target": key,
            "provider": provider.id if provider else None,
            "result": result,
        })
