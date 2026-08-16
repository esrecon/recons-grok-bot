"""Promote/demote — the cutover core.

An imported snapshot becomes the managed head of staff: joins the mesh, gets
the template-managed Telegram gateway, keeps the model it arrived with — and
nothing is STARTED (the cutover script owns the moment polling begins).
Demote rolls all of it back to the imported state."""

from __future__ import annotations

import pytest
import yaml

from recons_orchestrator.models import AgentSpec, AgentStatus
from recons_orchestrator.provisioning import ImportedAgentError, ProvisioningError


def _home(tmp_path, *, telegram_cfg=True, custom_key=True):
    home = tmp_path / ".hermes"
    home.mkdir(parents=True, exist_ok=True)
    cfg = "model:\n  provider: nous\n  default: hermes-4-405b\n"
    if telegram_cfg:
        cfg += (
            "gateway:\n"
            "  platforms:\n"
            "    telegram:\n"
            "      enabled: true\n"
            "      extra:\n"
            "        bot_token: '123456789:from-old-box'\n"
        )
    if custom_key:
        cfg += "my_custom_tweak: 7\n"
    (home / "config.yaml").write_text(cfg)
    (home / "SOUL.md").write_text("# Hermes\n\nMine.\n")
    return home


def test_promote_wires_mesh_telegram_and_keeps_model(
    provisioner, settings, services, tmp_path
):
    provisioner.create_agent(AgentSpec(name="Scout", role="Research"))
    home = _home(tmp_path)
    provisioner.import_agent(home, name="Hermes")
    services.calls.clear()

    report = provisioner.promote_agent(
        "hermes", telegram_enabled=True, telegram_allowed_users="42", make_lead=True
    )

    rec = report.record
    assert rec.imported is False and rec.home is None
    assert rec.is_lead is True
    assert rec.status is AgentStatus.PAUSED
    assert report.telegram_token_source == "extracted"
    assert report.model_captured is True
    assert "my_custom_tweak" in report.captured_config_keys

    # Home relocated into the canonical layout, original config archived.
    new_home = settings.home_dir("hermes")
    assert (new_home / "SOUL.md").exists()
    assert (new_home / "config.yaml.pre-promote.bak").exists()
    assert not home.exists()

    cfg = yaml.safe_load((new_home / "config.yaml").read_text())
    assert cfg["model"]["provider"] == "nous"
    assert cfg["model"]["default"] == "hermes-4-405b"  # kept its own brain
    assert "scout" in cfg["a2a_agents"]  # in the mesh now
    assert cfg["gateway"]["platforms"]["telegram"]["enabled"] is True
    # Custom config was CAPTURED, not dropped: it lives in config-extras.yaml
    # and still renders into the generated config.
    assert cfg["my_custom_tweak"] == 7
    assert settings.config_extras("hermes").is_file()
    assert report.extras_path == str(settings.config_extras("hermes"))

    env = settings.service_env("hermes").read_text()
    assert "TELEGRAM_BOT_TOKEN=123456789:from-old-box" in env
    assert "TELEGRAM_ALLOWED_USERS=42" in env
    # Placeholder-only in the config; the secret never leaks there.
    raw = (new_home / "config.yaml").read_text()
    assert "${TELEGRAM_BOT_TOKEN}" in raw
    assert "123456789:from-old-box" not in raw

    # Scout can now reach hermes and was restarted to load that config;
    # hermes itself was NOT started.
    scout_cfg = yaml.safe_load(
        (settings.home_dir("scout") / "config.yaml").read_text()
    )
    assert "hermes" in scout_cfg["a2a_agents"]
    assert ("restart", settings.unit_name("scout")) in services.calls
    assert not any("hermes-gateway@hermes" in unit for _, unit in services.calls)

    # Managed now → the SOUL gains the head-of-staff contract, non-destructively.
    soul = (new_home / "SOUL.md").read_text()
    assert soul.startswith("# Hermes\n\nMine.\n")
    assert "You are the **head of staff**" in soul


def test_promote_requires_imported(provisioner):
    provisioner.create_agent(AgentSpec(name="Scout", role="x"))
    with pytest.raises(ProvisioningError, match="not an imported agent"):
        provisioner.promote_agent("scout")


def test_promote_telegram_without_any_token_fails_before_mutation(
    provisioner, tmp_path
):
    home = _home(tmp_path, telegram_cfg=False)
    provisioner.import_agent(home, name="Hermes")

    with pytest.raises(ProvisioningError, match="bot token"):
        provisioner.promote_agent(
            "hermes", telegram_enabled=True, telegram_allowed_users="42"
        )

    rec = provisioner.get_agent("hermes")
    assert rec is not None and rec.imported is True  # untouched
    assert home.exists()


def test_promote_provided_token_beats_extraction(provisioner, settings, tmp_path):
    home = _home(tmp_path)
    provisioner.import_agent(home, name="Hermes")

    report = provisioner.promote_agent(
        "hermes",
        telegram_enabled=True,
        telegram_allowed_users="42",
        telegram_token="987654321:pasted",
    )

    assert report.telegram_token_source == "provided"
    assert "987654321:pasted" in settings.service_env("hermes").read_text()


def test_promote_rejects_a_non_numeric_allowlist(provisioner, tmp_path):
    home = _home(tmp_path)
    provisioner.import_agent(home, name="Hermes")
    with pytest.raises(ValueError, match="numeric"):
        provisioner.promote_agent(
            "hermes", telegram_enabled=True, telegram_allowed_users="not-a-number"
        )


