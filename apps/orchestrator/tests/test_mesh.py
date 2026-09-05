"""A2A mesh tests: the security-critical wiring that lets agents talk while
keeping each directed edge independently authenticated. Secrets must live only
in service.env, never in config.yaml."""

from __future__ import annotations

import yaml

from recons_orchestrator.models import AgentSpec


def _make(provisioner, name):
    return provisioner.create_agent(AgentSpec(name=name, role="x"))


def _read_env(path):
    out = {}
    for line in path.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


def test_full_mesh_every_agent_can_reach_every_other(provisioner, settings):
    for n in ("Recon", "Scout", "Clerk"):
        _make(provisioner, n)

    ids = {"recon", "scout", "clerk"}
    for aid in ids:
        cfg = yaml.safe_load((settings.home_dir(aid) / "config.yaml").read_text())
        peers = set((cfg.get("a2a_agents") or {}).keys())
        assert peers == ids - {aid}


def test_config_contains_no_raw_tokens_only_placeholders(provisioner, settings):
    _make(provisioner, "Recon")
    _make(provisioner, "Scout")
    raw = (settings.home_dir("recon") / "config.yaml").read_text()
    # Placeholder form, not the secret itself.
    assert "${A2A_TOKEN_SCOUT}" in raw
    # None of the actual secret values (which live in service.env) leak into config.
    secret_values = [v for k, v in _read_env(settings.service_env("recon")).items()
                     if k.startswith("A2A_TOKEN_") or k == "A2A_PEER_TOKENS"]
    for blob in secret_values:
        for value in blob.replace("recon:", "").replace("scout:", "").split(","):
            assert value and value not in raw


def test_service_env_has_directed_edge_tokens(provisioner, settings):
    _make(provisioner, "Recon")
    _make(provisioner, "Scout")

    recon_env = _read_env(settings.service_env("recon"))
    scout_env = _read_env(settings.service_env("scout"))

    # Recon's HERMES_HOME + A2A port present.
    assert recon_env["HERMES_HOME"].endswith("/agents/recon/home")
    assert recon_env["A2A_HOST"] == "127.0.0.1"

    # Recon calls Scout with the recon->scout token; Scout's inbound peer-token
    # map contains the SAME token under caller id 'recon'.
    recon_to_scout = recon_env["A2A_TOKEN_SCOUT"]
    assert f"recon:{recon_to_scout}" in scout_env["A2A_PEER_TOKENS"]

    # And the reverse edge is a DIFFERENT secret.
    scout_to_recon = scout_env["A2A_TOKEN_RECON"]
    assert scout_to_recon != recon_to_scout
    assert f"scout:{scout_to_recon}" in recon_env["A2A_PEER_TOKENS"]


def test_service_env_is_chmod_600(provisioner, settings):
    _make(provisioner, "Recon")
    _make(provisioner, "Scout")
    mode = settings.service_env("recon").stat().st_mode & 0o777
    assert mode == 0o600


def test_edge_tokens_stable_across_rewire(provisioner, settings):
    _make(provisioner, "Recon")
    _make(provisioner, "Scout")
    before = _read_env(settings.service_env("recon"))["A2A_TOKEN_SCOUT"]
    # Adding a third agent rewires everyone; the existing recon->scout edge must
    # keep its token.
    _make(provisioner, "Clerk")
    after = _read_env(settings.service_env("recon"))["A2A_TOKEN_SCOUT"]
    assert before == after


def test_removed_agent_tokens_pruned(provisioner, settings):
    import json

    _make(provisioner, "Recon")
    _make(provisioner, "Scout")
    provisioner.remove_agent("scout")
    tokens = json.loads((settings.shared_dir / "a2a-tokens.json").read_text())
    assert all("scout" not in edge for edge in tokens)


# --- outbound A2A toolset rendering --------------------------------------------
# Hermes ships the "a2a" toolset default-off; the gateway.platforms.a2a block
# alone only serves inbound. platform_toolsets must therefore restate each
# conversational platform's composite plus "a2a" — and must NOT configure the
# "a2a" platform itself (its auto composite is registry-generated; an explicit
# list there would silently drop the core tools).
def test_outbound_a2a_toolset_enabled_for_cli_platform(provisioner, settings):
    _make(provisioner, "Recon")
    _make(provisioner, "Scout")
    for aid in ("recon", "scout"):
        cfg = yaml.safe_load((settings.home_dir(aid) / "config.yaml").read_text())
        pts = cfg["platform_toolsets"]
        assert pts["cli"] == ["hermes-cli", "a2a"]
        assert "a2a" not in pts  # never an explicit a2a-platform entry
        assert "telegram" not in pts  # only rendered for telegram agents


