"""Helpers shared by the /watch and /insights routers. See alr/api/CONTRACT.md."""
from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

from fastapi import APIRouter, FastAPI
from fastapi.datastructures import Default

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
        app.router.add_api_route(
            r.path, r.endpoint,
            response_model=Default(None) if r.stream_item_type is not None else r.response_model,
            status_code=r.status_code,
            tags=list(r.tags),
            dependencies=list(r.dependencies),
            summary=r.summary,
            description=r.description,
            response_description=r.response_description,
            responses=dict(r.responses),
            deprecated=r.deprecated,
            methods=sorted(r.methods),
            operation_id=r.operation_id,
            response_model_include=r.response_model_include,
            response_model_exclude=r.response_model_exclude,
            response_model_by_alias=r.response_model_by_alias,
            response_model_exclude_unset=r.response_model_exclude_unset,
            response_model_exclude_defaults=r.response_model_exclude_defaults,
            response_model_exclude_none=r.response_model_exclude_none,
            include_in_schema=r.include_in_schema,
            response_class=r.response_class,
            name=r.name,
            route_class_override=type(r),
            callbacks=r.callbacks,
            openapi_extra=r.openapi_extra,
            generate_unique_id_function=r.generate_unique_id_function,
            strict_content_type=r.strict_content_type,
        )
