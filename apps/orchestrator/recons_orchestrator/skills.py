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

import os
import re
import shutil
from dataclasses import asdict, dataclass, field
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


# --- inspection ------------------------------------------------------------------
# Slugs are folder names under a trusted base; keep them to a strict charset and
# resolve every path back inside the base so nothing can point elsewhere.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
BODY_LIMIT = 64 * 1024
FILE_LIMIT = 256 * 1024
MAX_FILES = 200
SCRIPT_SUFFIXES = frozenset({
    ".sh", ".bash", ".zsh", ".py", ".js", ".mjs", ".cjs", ".ts", ".ps1", ".bat", ".cmd",
    ".exe", ".rb", ".pl", ".php",
})
SECRET_RE = re.compile(
    r"sk-ant-[A-Za-z0-9_-]{8,}|sk-proj-[A-Za-z0-9_-]{8,}|sk-oat[A-Za-z0-9_-]{8,}"
    r"|AKIA[0-9A-Z]{16}|(?i:password|api[_-]?key|secret|token)\s*[:=]\s*[^\s`'\"]{8,}"
)
URL_RE = re.compile(r"https?://[^\s)\]>\"']+")
GUARDRAILS_RE = re.compile(r"^#{1,6}\s*guardrails?\b", re.IGNORECASE | re.MULTILINE)


def validate_slug(slug: str) -> str:
    if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug) or ".." in slug:
        raise ValueError("invalid skill slug")
    return slug


@dataclass
class SkillFile:
    path: str
    size: int
    kind: str  # text | script | binary


@dataclass
class SkillDetail:
    skill: Skill
    frontmatter: dict[str, Any]
    body: str
    truncated: bool
    files: list[SkillFile]
    warnings: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "skill": self.skill.to_json(),
            "frontmatter": self.frontmatter,
            "body": self.body,
            "truncated": self.truncated,
            "files": [asdict(f) for f in self.files],
            "warnings": list(self.warnings),
        }


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _looks_binary(chunk: bytes) -> bool:
    return b"\x00" in chunk


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

    # -- inspection ------------------------------------------------------------
    def _folder(self, source: str, slug: str, agent: str | None) -> Path:
        validate_slug(slug)
        if source == "shared":
            base = self._s.shared_skills_dir
        elif source == "pending":
            if not agent or not SLUG_RE.fullmatch(agent):
                raise ValueError("pending skills need a valid agent id")
            base = self._pending_dir(agent)
        else:
            raise ValueError("source must be shared or pending")
        folder = base / slug
        if folder.is_symlink() or not (folder / "SKILL.md").is_file():
            raise FileNotFoundError(f"no {source} skill '{slug}'")
        try:
            folder.resolve().relative_to(base.resolve())
        except ValueError as exc:
            raise FileNotFoundError(f"no {source} skill '{slug}'") from exc
        return folder

    @staticmethod
    def _files(folder: Path) -> list[SkillFile]:
        out: list[SkillFile] = []
        for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
            dirnames[:] = sorted(
                d for d in dirnames
                if not d.startswith(".") and not (Path(dirpath) / d).is_symlink()
            )
            for name in filenames:
                if name.startswith("."):
                    continue
                path = Path(dirpath) / name
                if path.is_symlink() or not path.is_file():
                    continue
                rel = path.relative_to(folder).as_posix()
                size = path.stat().st_size
                if path.suffix.lower() in SCRIPT_SUFFIXES:
                    kind = "script"
                else:
                    with path.open("rb") as fh:
                        kind = "binary" if _looks_binary(fh.read(1024)) else "text"
                out.append(SkillFile(path=rel, size=size, kind=kind))
                if len(out) >= MAX_FILES:
                    break
        out.sort(key=lambda f: f.path)
        return out

    def inspect(self, source: str, slug: str, agent: str | None = None) -> SkillDetail:
        """Everything the operator needs to review a skill — read-only."""
        folder = self._folder(source, slug, agent)
        skill = _skill_from_dir(folder, source, agent=agent if source == "pending" else None)
        if skill is None:
            skill = Skill(slug=slug, name=slug, description="", source=source,
                          agent=agent if source == "pending" else None)
        raw = (folder / "SKILL.md").read_bytes()
        truncated = len(raw) > BODY_LIMIT
        body = raw[:BODY_LIMIT].decode("utf-8", "replace")
        fm = _jsonable(parse_frontmatter(folder / "SKILL.md"))
        files = self._files(folder)

        warnings: list[str] = []
        if not fm.get("name") or not fm.get("description"):
            warnings.append("frontmatter is missing name and/or description")
        if not GUARDRAILS_RE.search(body):
            warnings.append(
                "no Guardrails section — every taught skill must say where the agent stops and asks"
            )
        scanned = body
        for f in files:
            if f.path != "SKILL.md" and f.kind != "binary" and f.size <= FILE_LIMIT:
                scanned += "\n" + (folder / f.path).read_text("utf-8", "replace")
        if SECRET_RE.search(scanned):
            warnings.append("contains secret-shaped strings — keys must never be baked into a skill")
        scripts = [f.path for f in files if f.kind == "script"]
        if scripts:
            warnings.append("contains scripts: " + ", ".join(scripts) + " — read them before approving")
        urls = URL_RE.findall(scanned)
        if urls:
            warnings.append(f"references {len(urls)} external URL(s) — check where data would be sent")
        if truncated:
            warnings.append("SKILL.md is larger than 64 KB; body shown here is truncated")
        return SkillDetail(skill=skill, frontmatter=fm, body=body, truncated=truncated,
                           files=files, warnings=warnings)

    def read_file(self, source: str, slug: str, rel: str, agent: str | None = None) -> dict[str, Any]:
        """One text file from inside the skill folder, size-capped."""
        folder = self._folder(source, slug, agent)
        if (not rel or rel.startswith("/") or "\\" in rel
                or any(part in ("..", "") for part in rel.split("/"))
                or any(part.startswith(".") for part in rel.split("/"))):
            raise ValueError("invalid file path")
        target = folder / rel
        if target.is_symlink():
            raise ValueError("symlinks are not served")
        try:
            target.resolve().relative_to(folder.resolve())
        except ValueError as exc:
            raise ValueError("invalid file path") from exc
        if not target.is_file():
            raise FileNotFoundError("no such file in skill")
        size = target.stat().st_size
        with target.open("rb") as fh:
            data = fh.read(FILE_LIMIT + 1)
        if _looks_binary(data[:4096]):
            raise ValueError("binary files are not served")
        try:
            text = data[:FILE_LIMIT].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("binary files are not served") from exc
        return {"path": rel, "size": size, "text": text, "truncated": size > FILE_LIMIT}
