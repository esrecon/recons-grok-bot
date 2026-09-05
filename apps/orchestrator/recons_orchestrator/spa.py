"""Serve the built dashboard (Vite `dist/`) from the orchestrator.

Single origin for the SPA and its API keeps cookies SameSite=Strict and CSP
`'self'`-only. Files are served from `Settings.dashboard_dist`; anything that
isn't a real file under it gets `index.html` so client-side routes deep-link.
Every path is resolved and checked to stay inside the dist directory.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from .config import Settings

_IMMUTABLE = "public, max-age=31536000, immutable"
_NO_CACHE = "no-cache"


def _resolve(dist: Path, rel: str) -> Path | None:
    root = dist.resolve()
    candidate = (root / rel.lstrip("/")).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def register_spa(app: FastAPI, settings: Settings) -> None:
    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404, detail="not found")
        dist = settings.dashboard_dist
        index = dist / "index.html"
        if not index.is_file():
            raise HTTPException(
                status_code=503,
                detail=(
                    f"The dashboard is not built at {dist}. Build it with: "
                    "cd apps/dashboard && npm ci && npm run build — then copy "
                    "dist/ there, or point RECONS_DASHBOARD_DIST at it."
                ),
            )
        target = _resolve(dist, path) if path else None
        if target is not None:
            # Vite fingerprints everything under assets/, so those can be cached
            # forever; the shell, manifest and service worker must revalidate.
            cache = _IMMUTABLE if path.startswith("assets/") else _NO_CACHE
            return FileResponse(target, headers={"cache-control": cache})
        return FileResponse(index, headers={"cache-control": _NO_CACHE})
