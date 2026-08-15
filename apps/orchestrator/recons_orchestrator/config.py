"""Orchestrator settings and the model-tier → Hermes-provider mapping.

Everything here is data, not behaviour, so it stays easy to test and easy for
Tony to audit. Paths default to the on-VPS layout but are overridable via env
(the test-suite points them at a temp dir).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# --- Ports (all loopback; published only via `tailscale serve`, see docs/15) ---
ORCHESTRATOR_PORT = 8330
A2A_PORT_BASE = 9900  # each agent gets A2A_PORT_BASE + slot


# --- Model tiers ---------------------------------------------------------------
# Maps the dashboard's friendly tier to a Hermes provider/model plus a fallback
# chain. Model ids are marked VERIFY: confirm against the provider before relying
# on an exact string — providers rev these often.
@dataclass(frozen=True)
class TierConfig:
    provider: str
    model: str
    fallbacks: tuple[str, ...] = ()


TIERS: dict[str, TierConfig] = {
    # Claude via claude-code-openai-wrapper (custom OpenAI-compatible provider
    # "claude_wrapper"). Reserved for the lead agent. Falls back to the GPT
    # subscription if the wrapper is down. VERIFY model id against the wrapper.
    "lead": TierConfig(
        provider="claude_wrapper",
        model="claude-sonnet-4-6",  # VERIFY
        fallbacks=("openai/gpt-5.6-sol",),  # VERIFY
    ),
    # ChatGPT/Codex subscription — the everyday workhorse (officially blessed
    # for third-party agents, see docs/40). Falls back to cheap Nous.
    "workhorse": TierConfig(
        provider="openai",
        model="gpt-5.6-sol",  # VERIFY
        fallbacks=("nous/hermes-4-70b",),  # VERIFY
    ),
    # Nous Portal — cheap bulk. Falls back to the GPT subscription.
    "bulk": TierConfig(
        provider="nous",
        model="hermes-4-70b",  # VERIFY
        fallbacks=("openai/gpt-5.6-sol",),  # VERIFY
    ),
}


@dataclass
class Settings:
    """Filesystem + service layout the provisioner operates on."""

    root: Path = field(default_factory=lambda: Path(os.environ.get("RECONS_ROOT", "/opt/recons")))
    # Where the built dashboard SPA is served from (Phase 3+).
    dashboard_dist: Path = field(
        default_factory=lambda: Path(
            os.environ.get("RECONS_DASHBOARD_DIST", "/opt/recons/dashboard")
        )
    )
    # systemd template unit; %i is the agent id.
    service_template: str = os.environ.get("RECONS_SERVICE_TEMPLATE", "hermes-gateway@")
    # Shared outbound-webhook secret name (the value lives in shared/secrets.env).
    webhook_secret_env: str = "RECONS_WEBHOOK_SECRET"
    # Orchestrator's own webhook receiver URL that each profile posts events to.
    webhook_receiver_url: str = os.environ.get(
        "RECONS_WEBHOOK_URL", f"http://127.0.0.1:{ORCHESTRATOR_PORT}/api/hooks"
    )

    @property
    def agents_dir(self) -> Path:
        return self.root / "agents"

    @property
    def shared_dir(self) -> Path:
        return self.root / "shared"

    @property
    def shared_skills_dir(self) -> Path:
        return self.shared_dir / "skills"

    @property
    def shared_secrets_env(self) -> Path:
        return self.shared_dir / "secrets.env"

    @property
    def roster_path(self) -> Path:
        return self.root / "roster.json"

    def home_dir(self, agent_id: str) -> Path:
        """HERMES_HOME for one agent."""
        return self.agents_dir / agent_id / "home"

    def service_env(self, agent_id: str) -> Path:
        """Per-agent EnvironmentFile the systemd unit loads (HERMES_HOME, A2A tokens)."""
        return self.agents_dir / agent_id / "service.env"

    def unit_name(self, agent_id: str) -> str:
        return f"{self.service_template}{agent_id}.service"
