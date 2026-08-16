"""A2A peer-mesh wiring.

Grok Bot lets any bot message any other bot. We reproduce that with a full A2A
mesh: for every ordered pair (caller, target) there is a unique bearer secret,
so each directed edge is independently authenticated, rate-limited and audited.

When the roster changes, `rewire` regenerates:
  * each agent's `config.yaml` (its outbound `a2a_agents` list — the peers it may
    call, with `${A2A_TOKEN_<PEER>}` placeholders), and
  * each agent's `service.env` (the actual secrets: its inbound `A2A_PEER_TOKENS`
    for callers, plus the `A2A_TOKEN_<PEER>` values it uses to call out).

Secrets only ever land in `service.env` (chmod 600), never in `config.yaml`.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Callable

import yaml
from jinja2 import Environment, PackageLoader, select_autoescape

from .config import Settings, TierConfig, TIERS, inherited_model
from .models import AgentRecord, ModelTier
from .telegram import load_tokens as load_telegram_tokens

TokenFactory = Callable[[], str]


class MeshError(RuntimeError):
    """The roster demands something the shared stores cannot provide.

    Raised fail-loud from rewire (e.g. telegram enabled with no stored bot
    token). The API paths store the token before flipping the flag, so this
    only fires on hand-mangled state — where the boot rewire's blanket
    exception handler will log it and leave that agent's files stale.
    """


def _default_token() -> str:
    return secrets.token_urlsafe(32)


def _env_key(agent_id: str) -> str:
    """`recon-2` -> `RECON_2`, matching the config.yaml placeholder convention."""
    return agent_id.upper().replace("-", "_")


class Mesh:
    def __init__(
        self,
        settings: Settings,
        token_factory: TokenFactory = _default_token,
    ) -> None:
        self._s = settings
        self._new_token = token_factory
        self._jinja = Environment(
            loader=PackageLoader("recons_orchestrator", "templates"),
            autoescape=select_autoescape(enabled_extensions=()),
            keep_trailing_newline=True,
            trim_blocks=False,
            lstrip_blocks=False,
        )

    # -- edge-token store ------------------------------------------------------
    # Directed-edge tokens persist in shared/a2a-tokens.json so that rewiring is
    # stable: an existing edge keeps its token across roster changes, and only
    # genuinely new edges mint new secrets.
    @property
    def _token_store(self) -> Path:
        return self._s.shared_dir / "a2a-tokens.json"

    def _load_tokens(self) -> dict[str, str]:
        import json

        if not self._token_store.exists():
            return {}
        return json.loads(self._token_store.read_text("utf-8"))

    def _save_tokens(self, tokens: dict[str, str]) -> None:
        import json
        import os

        self._token_store.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._token_store.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(tokens, indent=2, sort_keys=True), "utf-8")
        os.replace(tmp, self._token_store)
        self._token_store.chmod(0o600)

    @staticmethod
    def _edge(caller: str, target: str) -> str:
        return f"{caller}->{target}"

    def _edge_token(self, tokens: dict[str, str], caller: str, target: str) -> str:
        key = self._edge(caller, target)
        if key not in tokens:
            tokens[key] = self._new_token()
        return tokens[key]

    # -- rewiring --------------------------------------------------------------
    def rewire(self, records: list[AgentRecord]) -> None:
        """Regenerate config.yaml + service.env for every agent in the roster.

        Prunes tokens for edges that no longer exist, then rewrites each agent's
        files against the current peer set. Idempotent: same roster in → same
        files out (aside from freshly minted tokens for new edges).
        """
        tokens = self._load_tokens()
        ids = {r.id for r in records}

        # Prune stale edges (agents that were deleted).
        for edge in list(tokens):
            caller, target = edge.split("->", 1)
            if caller not in ids or target not in ids:
                del tokens[edge]

        # Ensure every directed edge in the full mesh has a token.
        for caller in ids:
            for target in ids:
                if caller != target:
                    self._edge_token(tokens, caller, target)

        self._save_tokens(tokens)

        by_id = {r.id: r for r in records}
        for rec in records:
            # Never rewrite an imported agent's files. It arrived with a working
            # config we don't own, and clobbering it would break the agent
            # outside this dashboard. Wiring one into the mesh is a deliberate,
            # manual step (docs/50-agents-a2a.md).
            if rec.imported:
                continue
            peers = [by_id[p] for p in sorted(ids) if p != rec.id]
            self._write_config(rec, peers)
            self._write_service_env(rec, peers, tokens)

    def _write_config(self, record: AgentRecord, peers: list[AgentRecord]) -> None:
        home = self._s.home_dir(record.id)
        home.mkdir(parents=True, exist_ok=True)
        tier = TIERS[record.tier.value]
        if record.model_provider and record.model_name:
            # Promotion captured the provider/model this agent arrived with —
            # an adopted agent keeps its own brain, not the tier constants.
            tier = TierConfig(provider=record.model_provider, model=record.model_name)
        elif record.tier is ModelTier.WORKHORSE:
            # Workhorse means "the subscription this machine is signed into" —
            # inherit the provider/model Hermes itself wrote to the default
            # home's config, falling back to the constants only without one.
            tier = inherited_model() or tier
        rendered = self._jinja.get_template("config.yaml.j2").render(
            record=record,
            tier=tier,
            peers=peers,
            shared_skills_dir=str(self._s.shared_skills_dir),
            webhook_receiver_url=self._s.webhook_receiver_url,
            webhook_secret_env=self._s.webhook_secret_env,
        )
        # Fail loudly if the template ever emits invalid YAML.
        yaml.safe_load(rendered)
        (home / "config.yaml").write_text(rendered, "utf-8")

    def _write_service_env(
        self, record: AgentRecord, peers: list[AgentRecord], tokens: dict[str, str]
    ) -> None:
        lines = [
            f"# Per-agent environment for {record.name} ({record.id}) — GENERATED, chmod 600.",
            f"HERMES_HOME={self._s.home_dir(record.id)}",
            f"A2A_HOST=127.0.0.1",
            f"A2A_PORT={record.a2a_port}",
        ]
        # Inbound: which callers may reach me, and with what token.
        peer_tokens = ",".join(
            f"{p.id}:{tokens[self._edge(p.id, record.id)]}" for p in peers
        )
        lines.append(f"A2A_PEER_TOKENS={peer_tokens}")
        # Outbound: the token I present when calling each peer.
        for p in peers:
            lines.append(f"A2A_TOKEN_{_env_key(p.id)}={tokens[self._edge(record.id, p.id)]}")

        # Telegram gateway: config.yaml carries only ${TELEGRAM_BOT_TOKEN};
        # the real value is re-emitted here from the shared store on every
        # rewire, and the allowlist is the env mechanism docs/30 documents.
        if record.telegram_enabled:
            tg_token = load_telegram_tokens(self._s).get(record.id, "")
            if not tg_token:
                raise MeshError(
                    f"{record.id}: telegram is enabled but no bot token is "
                    "stored in shared/telegram-tokens.json — set one via "
                    f"PUT /api/agents/{record.id}/telegram or the promote flow"
                )
            lines.append(f"TELEGRAM_BOT_TOKEN={tg_token}")
            lines.append(f"TELEGRAM_ALLOWED_USERS={record.telegram_allowed_users}")

        path = self._s.service_env(record.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", "utf-8")
        path.chmod(0o600)
