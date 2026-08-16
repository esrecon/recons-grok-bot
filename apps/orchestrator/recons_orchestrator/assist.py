"""Server-side "improve this text" for the Customize tab.

Routing, cheapest-and-cleanest first:

  1. A stored Nous Portal key → direct HTTP to the Nous API (ordinary paid
     API; docs/40's own advice for auxiliary text tasks).
  2. The Hermes CLI, non-interactively against the DEFAULT home — the normal
     setup here signs subscriptions in through Hermes (auth.json), which the
     orchestrator cannot (and must not) borrow for direct API calls. Running
     the prompt through `hermes -z` uses exactly the auth and model the
     machine is already set up with, the same way dashboard chat does.
  3. The local claude-code-openai-wrapper, if installed and alive.

Orchestrator-initiated traffic never rides consumer OAuth tokens directly.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import httpx

from .chat import chat_command, chat_timeout
from .config import TIERS
from .secrets_store import SecretsStore

# CLIs aimed at terminals emit colour/cursor codes; same scrub as chat.py.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")

# Nous base URL per docs/00-research-report.md; the model is the bulk tier's.
NOUS_BASE_URL = "https://inference-api.nousresearch.com/v1"
DEFAULT_WRAPPER_BASE = "http://127.0.0.1:8600/v1"

FIELDS = ("name", "role", "personality", "soul")

_MAX_TOKENS = {"name": 60, "role": 150, "personality": 800, "soul": 4000}
# Clamp results to the roster's own field limits (AgentSpec); soul is unclamped.
_MAX_CHARS = {"name": 48, "role": 120, "personality": 2000}

_PREAMBLE = (
    "You improve text used to configure AI teammates for a small electronics "
    "repair business. Reply with ONLY the improved text — no preamble, no "
    "explanations, no quotation marks, no markdown code fences."
)

_FIELD_PROMPTS = {
    "name": (
        "Improve this AI agent's display name. One or two words, memorable, "
        "professional, no emoji. The agent's job: {role}."
    ),
    "role": (
        "Rewrite this one-line job description for an AI teammate named {name}. "
        "One sentence, plain business English, specific about what the agent "
        "actually does. Hard limit: 120 characters."
    ),
    "personality": (
        "Rewrite these working instructions for an AI teammate named {name} "
        "whose job is: {role}. Keep every rule and the original intent; make "
        "them clearer, more specific and actionable. Short lines or dashes are "
        "fine. Stay under 2000 characters."
    ),
    "soul": (
        "This is SOUL.md, the persona file for {name}, an AI teammate whose job "
        "is: {role}. Improve clarity, specificity and usefulness while keeping "
        "the document structure: keep every heading, keep all HTML comment "
        "markers (<!-- ... -->) and the content between recons:team markers "
        "exactly as they are, and keep the meaning of every ground rule. "
        "Return the complete improved document."
    ),
}


class AssistError(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status


def wrapper_alive(secrets: SecretsStore) -> bool:
    """Same 1.5s health probe ProviderService uses (providers.py)."""
    base = secrets.get("CLAUDE_WRAPPER_BASE_URL") or DEFAULT_WRAPPER_BASE
    root = base.rsplit("/v1", 1)[0]
    try:
        resp = httpx.get(f"{root}/health", timeout=1.5)
        return resp.status_code < 500
    except httpx.HTTPError:
        return False


def hermes_cli_available() -> bool:
    """True when the chat command's binary is on the orchestrator's PATH."""
    return shutil.which(chat_command("ping")[0]) is not None


def _improve_via_hermes(system: str, text: str) -> str:
    """One non-interactive Hermes turn against the default home.

    The default home (not an agent's) so improve prompts never land in an
    agent's transcript/memory; they do write sessions into the default home's
    state.db, which is the same trade dashboard chat makes. Output cleanliness
    depends on the CLI honouring a plain text turn — the same contract chat
    relies on (RECONS_CHAT_CMD overrides the command shape there and here).
    """
    prompt = f"{system}\n\nText to improve:\n{text}"
    argv = chat_command(prompt)
    env = dict(os.environ)
    env["HERMES_HOME"] = str(
        os.environ.get("HERMES_HOME", Path.home() / ".hermes")
    )
    env["PYTHONUNBUFFERED"] = "1"
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    env.pop("FORCE_COLOR", None)
    try:
        proc = subprocess.run(  # noqa: S603 - argv template is operator-configured
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=chat_timeout(),
            env=env,
        )
    except FileNotFoundError as exc:
        raise AssistError(502, f"Could not run `{argv[0]}` — is Hermes on PATH?") from exc
    except subprocess.TimeoutExpired as exc:
        raise AssistError(502, "The Hermes CLI timed out.") from exc
    except OSError as exc:
        raise AssistError(502, f"Could not run the Hermes CLI: {exc}") from exc
    if proc.returncode != 0:
        raise AssistError(502, f"The Hermes CLI failed (exit {proc.returncode}).")
    return _ANSI_RE.sub("", proc.stdout or "")


def _postprocess(field: str, text: str) -> str:
    out = text.strip()
    # One surrounding code fence or quote pair, if the model ignored the rules.
    fence = re.fullmatch(r"```[a-zA-Z]*\n(.*?)\n?```", out, flags=re.S)
    if fence:
        out = fence.group(1).strip()
    if len(out) >= 2 and out[0] == out[-1] and out[0] in "\"'":
        out = out[1:-1].strip()
    limit = _MAX_CHARS.get(field)
    if limit and len(out) > limit:
        cut = out[:limit]
        # Word-boundary truncate so a clamped role doesn't end mid-word.
        if " " in cut[limit // 2 :]:
            cut = cut[: cut.rindex(" ")]
        out = cut.rstrip(" ,;:-")
    return out


def improve(
    secrets: SecretsStore,
    field: str,
    text: str,
    *,
    agent_name: str = "",
    agent_role: str = "",
) -> str:
    if field not in FIELDS:
        raise AssistError(400, f"unknown field: {field}")

    system = _PREAMBLE + " " + _FIELD_PROMPTS[field].format(
        name=agent_name or "the agent", role=agent_role or "not specified"
    )

    if secrets.get("NOUS_API_KEY"):
        base, key = NOUS_BASE_URL, secrets.get("NOUS_API_KEY")
        model, label = TIERS["bulk"].model, "Nous"
    elif hermes_cli_available():
        # The usual setup: subscriptions signed in through Hermes itself, so
        # the CLI is the only door to them. Uses the default home's model.
        out = _postprocess(field, _improve_via_hermes(system, text))
        if not out:
            raise AssistError(502, "The Hermes CLI returned an empty result.")
        return out
    elif wrapper_alive(secrets):
        base = secrets.get("CLAUDE_WRAPPER_BASE_URL") or DEFAULT_WRAPPER_BASE
        key = secrets.get("CLAUDE_WRAPPER_API_KEY") or "none"
        model, label = TIERS["lead"].model, "Claude wrapper"
    else:
        raise AssistError(
            503,
            "No model available for text improvement: the Hermes CLI isn't on "
            "the orchestrator's PATH, no Nous Portal key is stored, and the "
            "Claude wrapper isn't running.",
        )
    try:
        resp = httpx.post(
            f"{base}/chat/completions",
            headers={"authorization": f"Bearer {key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": text},
                ],
                "temperature": 0.7,
                "max_tokens": _MAX_TOKENS[field],
            },
            timeout=httpx.Timeout(60.0, connect=10.0),
        )
    except httpx.HTTPError as exc:
        raise AssistError(502, f"Could not reach the {label} API: {exc}") from exc
    if resp.status_code >= 400:
        raise AssistError(
            502, f"The {label} API returned an error ({resp.status_code})."
        )
    try:
        improved = resp.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AssistError(502, f"Unexpected reply from the {label} API.") from exc
    out = _postprocess(field, str(improved))
    if not out:
        raise AssistError(502, f"The {label} API returned an empty result.")
    return out
