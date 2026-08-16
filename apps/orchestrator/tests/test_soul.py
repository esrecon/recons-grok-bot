"""soul.py — targeted SOUL.md edits must touch only the template-derived
lines and never enter the managed team fence (which contains headings of its
own, so naive heading scans would drift into it)."""

from __future__ import annotations

from recons_orchestrator import soul, team

FENCE = (
    "<!-- recons:team:begin (managed by the orchestrator — do not edit) -->\n"
    "## Your team (managed)\n"
    "- **Scout** — Finds parts\n"
    "<!-- recons:team:end -->"
)

DOC = (
    "# Recon\n"
    "\n"
    "You are **Recon**, a permanent AI teammate at Essex Recons.\n"
    "\n"
    "## Your job\n"
    "\n"
    "Fix consoles\n"
    "## Working with the team\n"
    "\n"
    "Custom user prose here.\n"
    "\n"
    f"{FENCE}\n"
    "\n"
    "## Ground rules\n"
    "\n"
    "- Be careful.\n"
)


def test_rename_touches_title_and_intro_only():
    updated, changed = soul.rename(DOC, "Recon", "Ranger")
    assert changed
    assert updated.startswith("# Ranger\n")
    assert "You are **Ranger**, a permanent AI teammate" in updated
    # Body mentions elsewhere are not renamed; the fence is untouched.
    assert FENCE in updated
    assert "**Scout**" in updated


def test_replace_job_body_preserves_everything_else():
    updated, changed = soul.replace_section(DOC, soul.JOB_HEADING, "Repair Xbox pads")
    assert changed
    assert "## Your job\n\nRepair Xbox pads\n## Working with the team" in updated
    assert "Custom user prose here." in updated
    assert FENCE in updated


def test_section_body_never_swallows_the_fence():
    # A layout where the fence directly follows the job section: the fence
    # itself must act as the section boundary.
    doc = (
        "# R\n\n## Your job\n\nOld role\n\n"
        f"{FENCE}\n\n## Ground rules\n\n- x\n"
    )
    updated, changed = soul.replace_section(doc, soul.JOB_HEADING, "New role")
    assert changed
    assert FENCE in updated
    assert "New role" in updated and "Old role" not in updated
    assert "## Ground rules" in updated


def test_fence_headings_are_not_edit_targets():
    # "## Your team (managed)" lives inside the fence; replacing a heading that
    # exists ONLY there must be a no-op.
    updated, changed = soul.replace_section(DOC, "## Your team (managed)", "nope")
    assert not changed
    assert updated == DOC


def test_upsert_how_you_work_insert_replace_remove():
    inserted, changed = soul.upsert_how_you_work(DOC, "Be brief.")
    assert changed
    assert "Fix consoles\n## How you work\n\nBe brief.\n## Working with the team" in inserted

    replaced, changed = soul.upsert_how_you_work(inserted, "Be thorough.")
    assert changed
    assert "Be thorough." in replaced and "Be brief." not in replaced

    removed, changed = soul.upsert_how_you_work(replaced, "")
    assert changed
    assert "## How you work" not in removed
    # Removal restores the personality-less template shape exactly.
    assert "Fix consoles\n## Working with the team" in removed
    assert FENCE in removed


def test_user_subsections_survive_role_replacement():
    doc = DOC.replace(
        "Fix consoles\n",
        "Fix consoles\n\n### Notes\n\nKeep receipts.\n",
    )
    updated, _ = soul.replace_section(doc, soul.JOB_HEADING, "New role")
    # An any-level heading ends the section, so the user's ### subsection is
    # outside the replaced body and survives.
    assert "### Notes" in updated
    assert "Keep receipts." in updated
    assert "New role" in updated


def test_apply_patch_missing_file_is_a_noop(settings):
    from recons_orchestrator.models import AgentRecord

    rec = AgentRecord(
        id="ghost", name="Ghost", role="r", a2a_port=9900, created_at="2026"
    )
    assert soul.apply_patch(settings, rec, old_name="Ghost", role_changed=True) is False


def test_broken_fence_is_treated_as_fenced_to_eof():
    doc = "# R\n\n## Your job\n\nOld\n" + team.TEAM_BEGIN + " -->\n## Inside\n"
    updated, changed = soul.replace_section(doc, "## Inside", "nope")
    assert not changed and updated == doc