def test_promote_without_telegram_still_joins_the_mesh(provisioner, settings, tmp_path):
    home = _home(tmp_path, telegram_cfg=False)
    provisioner.create_agent(AgentSpec(name="Scout", role="x"))
    provisioner.import_agent(home, name="Hermes")

    report = provisioner.promote_agent("hermes")

    assert report.record.telegram_enabled is False
    cfg = yaml.safe_load((settings.home_dir("hermes") / "config.yaml").read_text())
    assert "telegram" not in cfg["gateway"]["platforms"]
    env = settings.service_env("hermes").read_text()
    assert "TELEGRAM_BOT_TOKEN" not in env
    assert "A2A_PEER_TOKENS" in env


def test_promote_never_clobbers_an_existing_extras_file(
    provisioner, settings, tmp_path
):
    home = _home(tmp_path, telegram_cfg=False)
    provisioner.import_agent(home, name="Hermes")
    extras = settings.config_extras("hermes")
    extras.parent.mkdir(parents=True, exist_ok=True)
    extras.write_text("my_pre_edit: true\n")

    report = provisioner.promote_agent("hermes")

    assert extras.read_text() == "my_pre_edit: true\n"  # user's file untouched
    cfg = yaml.safe_load((settings.home_dir("hermes") / "config.yaml").read_text())
    assert cfg["my_pre_edit"] is True  # rendered from the user's file
    assert "my_custom_tweak" in report.captured_config_keys  # still reported
    assert "my_custom_tweak" not in cfg  # but never written over user's edits


def test_promote_rewrites_migrated_data_paths_in_captured_config(
    provisioner, settings, tmp_path
):
    import json

    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "config.yaml").write_text(
        "model:\n  provider: nous\n  default: hermes-4-405b\n"
        "mcp:\n  servers:\n    obsidian:\n"
        "      args: ['/root/obsidian-memory-vault']\n"
    )
    (home / "SOUL.md").write_text("# Hermes\n")
    provisioner.import_agent(home, name="Hermes")

    local_vault = settings.agents_dir / "hermes" / "data" / "obsidian-memory-vault"
    stamp = settings.agents_dir / "hermes" / ".migrate-hermes.json"
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(
        json.dumps(
            {
                "also_paths": [
                    {"remote": "/root/obsidian-memory-vault", "local": str(local_vault)}
                ]
            }
        )
    )

    provisioner.promote_agent("hermes")

    cfg = yaml.safe_load((settings.home_dir("hermes") / "config.yaml").read_text())
    assert cfg["mcp"]["servers"]["obsidian"]["args"] == [str(local_vault)]


def test_demote_restores_the_snapshot_state(provisioner, settings, services, tmp_path):
    provisioner.create_agent(AgentSpec(name="Scout", role="x"))
    home = _home(tmp_path)
    provisioner.import_agent(home, name="Hermes")
    original_cfg = (home / "config.yaml").read_text()
    provisioner.promote_agent(
        "hermes", telegram_enabled=True, telegram_allowed_users="42", make_lead=True
    )
    services.calls.clear()

    rec = provisioner.demote_agent("hermes")

    assert rec.imported is True
    assert rec.telegram_enabled is False
    new_home = settings.home_dir("hermes")
    assert rec.home == str(new_home)
    assert (new_home / "config.yaml").read_text() == original_cfg
    assert not settings.service_env("hermes").exists()  # fails closed again
    assert ("disable", settings.unit_name("hermes")) in services.calls
    # The badge went back to a managed agent that can actually route.
    assert [a.id for a in provisioner.list_agents() if a.is_lead] == ["scout"]


def test_demote_requires_a_prior_promotion(provisioner):
    provisioner.create_agent(AgentSpec(name="Scout", role="x"))
    with pytest.raises(ProvisioningError, match="never promoted"):
        provisioner.demote_agent("scout")


def test_set_telegram_on_imported_is_refused(provisioner, tmp_path):
    home = _home(tmp_path)
    provisioner.import_agent(home, name="Hermes")
    with pytest.raises(ImportedAgentError):
        provisioner.set_telegram(
            "hermes", enabled=True, allowed_users="42", token="1:x"
        )


def test_set_telegram_managed_lifecycle(provisioner, settings, services):
    provisioner.create_agent(AgentSpec(name="Comms", role="Messaging"))
    services.calls.clear()

    rec = provisioner.set_telegram(
        "comms", enabled=True, allowed_users="42, 43", token="111:tok"
    )

    assert rec.telegram_enabled is True
    assert rec.telegram_allowed_users == "42,43"  # normalised
    env = settings.service_env("comms").read_text()
    assert "TELEGRAM_BOT_TOKEN=111:tok" in env
    assert "TELEGRAM_ALLOWED_USERS=42,43" in env
    assert ("restart", settings.unit_name("comms")) in services.calls

    rec = provisioner.set_telegram("comms", enabled=False)
    assert rec.telegram_enabled is False
    assert "TELEGRAM_BOT_TOKEN" not in settings.service_env("comms").read_text()


def test_set_telegram_enable_without_token_fails(provisioner):
    provisioner.create_agent(AgentSpec(name="Comms", role="x"))
    with pytest.raises(ProvisioningError, match="no bot token"):
        provisioner.set_telegram("comms", enabled=True, allowed_users="42")
