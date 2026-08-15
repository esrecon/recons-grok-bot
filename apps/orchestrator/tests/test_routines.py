"""Routine (cron) store tests."""

from __future__ import annotations

import json

import pytest

from recons_orchestrator.config import Settings
from recons_orchestrator.routines import RoutineStore


@pytest.fixture
def settings(tmp_path) -> Settings:
    root = tmp_path / "recons"
    root.mkdir()
    (root / "roster.json").write_text(json.dumps([
        {"id": "clerk", "name": "Clerk", "role": "x", "tier": "bulk",
         "avatar_color": "#4a4a52", "status": "running", "is_lead": False,
         "created_at": "2026-08-15T12:00:00+00:00", "a2a_port": 9900}
    ]))
    return Settings(root=root, dashboard_dist=tmp_path / "d")


def test_create_and_list(settings):
    store = RoutineStore(settings)
    r = store.create("clerk", "every weekday at 8:00am", "Send the morning brief", deliver="telegram")
    assert r.id == "routine-1"
    assert r.enabled is True
    rows = store.list_all()
    assert len(rows) == 1
    assert rows[0].instruction == "Send the morning brief"
    assert rows[0].deliver == "telegram"


def test_create_unknown_agent_rejected(settings):
    store = RoutineStore(settings)
    with pytest.raises(ValueError):
        store.create("ghost", "daily", "x")


def test_enable_pause(settings):
    store = RoutineStore(settings)
    store.create("clerk", "daily", "x")
    paused = store.set_enabled("clerk", "routine-1", False)
    assert paused.enabled is False
    resumed = store.set_enabled("clerk", "routine-1", True)
    assert resumed.enabled is True


def test_delete(settings):
    store = RoutineStore(settings)
    store.create("clerk", "daily", "x")
    store.delete("clerk", "routine-1")
    assert store.list_all() == []
    with pytest.raises(KeyError):
        store.delete("clerk", "routine-1")


def test_ids_unique_after_delete(settings):
    store = RoutineStore(settings)
    store.create("clerk", "daily", "a")
    store.create("clerk", "daily", "b")
    store.delete("clerk", "routine-1")
    # Next create must not collide with the surviving routine-2.
    r = store.create("clerk", "daily", "c")
    assert r.id not in {"routine-2"}
    ids = {x.id for x in store.list_all()}
    assert len(ids) == 2


def test_tolerates_jobs_wrapped_object(settings):
    # Some Hermes versions may store {"jobs": [...]} rather than a bare list.
    path = settings.home_dir("clerk") / "cron" / "jobs.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"jobs": [
        {"id": "j1", "cron": "0 9 * * 1-5", "prompt": "brief", "enabled": True}
    ]}))
    rows = RoutineStore(settings).list_for("clerk")
    assert rows[0].id == "j1"
    assert rows[0].schedule == "0 9 * * 1-5"
    assert rows[0].instruction == "brief"