def test_outbound_a2a_toolset_follows_telegram_enablement(provisioner, settings):
    from recons_orchestrator import telegram as tg

    _make(provisioner, "Recon")
    _make(provisioner, "Comms")
    tg.store_token(settings, "comms", "12345678:tg-secret-value")
    provisioner.set_telegram("comms", enabled=True, allowed_users="42")

    cfg = yaml.safe_load((settings.home_dir("comms") / "config.yaml").read_text())
    assert cfg["platform_toolsets"]["telegram"] == ["hermes-telegram", "a2a"]

    provisioner.set_telegram("comms", enabled=False, allowed_users="")
    cfg = yaml.safe_load((settings.home_dir("comms") / "config.yaml").read_text())
    assert "telegram" not in cfg["platform_toolsets"]


# --- operator config extras ---------------------------------------------------
def test_config_extras_are_appended_and_survive_rewire(provisioner, settings):
    _make(provisioner, "Recon")
    settings.config_extras("recon").write_text(
        "mcp:\n  servers:\n    obsidian:\n      command: npx\n"
    )

    _make(provisioner, "Scout")  # roster change → full rewire

    cfg = yaml.safe_load((settings.home_dir("recon") / "config.yaml").read_text())
    assert cfg["mcp"]["servers"]["obsidian"]["command"] == "npx"

    _make(provisioner, "Clerk")  # and again — regeneration keeps them
    cfg = yaml.safe_load((settings.home_dir("recon") / "config.yaml").read_text())
    assert "mcp" in cfg


def test_absent_or_empty_extras_change_nothing(provisioner, settings):
    from recons_orchestrator.mesh import Mesh

    _make(provisioner, "Recon")
    before = (settings.home_dir("recon") / "config.yaml").read_bytes()

    settings.config_extras("recon").write_text("\n\n")
    Mesh(settings, token_factory=lambda: "T").rewire(provisioner.list_agents())

    assert (settings.home_dir("recon") / "config.yaml").read_bytes() == before


def test_invalid_extras_yaml_fails_loud(provisioner, settings):
    import pytest

    from recons_orchestrator.mesh import Mesh, MeshError

    _make(provisioner, "Recon")
    settings.config_extras("recon").write_text(": not yaml [")

    with pytest.raises(MeshError, match="config-extras"):
        Mesh(settings, token_factory=lambda: "T").rewire(provisioner.list_agents())


# --- telegram rendering -------------------------------------------------------
def test_telegram_disabled_renders_no_block(provisioner, settings):
    _make(provisioner, "Recon")
    cfg = yaml.safe_load((settings.home_dir("recon") / "config.yaml").read_text())
    assert "telegram" not in cfg["gateway"]["platforms"]
    assert "TELEGRAM_BOT_TOKEN" not in settings.service_env("recon").read_text()


def test_telegram_enabled_placeholder_in_config_secret_only_in_env(
    provisioner, settings
):
    from recons_orchestrator import telegram as tg

    _make(provisioner, "Recon")
    _make(provisioner, "Comms")
    tg.store_token(settings, "comms", "12345678:real-secret")
    provisioner.set_telegram("comms", enabled=True, allowed_users="42")

    raw = (settings.home_dir("comms") / "config.yaml").read_text()
    assert '"${TELEGRAM_BOT_TOKEN}"' in raw
    assert "12345678:real-secret" not in raw
    cfg = yaml.safe_load(raw)  # the block keeps the file valid YAML
    assert cfg["gateway"]["platforms"]["telegram"]["enabled"] is True

    env = _read_env(settings.service_env("comms"))
    assert env["TELEGRAM_BOT_TOKEN"] == "12345678:real-secret"
    assert env["TELEGRAM_ALLOWED_USERS"] == "42"


def test_telegram_enabled_without_stored_token_fails_loud(
    settings, token_factory
):
    import pytest

    from recons_orchestrator.mesh import Mesh, MeshError
    from recons_orchestrator.models import AgentRecord

    rec = AgentRecord(
        id="comms", name="Comms", role="x", a2a_port=9900,
        created_at="2026-08-15T12:00:00+00:00",
        telegram_enabled=True, telegram_allowed_users="42",
    )
    with pytest.raises(MeshError, match="no bot token"):
        Mesh(settings, token_factory=token_factory).rewire([rec])
