"""Find Hermes agents that already exist on this machine.

People arrive with Hermes already installed and configured — the default
profile in ~/.hermes, and often extra profiles beside it. Making them recreate
those from scratch in the dashboard would be pointless and would throw away
their existing memory and history, so instead we discover them and let the user
adopt them into the roster.

Adoption is deliberately non-destructive: we record where the agent already
lives and leave its files exactly where they are. Nothing is moved, rewritten
or re-owned, so an imported agent keeps working through the Hermes CLI just as
it did before.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class DiscoveredAgent:
    """A Hermes home found on disk that the roster doesn't know about yet."""

    id: str
    name: str
    home: Path
    role: str = ""
    model: str = ""
    already_imported: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "home": str(self.home),
            "role": self.role,
            "model": self.model,
            "already_imported": self.already_imported,
        }


def _looks_like_hermes_home(path: Path) -> bool:
    """A Hermes home has a config, or the state/soul files a used agent leaves."""
    if not path.is_dir():
        return False
    markers = ("config.yaml", "config.yml", "SOUL.md", "state.db")
    return any((path / m).exists() for m in markers)


def _read_config(home: Path) -> dict[str, Any]:
    for name in ("config.yaml", "config.yml"):
        cfg = home / name
        if cfg.is_file():
            try:
                data = yaml.safe_load(cfg.read_text("utf-8"))
                return data if isinstance(data, dict) else {}
            except (yaml.YAMLError, OSError):
                return {}
    return {}


def _name_from_soul(home: Path) -> str:
    """First markdown heading of SOUL.md is conventionally the agent's name."""
    soul = home / "SOUL.md"
    if not soul.is_file():
        return ""
    try:
        for line in soul.read_text("utf-8").splitlines():
            if line.startswith("# "):
                return line[2:].strip()
    except OSError:
        pass
    return ""


def _model_of(cfg: dict[str, Any]) -> str:
    model = cfg.get("model")
    if isinstance(model, dict):
        return str(model.get("default") or model.get("provider") or "")
    return str(model or "")


def search_paths() -> list[Path]:
    """Where Hermes homes conventionally live.

    RECONS_DISCOVERY_PATHS (colon-separated) adds more, for installs that keep
    profiles somewhere unusual.
    """
    paths: list[Path] = []
    home = Path(os.path.expanduser("~"))
    default = Path(os.environ.get("HERMES_HOME", home / ".hermes"))
    paths.append(default)
    # Sibling profiles: ~/.hermes-<name> and ~/.hermes/profiles/<name>.
    paths.extend(sorted(home.glob(".hermes-*")))
    profiles = default / "profiles"
    if profiles.is_dir():
        paths.extend(sorted(p for p in profiles.iterdir() if p.is_dir()))
    for extra in os.environ.get("RECONS_DISCOVERY_PATHS", "").split(":"):
        if extra.strip():
            paths.append(Path(extra.strip()))
    return paths


def discover(known_homes: set[Path] | None = None) -> list[DiscoveredAgent]:
    """Hermes homes on this machine, flagged if already in the roster."""
    known = {p.resolve() for p in (known_homes or set())}
    found: list[DiscoveredAgent] = []
    seen: set[Path] = set()

    for path in search_paths():
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen or not _looks_like_hermes_home(resolved):
            continue
        seen.add(resolved)

        cfg = _read_config(resolved)
        # ~/.hermes is the default profile; deeper paths are named by folder.
        raw_name = _name_from_soul(resolved) or resolved.name.lstrip(".")
        name = "Hermes" if raw_name == "hermes" else raw_name
        found.append(
            DiscoveredAgent(
                id=resolved.name.lstrip(".").lower().replace(" ", "-") or "hermes",
                name=name,
                home=resolved,
                role=str(cfg.get("description") or cfg.get("role") or ""),
                model=_model_of(cfg),
                already_imported=resolved in known,
            )
        )
    return found
