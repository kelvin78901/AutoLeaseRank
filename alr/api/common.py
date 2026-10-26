"""Helpers shared by the /watch and /insights routers. See alr/api/CONTRACT.md."""
from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

from fastapi import APIRouter, FastAPI

from ..store import db

if TYPE_CHECKING:
    import duckdb


def get_con() -> Iterator[duckdb.DuckDBPyConnection]:
    con = db.connect()
    try:
        yield con
    finally:
        con.close()


def add_flat_routes(app: FastAPI, router: APIRouter) -> None:
    for r in router.routes:  # flat routes: include_router is lazy in newer FastAPI
        app.add_api_route(r.path, r.endpoint, methods=sorted(r.methods),
                          status_code=r.status_code, response_model=r.response_model)
