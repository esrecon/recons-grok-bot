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
