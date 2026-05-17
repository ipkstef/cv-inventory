"""Bearer-token authentication middleware."""

from __future__ import annotations

from fastapi import HTTPException, Request


def require_bearer(api_key: str):
    async def dep(request: Request) -> None:
        header = request.headers.get("authorization") or ""
        if not header.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="Missing bearer token")
        token = header[len("bearer ") :].strip()
        if token != api_key:
            raise HTTPException(status_code=401, detail="Invalid bearer token")

    return dep
