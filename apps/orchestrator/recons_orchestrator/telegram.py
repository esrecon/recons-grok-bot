"""Per-agent Telegram bot-token store + best-effort extraction from a home.

Mirrors the a2a edge-token store: one JSON file under shared/ (chmod 600,
atomic tmp+rename writes) that the mesh re-emits into service.env on every
rewire — so the real token survives regeneration without ever appearing in
config.yaml, which carries only the ${TELEGRAM_BOT_TOKEN} placeholder.

Write-only over the API, same contract as SecretsStore: presence can be
reported, the value is never returned.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml

from .config import Settings


def tokens_path(settings: Settings) -> Path:
    return settings.shared_dir / "telegram-tokens.json"


def load_tokens(settings: Settings) -> dict[str, str]:
    path = tokens_path(settings)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def store_token(settings: Settings, agent_id: str, token: str) -> None:
    tokens = load_tokens(settings)
    tokens[agent_id] = token
    _save(settings, tokens)


def drop_token(settings: Settings, agent_id: str) -> None:
    tokens = load_tokens(settings)
    if tokens.pop(agent_id, None) is not None:
        _save(settings, tokens)


def _save(settings: Settings, tokens: dict[str, str]) -> None:
    path = tokens_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(tokens, indent=2, sort_keys=True), "utf-8")
    tmp.chmod(0o600)
    os.replace(tmp, path)
    path.chmod(0o600)


def extract_telegram(home: Path) -> tuple[str | None, str | None]:
    """(bot_token, allowed_users) found inside a copied home, else Nones.

    `hermes gateway setup` stores the pasted BotFather token "in Hermes' own
    state" (docs/30) — exactly where varies by Hermes version, so this tries
    the plausible config paths and a home-local .env, and returns None rather
    than guessing. Promotion demands an explicit token whenever extraction
    comes up empty. VERIFY each key path against the installed Hermes.
    """
    cfg: dict[str, Any] = {}
    for name in ("config.yaml", "config.yml"):
        f = home / name
        if f.is_file():
            try:
                loaded = yaml.safe_load(f.read_text("utf-8"))
                cfg = loaded if isinstance(loaded, dict) else {}
            except (OSError, yaml.YAMLError):
                cfg = {}
            break

    def dig(*path: str) -> str | None:
        node: Any = cfg
        for key in path:
            if not isinstance(node, dict):
                return None
            node = node.get(key)
        return str(node) if isinstance(node, (str, int)) and str(node) else None

    token = None
    for candidate in (
        ("gateway", "platforms", "telegram", "extra", "bot_token"),  # VERIFY
        ("gateway", "platforms", "telegram", "extra", "token"),  # VERIFY
        ("gateway", "platforms", "telegram", "token"),  # VERIFY
        ("telegram", "bot_token"),  # VERIFY
    ):
        token = dig(*candidate)
        if token:
            break

    users = None
    for candidate in (
        ("gateway", "platforms", "telegram", "extra", "allowed_users"),  # VERIFY
        ("telegram", "allowed_users"),  # VERIFY
    ):
        users = dig(*candidate)
        if users:
            break

    # Some setups keep a .env beside the config; the old machine's actual
    # process environment does not travel with the home, but this file does.
    env_file = home / ".env"
    if env_file.is_file() and (token is None or users is None):
        try:
            for line in env_file.read_text("utf-8").splitlines():
                if line.lstrip().startswith("#"):
                    continue
                key, sep, value = line.partition("=")
                key, value = key.strip(), value.strip().strip("'\"")
                if not sep or not value:
                    continue
                if token is None and key == "TELEGRAM_BOT_TOKEN":
                    token = value
                if users is None and key == "TELEGRAM_ALLOWED_USERS":
                    users = value
        except OSError:
            pass

    return token, users
