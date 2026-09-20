"""ASGI adapter for a persistent internal deployment.

Vercel's ephemeral filesystem is deliberately not treated as durable storage.
"""
from __future__ import annotations

import os
from functools import lru_cache

from fastapi import FastAPI, Request
from fastapi.responses import Response, JSONResponse
from starlette.concurrency import run_in_threadpool

from .reader import MAX_BYTES
from .web import Application

app = FastAPI(title="STAR Preparation Engine", version="0.2.0", docs_url=None, redoc_url=None, openapi_url=None)


@lru_cache(maxsize=1)
def application():
    if not os.environ.get("STAR_DATA_DIR"):
        raise ValueError("Set STAR_DATA_DIR to a persistent private volume")
    return Application(os.environ["STAR_DATA_DIR"], shared=True,
                       secure_cookies=os.environ.get("STAR_SECURE_COOKIES") == "1")


@app.api_route("/{path:path}", methods=["GET", "POST"])
async def dispatch(request: Request, path: str):
    try:
        backend = application()
    except ValueError as error:
        if request.method == "GET" and path == "api/health":
            return JSONResponse({"status": "not_ready", "ready": False, "reason": str(error)}, status_code=503)
        return JSONResponse({"error": str(error), "ready": False}, status_code=503,
                            headers={"Cache-Control": "no-store"})
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BYTES:
            return JSONResponse({"error": "Upload exceeds 25 MiB"}, status_code=413)
    target = request.url.path + ("?" + request.url.query if request.url.query else "")
    status, headers, data = await run_in_threadpool(
        backend.response, request.method, target, dict(request.headers), bytes(body))
    return Response(data, status_code=status, headers=headers)
