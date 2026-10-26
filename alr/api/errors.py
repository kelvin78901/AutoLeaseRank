"""Unified JSON errors for prefixed API paths. See alr/api/CONTRACT.md."""
from __future__ import annotations

from typing import Iterable

from fastapi import FastAPI, Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


def validation_message(exc: RequestValidationError) -> str:
    """One human-readable line for a validation error (the "error" value in a 422)."""
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()))
        msg = err.get("msg", "invalid")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts) or "invalid request"


def path_matches(path: str, prefixes: Iterable[str]) -> bool:
    """True iff path == p or path.startswith(p + "/") for some p in prefixes."""
    return any(path == p or path.startswith(p + "/") for p in prefixes)


def install_json_errors(app: FastAPI, prefixes: Iterable[str]) -> None:
    """Render errors on the prefixed paths as {"error": msg}; leave other paths on defaults.

    Call before the app serves its first request (Starlette copies handlers once).
    """
    prefixes = tuple(prefixes)
    if not prefixes or any(not p.startswith("/") or p.endswith("/") for p in prefixes):
        raise ValueError(f"prefixes must be non-empty and look like '/name': {prefixes!r}")

    async def _http_error(request: Request, exc: StarletteHTTPException):
        if path_matches(request.url.path, prefixes):
            return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code,
                                headers=getattr(exc, "headers", None))
        return await http_exception_handler(request, exc)

    async def _validation_error(request: Request, exc: RequestValidationError):
        if path_matches(request.url.path, prefixes):
            return JSONResponse({"error": validation_message(exc)}, status_code=422)
        return await request_validation_exception_handler(request, exc)

    async def _unexpected_error(request: Request, exc: Exception):
        # Runs inside ServerErrorMiddleware, which re-raises after responding,
        # so the error is still logged either way.
        if path_matches(request.url.path, prefixes):
            return JSONResponse({"error": "internal error"}, status_code=500)
        return PlainTextResponse("Internal Server Error", status_code=500)

    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _unexpected_error)
