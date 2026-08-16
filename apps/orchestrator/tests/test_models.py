"""Model-level guards: slug safety and spec validation."""

from __future__ import annotations

import pytest

from recons_orchestrator.models import AgentSpec, ModelTier, slugify


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Recon", "recon"),
        ("Front Desk", "front-desk"),
        ("Bot #2!", "bot-2"),
        ("  Spaced  Out  ", "spaced-out"),
        # Not reserved: the default Hermes profile is discovered under exactly
        # this name, and it must be importable (see models._RESERVED comment).
        ("Hermes", "hermes"),
    ],
)
def test_slugify(name, expected):
    assert slugify(name) == expected


@pytest.mark.parametrize("bad", ["", "!!!", "   ", "shared", "roster", "all"])
def test_slugify_rejects(bad):
    with pytest.raises(ValueError):
        slugify(bad)


def test_spec_defaults_to_workhorse():
    spec = AgentSpec(name="Scout", role="Research")
    assert spec.tier is ModelTier.WORKHORSE
    assert spec.avatar_color == "#6b7280"


def test_spec_rejects_bad_color():
    with pytest.raises(ValueError):
        AgentSpec(name="Scout", role="x", avatar_color="blue")


def test_spec_rejects_symbol_only_name():
    with pytest.raises(ValueError):
        AgentSpec(name="###", role="x")


def test_pre_telegram_roster_records_load_with_defaults():
    """roster.json written before the telegram/model fields existed must keep
    loading — rollback safety for the orchestrator itself."""
    from recons_orchestrator.models import AgentRecord

    rec = AgentRecord.model_validate(
        {
            "id": "recon",
            "name": "Recon",
            "role": "Lead",
            "a2a_port": 9900,
            "created_at": "2026-08-15T12:00:00+00:00",
        }
    )
    assert rec.telegram_enabled is False
    assert rec.telegram_allowed_users == ""
    assert rec.model_provider is None and rec.model_name is None


def test_record_refuses_telegram_without_numeric_allowlist():
    from recons_orchestrator.models import AgentRecord

    base = {
        "id": "comms",
        "name": "Comms",
        "role": "x",
        "a2a_port": 9900,
        "created_at": "2026-08-15T12:00:00+00:00",
        "telegram_enabled": True,
    }
    with pytest.raises(ValueError):
        AgentRecord.model_validate(base)
    with pytest.raises(ValueError):
        AgentRecord.model_validate({**base, "telegram_allowed_users": "me@x"})
    ok = AgentRecord.model_validate({**base, "telegram_allowed_users": "42, 43"})
    assert ok.telegram_allowed_users == "42,43"
