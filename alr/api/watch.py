"""HTTP surface for watches (saved searches). See alr/watch/CONTRACT.md.

Endpoints:
  POST   /watch                   create a watch
  GET    /watch                   list watches
  GET    /watch/{watch_id}        one watch
  DELETE /watch/{watch_id}        delete a watch
  GET    /watch/{watch_id}/matches?limit=N   current snapshot matches
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..store import watches as store
from ..watch.matcher import match_watch, scored_snapshot
from ..watch.models import DEFAULT_MATCH_LIMIT, MAX_MATCH_LIMIT, Watch, WatchCreate
from .common import get_con

router = APIRouter()


def get_snapshot():
    from .main import S  # lazy: main imports this module
    return S.listings, S.scorer


def _not_found(watch_id: int) -> StarletteHTTPException:
    return StarletteHTTPException(404, f"watch {watch_id} not found")


@router.post("/watch", status_code=201, response_model=Watch)
def create_watch(body: WatchCreate, con=Depends(get_con)):
    return store.create_watch(con, body)


@router.get("/watch", response_model=list[Watch])
def list_watches(con=Depends(get_con)):
    return store.list_watches(con)


@router.get("/watch/{watch_id}", response_model=Watch)
def get_watch(watch_id: int, con=Depends(get_con)):
    watch = store.get_watch(con, watch_id)
    if watch is None:
        raise _not_found(watch_id)
    return watch


@router.delete("/watch/{watch_id}", status_code=204)
def delete_watch(watch_id: int, con=Depends(get_con)):
    if not store.delete_watch(con, watch_id):
        raise _not_found(watch_id)
    return Response(status_code=204)


@router.get("/watch/{watch_id}/matches")
def watch_matches(
    watch_id: int,
    limit: int = Query(DEFAULT_MATCH_LIMIT, ge=1, le=MAX_MATCH_LIMIT),
    con=Depends(get_con),
    snapshot=Depends(get_snapshot),
):
    watch = store.get_watch(con, watch_id)
    if watch is None:
        raise _not_found(watch_id)
    listings, scorer = snapshot
    scored = scored_snapshot(listings, scorer)
    hits = match_watch(watch, scored, limit)
    return {"watch": watch.model_dump(mode="json"), "count": len(hits),
            "matches": [h.model_dump(mode="json") for h in hits]}
