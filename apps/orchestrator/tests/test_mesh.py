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
