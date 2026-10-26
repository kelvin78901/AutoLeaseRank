"""/insights endpoints: segment stats, price history, dropping-now, comparables, data quality.

Errors under /insights render as {"error": msg}; every other path keeps whatever
handlers were registered before install_insights_error_handlers ran.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..insights.comparables import find_comparables
from ..insights.history import dropping_now, listing_timeline, load_history
from ..insights.models import (
    DEFAULT_DROPPING_LIMIT, DEFAULT_K, DEFAULT_MIN_COUNT, DEFAULT_STALE_DAYS,
    MAX_DROPPING_LIMIT, MAX_K, MAX_STALE_DAYS,
    ComparablesResponse, DroppingResponse, HistoryResponse, QualityResponse,
    SegmentsResponse,
)
from ..insights.quality import source_quality
from ..insights.segments import segment_stats
from ..store import db
from .watch import _validation_message

router = APIRouter()


def get_insights_con():
    con = db.connect()
    try:
        yield con
    finally:
        con.close()


def get_insights_snapshot():
    from .main import S
    return S.listings


@router.get("/insights/segments", response_model=SegmentsResponse)
def segments(
    group_by: Literal["make", "body", "state"] = Query("make"),
    min_count: int = Query(DEFAULT_MIN_COUNT, ge=1),
    listings=Depends(get_insights_snapshot),
):
    groups = segment_stats(listings, group_by, min_count)
    return SegmentsResponse(group_by=group_by, min_count=min_count, groups=groups)


@router.get("/insights/history/{listing_key:path}", response_model=HistoryResponse)
def history(listing_key: str, con=Depends(get_insights_con)):
    points = listing_timeline(load_history(con, listing_key), listing_key)
    if not points:
        raise StarletteHTTPException(404, f"no history for listing {listing_key}")
    return HistoryResponse(listing_key=listing_key, count=len(points), points=points)


@router.get("/insights/dropping", response_model=DroppingResponse)
def dropping(
    limit: int = Query(DEFAULT_DROPPING_LIMIT, ge=1, le=MAX_DROPPING_LIMIT),
    con=Depends(get_insights_con),
):
    items = dropping_now(load_history(con), limit)
    return DroppingResponse(count=len(items), limit=limit, items=items)


@router.get("/insights/comparables/{listing_key:path}", response_model=ComparablesResponse)
def comparables(
    listing_key: str,
    k: int = Query(DEFAULT_K, ge=1, le=MAX_K),
    listings=Depends(get_insights_snapshot),
):
    try:
        comps = find_comparables(listings, listing_key, k)
    except KeyError:
        raise StarletteHTTPException(404, f"listing {listing_key} not found")
    target = next(l for l in listings if l.listing_key == listing_key)
    return ComparablesResponse(listing_key=listing_key, body=target.body, k=k,
                               count=len(comps), comparables=comps)


@router.get("/insights/quality", response_model=QualityResponse)
def quality(
    stale_days: int = Query(DEFAULT_STALE_DAYS, ge=0, le=MAX_STALE_DAYS),
    listings=Depends(get_insights_snapshot),
):
    now = datetime.now(timezone.utc)
    return QualityResponse(stale_days=stale_days, as_of=now,
                           sources=source_quality(listings, stale_days, now))


def _is_insights_path(request: Request) -> bool:
    path = request.url.path
    return path == "/insights" or path.startswith("/insights/")


async def _default_unexpected(request: Request, exc: Exception):
    return PlainTextResponse("Internal Server Error", status_code=500)


def install_insights_error_handlers(app: FastAPI) -> None:
    """Render /insights errors as {"error": msg}; delegate other paths to the prior handlers."""
    prev = dict(app.exception_handlers)
    prev_http = prev.get(StarletteHTTPException, http_exception_handler)
    prev_val = prev.get(RequestValidationError, request_validation_exception_handler)
    prev_exc = prev.get(Exception, _default_unexpected)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        if _is_insights_path(request):
            return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code,
                                headers=getattr(exc, "headers", None))
        return await prev_http(request, exc)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        if _is_insights_path(request):
            return JSONResponse({"error": _validation_message(exc)}, status_code=422)
        return await prev_val(request, exc)

    @app.exception_handler(Exception)
    async def _unexpected_error(request: Request, exc: Exception):
        if _is_insights_path(request):
            return JSONResponse({"error": "internal error"}, status_code=500)
        return await prev_exc(request, exc)
