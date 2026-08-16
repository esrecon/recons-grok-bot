"""Generated agent headshots (the Customize tab's Pixar-style avatars).

One OpenAI-compatible images endpoint covers both intended providers: xAI
(`https://api.x.ai/v1`, model grok-2-image — the default) and OpenAI
(`https://api.openai.com/v1`, gpt-image-1). The key/base/model live in the
shared secrets file as IMAGE_API_*; the PNG lands beside service.env in the
per-agent non-home dir, so migration re-syncs of home/ never touch it.

Provider quirks handled here:
  * grok-2-image ignores size params and returns JPEG bytes — we keep the
    on-disk name avatar.png but always serve by magic-byte media type.
  * gpt-image-1 rejects `response_format` (it always returns b64) — on a 400
    naming that param, retry once without it.
  * Responses may carry `b64_json` or a short-lived `url`; both are accepted.
"""

from __future__ import annotations

import base64
from pathlib import Path

import httpx

from .config import Settings
from .secrets_store import SecretsStore

DEFAULT_IMAGE_BASE_URL = "https://api.x.ai/v1"
DEFAULT_IMAGE_MODEL = "grok-2-image"  # VERIFY against the provider's model list


class AvatarError(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status


def avatar_path(settings: Settings, agent_id: str) -> Path:
    return settings.agents_dir / agent_id / "avatar.png"


def media_type_of(data: bytes) -> str:
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def build_prompt(name: str, role: str) -> str:
    return (
        "Pixar-style 3D cartoon character headshot of a friendly professional "
        f"AI assistant named {name}. Their job: {role}. Head and shoulders, "
        "facing slightly off-camera, big expressive eyes, warm confident "
        "smile, one small prop or outfit detail hinting at the job, soft "
        "studio lighting, clean solid pastel background, high-quality 3D "
        "render. No text, no watermark, no logo."
    )


def settings_of(secrets: SecretsStore) -> dict:
    """The image-generation settings as the API reports them (key never leaves)."""
    return {
        "key_set": secrets.is_set("IMAGE_API_KEY"),
        "base_url": secrets.get("IMAGE_API_BASE_URL") or DEFAULT_IMAGE_BASE_URL,
        "model": secrets.get("IMAGE_API_MODEL") or DEFAULT_IMAGE_MODEL,
    }


def _request(base: str, key: str, payload: dict) -> httpx.Response:
    try:
        return httpx.post(
            f"{base}/images/generations",
            headers={"authorization": f"Bearer {key}"},
            json=payload,
            timeout=httpx.Timeout(120.0, connect=10.0),
        )
    except httpx.HTTPError as exc:
        raise AvatarError(502, f"Could not reach the image API: {exc}") from exc


def generate(secrets: SecretsStore, name: str, role: str) -> bytes:
    key = secrets.get("IMAGE_API_KEY")
    if not key:
        raise AvatarError(
            503,
            "Add an image API key in Settings → Avatars first (xAI or OpenAI).",
        )
    cfg = settings_of(secrets)
    base, model = cfg["base_url"].rstrip("/"), cfg["model"]

    payload = {
        "model": model,
        "prompt": build_prompt(name, role),
        "n": 1,
        "response_format": "b64_json",
    }
    resp = _request(base, key, payload)
    if resp.status_code == 400 and "response_format" in resp.text:
        payload.pop("response_format")
        resp = _request(base, key, payload)
    if resp.status_code >= 400:
        raise AvatarError(
            502, f"The image API returned an error ({resp.status_code})."
        )

    try:
        item = resp.json()["data"][0]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AvatarError(502, "Unexpected reply from the image API.") from exc

    if item.get("b64_json"):
        try:
            return base64.b64decode(item["b64_json"])
        except (ValueError, TypeError) as exc:
            raise AvatarError(502, "The image API returned unreadable data.") from exc
    if item.get("url"):
        try:
            image = httpx.get(item["url"], timeout=60.0)
        except httpx.HTTPError as exc:
            raise AvatarError(502, f"Could not download the generated image: {exc}") from exc
        if image.status_code >= 400 or not image.content:
            raise AvatarError(502, "Could not download the generated image.")
        return image.content
    raise AvatarError(502, "The image API returned no image.")
