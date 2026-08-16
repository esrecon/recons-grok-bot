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
    # "claude_wrapper"). Reserved for the lead agent. VERIFY both ids.
    "lead": TierConfig(
        provider="claude_wrapper",
        model="claude-sonnet-4-6",  # VERIFY
    ),
    # ChatGPT/Codex subscription — the everyday workhorse. Both values verified
    # verbatim from a live v0.20.1 Codex sign-in, which prints
    # "Config updated: ... (model.provider=openai-codex)" and
    # "Default model set to: gpt-5.6-terra". Used only when the default home's
    # own config can't be inherited (see inherited_model()).
    "workhorse": TierConfig(
        provider="openai-codex",
        model="gpt-5.6-terra",
    ),
    # Nous Portal — cheap bulk. VERIFY both ids ('hermes model' lists them).
    "bulk": TierConfig(
        provider="nous",
        model="hermes-4-70b",  # VERIFY
    ),
}


def inherited_model() -> TierConfig | None:
    """The provider/model the default HERMES_HOME is actually configured with.

    Hermes's own sign-in flows write working values into the default home's
    config.yaml (e.g. model.provider=openai-codex). Inheriting those beats any
    constant in this file: a hardcoded guess of "openai" once made every
    workhorse agent fail with "Unknown provider", while the correct id had been
    sitting in that config all along.
    """
    import yaml

    default_home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    try:
        data = yaml.safe_load((default_home / "config.yaml").read_text("utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    model = (data or {}).get("model") or {}
    provider = model.get("provider")
    default = model.get("default") or model.get("model")
    if provider and default:
        return TierConfig(provider=str(provider), model=str(default))
    return None


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
