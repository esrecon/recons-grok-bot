"""The managed "Your team" SOUL section: fenced, idempotent, consent-gated.

The promise under test: everything OUTSIDE the fence is the user's and is
byte-preserved; imported homes are written only after the explicit operator
action of making that agent head of staff."""

from __future__ import annotations

from recons_orchestrator import team
from recons_orchestrator.models import AgentSpec


def _spec(name: str, role: str = "Do the thing") -> AgentSpec:
    return AgentSpec(name=name, role=role)


def _soul(settings, agent_id: str) -> str:
    return (settings.home_dir(agent_id) / "SOUL.md").read_text()


def _make_home(path, *, name="Hermes", soul=True):
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.yaml").write_text(
        "model:\n  provider: nous\n  default: hermes-4-70b\n"
    )
    if soul:
        (path / "SOUL.md").write_text(f"# {name}\n\nMine, all mine.\n")
    return path


def test_new_agents_get_the_fenced_team_section(provisioner, settings):
    provisioner.create_agent(_spec("Recon", "Lead assistant"))
    provisioner.create_agent(_spec("Scout", "Research"))

    recon, scout = _soul(settings, "recon"), _soul(settings, "scout")
    assert team.TEAM_BEGIN in recon and team.TEAM_END in recon
    assert "Scout" in recon and "`scout`" in recon
    assert "Recon" in scout and "`recon`" in scout


def test_lead_variant_carries_the_delegation_contract(provisioner, settings):
    provisioner.create_agent(_spec("Recon", "Lead assistant"))  # first = lead
    provisioner.create_agent(_spec("Scout", "Research"))

    recon, scout = _soul(settings, "recon"), _soul(settings, "scout")
    assert "You are the **head of staff**" in recon
    assert "a2a_orchestrate" in recon and "a2a_call" in recon
    assert "Your head of staff is **Recon**" in scout


def test_sync_is_idempotent_and_keeps_a_single_fence(provisioner, settings):
    provisioner.create_agent(_spec("Recon"))
    provisioner.create_agent(_spec("Scout"))
    before = _soul(settings, "recon")

    team.sync(settings, provisioner.list_agents())

    after = _soul(settings, "recon")
    assert after == before
    assert after.count(team.TEAM_BEGIN) == 1


def test_user_edits_outside_the_fence_survive(provisioner, settings):
    provisioner.create_agent(_spec("Recon"))
    soul_path = settings.home_dir("recon") / "SOUL.md"
    soul_path.write_text("TOP PREAMBLE\n" + soul_path.read_text() + "\nMY FOOTNOTE\n")

    provisioner.create_agent(_spec("Scout"))  # roster change → fence refresh

    now = soul_path.read_text()
    assert now.startswith("TOP PREAMBLE\n")
    assert now.rstrip().endswith("MY FOOTNOTE")
    assert "Scout" in now
    assert now.count(team.TEAM_BEGIN) == 1


def test_imported_soul_untouched_without_consent(provisioner, tmp_path):
    home = _make_home(tmp_path / ".hermes")
    before = (home / "SOUL.md").read_bytes()

    provisioner.create_agent(_spec("Scout"))  # scout is lead
    provisioner.import_agent(home, name="Hermes")
    provisioner.create_agent(_spec("Clerk"))  # roster change → sync runs

    assert (home / "SOUL.md").read_bytes() == before


def test_making_imported_agent_lead_seeds_the_fence_only(provisioner, tmp_path):
    home = _make_home(tmp_path / ".hermes")
    provisioner.create_agent(_spec("Scout"))
    provisioner.import_agent(home, name="Hermes")

    provisioner.set_lead("hermes")

    soul = (home / "SOUL.md").read_text()
    assert "You are the **head of staff**" in soul
    # Everything outside the fence is byte-identical to the original.
    outside = soul.split(team.TEAM_BEGIN)[0]
    assert outside == "# Hermes\n\nMine, all mine.\n\n"
    assert soul.count(team.TEAM_BEGIN) == 1


def test_lead_call_seeds_fence_even_when_already_lead_by_default(
    provisioner, tmp_path
):
    """An agent imported onto an EMPTY roster is lead by default — metadata
    only, no fence (no consent yet). Calling set_lead IS the consent and must
    seed the contract rather than no-op."""
    home = _make_home(tmp_path / ".hermes")
    provisioner.import_agent(home, name="Hermes")
    assert team.TEAM_BEGIN not in (home / "SOUL.md").read_text()

    provisioner.set_lead("hermes")

    assert "You are the **head of staff**" in (home / "SOUL.md").read_text()


def test_demoted_imported_ex_lead_keeps_a_fresh_fence(provisioner, tmp_path):
    """Fence-presence, not leadness, gates imported writes: once seeded, the
    section stays current — including flipping to the teammate variant."""
    home = _make_home(tmp_path / ".hermes")
    provisioner.create_agent(_spec("Scout"))
    provisioner.import_agent(home, name="Hermes")
    provisioner.set_lead("hermes")

    provisioner.set_lead("scout")

    soul = (home / "SOUL.md").read_text()
    assert "You are the **head of staff**" not in soul
    assert "Your head of staff is **Scout**" in soul


def test_missing_soul_in_imported_home_is_created_with_fence_only(
    provisioner, tmp_path
):
    home = _make_home(tmp_path / ".hermes", soul=False)
    provisioner.create_agent(_spec("Scout"))
    provisioner.import_agent(home, name="Hermes")

    provisioner.set_lead("hermes")

    soul = (home / "SOUL.md").read_text()
    assert soul.startswith(team.TEAM_BEGIN)
    assert soul.count(team.TEAM_BEGIN) == 1
