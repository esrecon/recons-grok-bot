"""Shared skills + the teach-mode approval queue.

Skills are AgentSkills-spec `SKILL.md` folders (YAML frontmatter). All agents
read the same shared library (`shared/skills/`, wired as Hermes
`skills.external_dirs`), so a skill approved once is usable by every agent.

Agent-authored skills are staged, not auto-installed: Hermes writes drafts under
each agent's `pending/skills/` (config `skills.write_approval: true`). Approving
a draft moves it into the shared library. This is the review gate behind
teach-mode (docs/00, the 341-malicious-skills precedent is why nothing
self-installs).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .config import Settings
from .roster import Roster


@dataclass
class Skill:
    slug: str
    name: str
    description: str
    source: str  # "shared" | "pending"
    agent: str | None = None  # set for pending drafts
    version: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "source": self.source,
            "agent": self.agent,
            "version": self.version,
        }


def parse_frontmatter(skill_md: Path) -> dict[str, Any]:
    """Read the YAML frontmatter block from a SKILL.md (between --- fences)."""
    if not skill_md.exists():
        return {}
    text = skill_md.read_text("utf-8")
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    block = text[3:end].strip()
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _skill_from_dir(folder: Path, source: str, agent: str | None = None) -> Skill | None:
    fm = parse_frontmatter(folder / "SKILL.md")
    if not fm:
        return None
    return Skill(
        slug=folder.name,
        name=str(fm.get("name") or folder.name),
        description=str(fm.get("description") or ""),
        source=source,
        agent=agent,
        version=str(fm["version"]) if fm.get("version") is not None else None,
    )


class SkillLibrary:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._roster = Roster(settings.roster_path)

    def _pending_dir(self, agent_id: str) -> Path:
        # Hermes stages skill writes under HERMES_HOME/pending/skills (VERIFY).
        return self._s.home_dir(agent_id) / "pending" / "skills"

    def list_shared(self) -> list[Skill]:
        base = self._s.shared_skills_dir
        if not base.is_dir():
            return []
        out = []
        for folder in sorted(base.iterdir()):
            if folder.is_dir():
                s = _skill_from_dir(folder, "shared")
                if s:
                    out.append(s)
        return out

    def list_pending(self) -> list[Skill]:
        out = []
        for rec in self._roster.load():
            pdir = self._pending_dir(rec.id)
            if not pdir.is_dir():
                continue
            for folder in sorted(pdir.iterdir()):
                if folder.is_dir():
                    s = _skill_from_dir(folder, "pending", agent=rec.id)
                    if s:
                        out.append(s)
        return out

    def list_all(self) -> list[Skill]:
        return self.list_shared() + self.list_pending()

    def approve(self, agent_id: str, slug: str) -> Skill:
        """Move an agent's pending draft into the shared library."""
        src = self._pending_dir(agent_id) / slug
        if not (src / "SKILL.md").exists():
            raise FileNotFoundError(f"no pending skill '{slug}' for agent '{agent_id}'")
        dest = self._s.shared_skills_dir / slug
        if dest.exists():
            raise FileExistsError(f"a shared skill named '{slug}' already exists")
        self._s.shared_skills_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        skill = _skill_from_dir(dest, "shared")
        assert skill is not None
        return skill

    def reject(self, agent_id: str, slug: str) -> None:
        src = self._pending_dir(agent_id) / slug
        if src.is_dir():
            shutil.rmtree(src)
