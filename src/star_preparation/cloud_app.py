"""Vercel ASGI entrypoint.

This is separate from ``web.py``: the local server stores artifacts on disk,
whereas a Vercel deployment must use private durable storage.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

PACKAGE_DIR = Path(__file__).parent
PROJECT_ROOT = PACKAGE_DIR.parents[1]
PROFILE_DIR = PROJECT_ROOT / "profiles"
INDEX_PAGE = PACKAGE_DIR / "web_assets" / "index.html"

app = FastAPI(title="STAR Preparation Engine", version="0.1.0")


def _storage_configured() -> bool:
    return bool(os.environ.get("BLOB_STORE_ID") or os.environ.get("BLOB_READ_WRITE_TOKEN"))


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return INDEX_PAGE.read_text(encoding="utf-8")


@app.get("/api/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "runtime": "vercel" if os.environ.get("VERCEL") else "local-asgi",
        "artifact_storage_configured": _storage_configured(),
    }


@app.get("/api/profiles")
def list_profiles() -> dict[str, list[str]]:
    return {"profiles": sorted(path.name for path in PROFILE_DIR.glob("*.json"))}


@app.get("/api/profiles/{name}")
def get_profile(name: str) -> dict:
    path = PROFILE_DIR / Path(name).name
    if path.suffix != ".json" or not path.is_file():
        raise HTTPException(status_code=404, detail="Profile not found")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/deployment-readiness")
def deployment_readiness() -> dict[str, object]:
    return {
        "ready": _storage_configured(),
        "required": ["private Vercel Blob store", "application authentication", "tenant metadata database"],
        "note": "Preparation runs are deliberately disabled until durable private storage is configured.",
    }
