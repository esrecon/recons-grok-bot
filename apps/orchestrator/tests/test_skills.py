"""Shared-skill library + teach-mode approval-queue tests."""

from __future__ import annotations

import json

from recons_orchestrator.config import Settings
from recons_orchestrator.skills import SkillLibrary, parse_frontmatter


def _skill(dir_, slug, name, desc):
    d = dir_ / slug
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\nversion: 1.0.0\n---\n\n# {name}\n\nsteps…\n"
    )
    return d


def _settings(tmp_path) -> Settings:
    root = tmp_path / "recons"
    root.mkdir()
    (root / "roster.json").write_text(json.dumps([
        {"id": "scout", "name": "Scout", "role": "x", "tier": "workhorse",
         "avatar_color": "#3b82f6", "status": "running", "is_lead": False,
         "created_at": "2026-08-15T12:00:00+00:00", "a2a_port": 9900}
    ]))
    return Settings(root=root, dashboard_dist=tmp_path / "d")


def test_parse_frontmatter(tmp_path):
    p = tmp_path / "SKILL.md"
    p.write_text("---\nname: Do Thing\ndescription: does it\n---\nbody")
    fm = parse_frontmatter(p)
    assert fm["name"] == "Do Thing"
    assert fm["description"] == "does it"


def test_list_shared_and_pending(tmp_path):
    s = _settings(tmp_path)
    _skill(s.shared_skills_dir, "invoice-chase", "Invoice Chase", "chase overdue invoices")
    _skill(s.home_dir("scout") / "pending" / "skills", "new-supplier", "New Supplier", "onboard supplier")

    lib = SkillLibrary(s)
    shared = lib.list_shared()
    pending = lib.list_pending()
    assert [x.slug for x in shared] == ["invoice-chase"]
    assert pending[0].slug == "new-supplier"
    assert pending[0].agent == "scout"
    assert pending[0].source == "pending"


def test_approve_moves_pending_into_shared(tmp_path):
    s = _settings(tmp_path)
    _skill(s.home_dir("scout") / "pending" / "skills", "new-supplier", "New Supplier", "onboard supplier")
    lib = SkillLibrary(s)

    approved = lib.approve("scout", "new-supplier")
    assert approved.source == "shared"
    # Now in the shared library, gone from pending.
    assert (s.shared_skills_dir / "new-supplier" / "SKILL.md").exists()
    assert not (s.home_dir("scout") / "pending" / "skills" / "new-supplier").exists()
    assert lib.list_pending() == []


def test_approve_conflict_when_name_taken(tmp_path):
    s = _settings(tmp_path)
    _skill(s.shared_skills_dir, "dupe", "Dupe", "already shared")
    _skill(s.home_dir("scout") / "pending" / "skills", "dupe", "Dupe", "draft")
    lib = SkillLibrary(s)
    try:
        lib.approve("scout", "dupe")
    except FileExistsError:
        pass
    else:
        raise AssertionError("expected FileExistsError")


def test_reject_removes_pending(tmp_path):
    s = _settings(tmp_path)
    _skill(s.home_dir("scout") / "pending" / "skills", "junk", "Junk", "no")
    lib = SkillLibrary(s)
    lib.reject("scout", "junk")
    assert lib.list_pending() == []
