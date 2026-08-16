"""The managed "Your team" section of each agent's SOUL.md.

SOUL.md is the user's file — rendered once at creation and never rewritten
(provisioning._write_soul). But agents still need to know who their teammates
are and, once a head of staff exists, how work is routed between them. That
knowledge lives in ONE clearly fenced block that this module owns:

    <!-- recons:team:begin ... -->
    ...regenerated whenever the roster changes...
    <!-- recons:team:end -->

Everything outside the fence is preserved byte-for-byte, and writes only
happen when the rendered section actually differs — same idempotency contract
as mesh.rewire.

Consent model (the important part):
  * Managed agents (platform-created) always get the fence and keep it current.
  * Imported agents' homes are never written — until the operator explicitly
    makes one the head of staff (POST /api/agents/{id}/lead), which *seeds*
    the fence once. Fence-presence, not leadness, gates all later writes: a
    demoted imported ex-lead keeps its (existing) fence fresh, while an
    imported agent that was never lead-ified is never touched.
"""

from __future__ import annotations

import logging
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from .config import Settings
from .models import AgentRecord

log = logging.getLogger("recons.team")

TEAM_BEGIN = "<!-- recons:team:begin"
TEAM_END = "<!-- recons:team:end -->"

_jinja = Environment(
    loader=PackageLoader("recons_orchestrator", "templates"),
    autoescape=select_autoescape(enabled_extensions=()),
    keep_trailing_newline=True,
    trim_blocks=True,
    lstrip_blocks=True,
)


def _home_of(settings: Settings, record: AgentRecord) -> Path:
    # Same resolution as ChatBackend._agent_home: an imported record's own
    # home wins; managed agents live in the canonical layout.
    return Path(record.home) if record.home else settings.home_dir(record.id)


def render_section(record: AgentRecord, records: list[AgentRecord]) -> str:
    """The fenced block for one agent, given the full roster."""
    lead = next((r for r in records if r.is_lead), None)
    teammates = [r for r in records if r.id != record.id]
    rendered = _jinja.get_template("team-section.md.j2").render(
        record=record, lead=lead, teammates=teammates
    )
    return rendered.rstrip("\n")


def upsert_section(soul_path: Path, section: str) -> bool:
    """Replace the fenced block in SOUL.md (or append one). True if changed.

    Bytes outside the fence are untouched; a SOUL.md that does not exist yet
    is created containing only the fence.
    """
    existing = soul_path.read_text("utf-8") if soul_path.is_file() else ""

    begin = existing.find(TEAM_BEGIN)
    if begin != -1:
        end = existing.find(TEAM_END, begin)
        if end != -1:
            updated = existing[:begin] + section + existing[end + len(TEAM_END) :]
        else:
            # Broken fence (no end marker): replace from the marker to EOF
            # rather than stacking a second fence under it.
            updated = existing[:begin] + section + "\n"
    elif existing:
        updated = existing.rstrip("\n") + "\n\n" + section + "\n"
    else:
        updated = section + "\n"

    if updated == existing:
        return False
    soul_path.parent.mkdir(parents=True, exist_ok=True)
    soul_path.write_text(updated, "utf-8")
    return True


def seed(settings: Settings, record: AgentRecord, records: list[AgentRecord]) -> None:
    """Force-create the fence for one agent.

    This is the explicit-consent path for imported homes: it runs only from a
    deliberate operator action (making the agent head of staff), the one place
    the never-touch-imported-files promise is knowingly crossed.
    """
    try:
        upsert_section(_home_of(settings, record) / "SOUL.md", render_section(record, records))
    except OSError:
        log.warning("could not seed team section for %s", record.id, exc_info=True)


def sync(settings: Settings, records: list[AgentRecord]) -> None:
    """Refresh every fence that is allowed to exist.

    Managed agents: always. Imported agents: only when the fence is already
    present (i.e. was seeded by an explicit operator action).
    """
    for record in records:
        soul = _home_of(settings, record) / "SOUL.md"
        try:
            if record.imported:
                if not soul.is_file() or TEAM_BEGIN not in soul.read_text("utf-8"):
                    continue
            upsert_section(soul, render_section(record, records))
        except OSError:
            # One unreadable home must not break provisioning for the rest.
            log.warning("could not sync team section for %s", record.id, exc_info=True)