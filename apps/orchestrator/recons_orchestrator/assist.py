"""Server-side "improve this text" for the Customize tab.

The one place the orchestrator itself calls an LLM. Routing follows docs/40's
ToS guidance: Nous Portal first (ordinary paid API, and the docs' own advice
for auxiliary text tasks), falling back to the local claude-code-openai-wrapper
only when no Nous key is stored AND the wrapper answers its health probe.
Orchestrator-initiated traffic never rides the consumer OAuth subscriptions.
"""

from __future__ import annotations

import re

import httpx

from .config import TIERS
from .secrets_store import SecretsStore

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

    if secrets.get("NOUS_API_KEY"):
        base, key = NOUS_BASE_URL, secrets.get("NOUS_API_KEY")
        model, label = TIERS["bulk"].model, "Nous"
    elif wrapper_alive(secrets):
        base = secrets.get("CLAUDE_WRAPPER_BASE_URL") or DEFAULT_WRAPPER_BASE
        key = secrets.get("CLAUDE_WRAPPER_API_KEY") or "none"
        model, label = TIERS["lead"].model, "Claude wrapper"
    else:
        raise AssistError(
            503,
            "No model available for text improvement. Add a Nous Portal API "
            "key in Settings → Providers — it's the cheap tier and takes a minute.",
        )

    system = _PREAMBLE + " " + _FIELD_PROMPTS[field].format(
        name=agent_name or "the agent", role=agent_role or "not specified"
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
