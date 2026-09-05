"""Operator-added model providers (the "add a different LLM" flow).

The three tier providers (claude_wrapper / openai-codex / nous) are assumed to
be configured at the Hermes level. Anything else — xAI, Groq, OpenRouter, any
OpenAI-compatible endpoint — is registered here: a small JSON registry in the
shared dir plus one API key in the shared secrets file, named CUSTOM_<ID>_API_KEY
(the one open-ended family secrets_store accepts). The mesh renders a
`providers:` block into an agent's config.yaml whenever its selected provider
comes from this registry, referencing the key by ${ENV} — the literal secret
never lands in config.yaml, matching the telegram-token rule.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

from .config import TIERS, Settings
from .models import slugify

# Provider ids the tiers already claim (plus Hermes's own built-ins the tier
# table names). A custom entry may not shadow them.
RESERVED_PROVIDER_IDS = frozenset(t.provider for t in TIERS.values())


class CustomModelError(ValueError):
    pass


@dataclass(frozen=True)
class CustomModel:
    id: str
    label: str
    base_url: str
    model: str
    key_env: str

    def to_json(self) -> dict:
        return asdict(self)


def _registry_path(settings: Settings):
    return settings.shared_dir / "custom-models.json"


def load(settings: Settings) -> list[CustomModel]:
    path = _registry_path(settings)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return []
    out: list[CustomModel] = []
    for entry in raw if isinstance(raw, list) else []:
        try:
            out.append(
                CustomModel(
                    id=str(entry["id"]),
                    label=str(entry["label"]),
                    base_url=str(entry["base_url"]),
                    model=str(entry["model"]),
                    key_env=str(entry["key_env"]),
                )
            )
        except (KeyError, TypeError):
            continue
    return out


def get(settings: Settings, provider_id: str) -> CustomModel | None:
    return next((m for m in load(settings) if m.id == provider_id), None)


def _save(settings: Settings, entries: list[CustomModel]) -> None:
    path = _registry_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps([e.to_json() for e in entries], indent=2), "utf-8")
    os.replace(tmp, path)


def key_env_for(provider_id: str) -> str:
    return f"CUSTOM_{provider_id.upper().replace('-', '_')}_API_KEY"


def add(settings: Settings, *, label: str, base_url: str, model: str) -> CustomModel:
    """Register a provider. The caller stores the API key separately."""
    try:
        provider_id = slugify(label)
    except ValueError as exc:
        raise CustomModelError(str(exc)) from exc
    if provider_id in RESERVED_PROVIDER_IDS:
        raise CustomModelError(f"'{provider_id}' is a built-in provider id")
    entries = load(settings)
    if any(e.id == provider_id for e in entries):
        raise CustomModelError(f"a model provider named '{label}' already exists")
    base_url = base_url.strip().rstrip("/")
    if not (
        base_url.startswith("https://")
        or base_url.startswith("http://127.0.0.1")
        or base_url.startswith("http://localhost")
    ):
        # Keys ride on every request: plain http is only acceptable on loopback
        # (e.g. a local wrapper), same posture as the claude wrapper.
        raise CustomModelError("base_url must be https:// (or http:// on loopback)")
    if not model.strip():
        raise CustomModelError("model id is required")
    entry = CustomModel(
        id=provider_id,
        label=label.strip(),
        base_url=base_url,
        model=model.strip(),
        key_env=key_env_for(provider_id),
    )
    _save(settings, entries + [entry])
    return entry


def remove(settings: Settings, provider_id: str) -> CustomModel:
    entries = load(settings)
    entry = next((e for e in entries if e.id == provider_id), None)
    if entry is None:
        raise KeyError(provider_id)
    _save(settings, [e for e in entries if e.id != provider_id])
    return entry
