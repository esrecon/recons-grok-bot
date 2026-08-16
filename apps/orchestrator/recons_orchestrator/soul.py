"""Targeted edits to the template-derived lines of a managed agent's SOUL.md.

SOUL.md is rendered once at creation (provisioning._write_soul) and is the
user's file from then on; team.py owns exactly one fenced block inside it.
This module owns the next-smallest thing: when the roster's name / role /
personality change from the Customize tab, the matching template-derived
pieces (title line, "You are **…**" intro, `## Your job`, `## How you work`)
are updated in place — and nothing else. Everything the user added stays
byte-for-byte, and the team fence is never entered (it contains its own
headings, so every heading scan here masks the fence span first).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from . import team
from .config import Settings
from .models import AgentRecord

log = logging.getLogger("recons.soul")

JOB_HEADING = "## Your job"
HOW_HEADING = "## How you work"

_HEADING = re.compile(r"^#{1,6} ", re.M)


def soul_path(settings: Settings, record: AgentRecord) -> Path:
    # Same resolution as team._home_of: an imported record's own home wins.
    home = Path(record.home) if record.home else settings.home_dir(record.id)
    return home / "SOUL.md"


def _fence_span(text: str) -> tuple[int, int]:
    """(start, end) of the team fence, or (len, len) when there is none."""
    begin = text.find(team.TEAM_BEGIN)
    if begin == -1:
        return (len(text), len(text))
    end = text.find(team.TEAM_END, begin)
    if end == -1:
        return (begin, len(text))  # broken fence: treat marker→EOF as fenced
    return (begin, end + len(team.TEAM_END))


def _find_heading(text: str, heading: str, fence: tuple[int, int]) -> re.Match | None:
    pattern = re.compile(rf"^{re.escape(heading)}[ \t]*$", re.M)
    for m in pattern.finditer(text):
        if not (fence[0] <= m.start() < fence[1]):
            return m
    return None


def _section_end(text: str, pos: int, fence: tuple[int, int]) -> int:
    """Where the section body starting at `pos` ends.

    The first heading of ANY level outside the fence (any level, so user-added
    `###` subsections under other sections survive untouched), or the fence
    itself if it comes first, or EOF.
    """
    end = len(text)
    for m in _HEADING.finditer(text, pos):
        if fence[0] <= m.start() < fence[1]:
            continue
        end = m.start()
        break
    if pos <= fence[0] < end:
        end = fence[0]
    return end


def replace_section(text: str, heading: str, body: str) -> tuple[str, bool]:
    """Replace the body under `heading`. No-op when the heading is absent."""
    fence = _fence_span(text)
    m = _find_heading(text, heading, fence)
    if m is None:
        return text, False
    end = _section_end(text, m.end() + 1, fence)
    updated = text[: m.end()] + "\n\n" + body.strip() + "\n" + text[end:]
    return updated, updated != text


def upsert_how_you_work(text: str, personality: str) -> tuple[str, bool]:
    """Replace, insert (after `## Your job`), or remove the personality section."""
    fence = _fence_span(text)
    personality = personality.strip()
    m = _find_heading(text, HOW_HEADING, fence)
    if m is not None:
        if personality:
            return replace_section(text, HOW_HEADING, personality)
        end = _section_end(text, m.end() + 1, fence)
        return text[: m.start()] + text[end:], True
    if not personality:
        return text, False
    jm = _find_heading(text, JOB_HEADING, fence)
    if jm is None:
        return text, False
    end = _section_end(text, jm.end() + 1, fence)
    block = f"{HOW_HEADING}\n\n{personality}\n"
    return text[:end] + block + text[end:], True


def rename(text: str, old: str, new: str) -> tuple[str, bool]:
    """Update the title line and the "You are **…**" intro, nothing else.

    Anchored, count=1, and safe against the fence by construction: the fence
    contains no `# ` title line and its "head of staff" bold never matches a
    name-anchored pattern. Function replacements sidestep re escape rules for
    names containing backslashes.
    """
    updated, n1 = re.subn(
        rf"^# {re.escape(old)}[ \t]*$", lambda _: f"# {new}", text, count=1, flags=re.M
    )
    updated, n2 = re.subn(
        rf"^You are \*\*{re.escape(old)}\*\*",
        lambda _: f"You are **{new}**",
        updated,
        count=1,
        flags=re.M,
    )
    return updated, bool(n1 or n2)


def apply_patch(
    settings: Settings,
    record: AgentRecord,
    *,
    old_name: str | None = None,
    role_changed: bool = False,
    personality_changed: bool = False,
) -> bool:
    """Sync the template-derived lines after a roster edit. True if written.

    Missing or unreadable SOUL.md is a quiet no-op — this must never block the
    roster change it trails. Callers gate on `not record.imported`.
    """
    path = soul_path(settings, record)
    try:
        text = path.read_text("utf-8")
    except OSError:
        return False
    updated = text
    if old_name and old_name != record.name:
        updated, _ = rename(updated, old_name, record.name)
    if role_changed:
        updated, _ = replace_section(updated, JOB_HEADING, record.role)
    if personality_changed:
        updated, _ = upsert_how_you_work(updated, record.personality)
    if updated == text:
        return False
    try:
        path.write_text(updated, "utf-8")
    except OSError:
        log.warning("could not update SOUL.md for %s", record.id, exc_info=True)
        return False
    return True
