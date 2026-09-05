"""Domain models shared across the orchestrator."""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, Field, field_validator

# Model tiers map to a provider/model pair in config.py. The dashboard offers
# these as friendly labels; the provisioner turns them into Hermes provider
# config. "workhorse" (GPT subscription) is the default for new agents so that
# Claude usage stays shaped like ordinary individual use (see docs/40).
class ModelTier(str, Enum):
    LEAD = "lead"            # Claude via wrapper — reserved for the lead agent
    WORKHORSE = "workhorse"  # ChatGPT/Codex subscription — the everyday default
    BULK = "bulk"            # Nous Portal — cheap, high-volume work


class AgentStatus(str, Enum):
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"


_SLUG_RE = re.compile(r"[^a-z0-9]+")
_RESERVED = frozenset({"shared", "pending", "orchestrator", "roster", "all", "new", "api", "hermes"})


def slugify(name: str) -> str:
    """Turn a display name into a filesystem- and systemd-safe agent id.

    systemd template instances (`hermes-gateway@<id>`) and HERMES_HOME paths
    both need a restricted charset, so we collapse to [a-z0-9-].
    """
    slug = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    if not slug:
        raise ValueError("name must contain at least one alphanumeric character")
    if slug in _RESERVED:
        raise ValueError(f"'{slug}' is a reserved name")
    return slug


class AgentSpec(BaseModel):
    """What the dashboard's 'New agent' modal submits."""

    name: str = Field(min_length=1, max_length=48)
    role: str = Field(min_length=1, max_length=120, description="One-line job description")
    personality: str = Field(default="", max_length=2000)
    tier: ModelTier = ModelTier.WORKHORSE
    avatar_color: str = Field(default="#6b7280")

    @field_validator("avatar_color")
    @classmethod
    def _check_color(cls, v: str) -> str:
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", v):
            raise ValueError("avatar_color must be a #rrggbb hex string")
        return v.lower()

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        # Validate that a usable slug can be derived, surfacing the error early.
        slugify(v)
        return v.strip()


class AgentRecord(BaseModel):
    """Persistent roster metadata for one provisioned agent."""

    id: str
    name: str
    role: str
    personality: str = ""
    tier: ModelTier = ModelTier.WORKHORSE
    avatar_color: str = "#6b7280"
    a2a_port: int
    status: AgentStatus = AgentStatus.RUNNING
    is_lead: bool = False
    created_at: str  # ISO-8601; injected so provisioning stays deterministic in tests
