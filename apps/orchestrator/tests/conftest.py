"""Shared fixtures. Everything runs against a temp root; no systemd, no network."""

from __future__ import annotations

import itertools
from datetime import datetime, timezone
from pathlib import Path

import pytest

from recons_orchestrator.config import Settings
from recons_orchestrator.provisioning import Provisioner
from recons_orchestrator.services import RecordingServiceManager


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    from tests.auth import with_operator

    return with_operator(Settings(root=tmp_path / "recons", dashboard_dist=tmp_path / "dash"))


@pytest.fixture
def services() -> RecordingServiceManager:
    return RecordingServiceManager()


@pytest.fixture
def fixed_clock():
    # Deterministic timestamps so roster output is reproducible.
    def clock() -> datetime:
        return datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc)

    return clock


@pytest.fixture
def token_factory():
    # Predictable A2A tokens: tok1, tok2, ... so edge wiring is assertable.
    counter = itertools.count(1)
    return lambda: f"tok{next(counter)}"


@pytest.fixture
def provisioner(settings, services, fixed_clock, token_factory) -> Provisioner:
    return Provisioner(
        settings,
        services=services,
        clock=fixed_clock,
        token_factory=token_factory,
    )
