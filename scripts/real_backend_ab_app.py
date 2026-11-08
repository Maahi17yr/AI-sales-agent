"""Guarded wrapper around the *real* Naija Bites FastAPI application.

No simulated business logic: existing authentication, menu, cart, checkout,
order and persistence services execute unchanged. One test-only route lets a
synthetic authenticated customer add a meal without invoking the paid LLM.
"""
import os
import secrets
from contextlib import asynccontextmanager

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from scripts import real_backend_ab_pool as ab_pool

MODE = ab_pool.guard()  # FAIL CLOSED before importing application code.
from core.api.server import app, _customer_session
from core.services.cart import CartService

original_lifespan = app.router.lifespan_context


@asynccontextmanager
async def ab_lifespan(instance):
    ab_pool.start()
    try:
        async with original_lifespan(instance):
            yield
    finally:
        ab_pool.stop()


app.router.lifespan_context = ab_lifespan


@app.middleware("http")
async def require_ab_key(request: Request, call_next):
    if request.url.path == "/ping" and request.method == "GET":
        return await call_next(request)
    if request.url.path.startswith(("/agent", "/payments", "/docs", "/redoc", "/openapi")):
        return JSONResponse({"detail": "External integrations disabled in isolated benchmark."}, status_code=404)
    supplied = request.headers.get("x-naija-ab-lab-key", "")
    if not secrets.compare_digest(supplied, os.environ["NAIJA_AB_LAB_KEY"]):
        return JSONResponse({"detail": "Not authorized for isolated benchmark."}, status_code=403)
    return await call_next(request)


class LabCartAdd(BaseModel):
    product_id: int = Field(gt=0)
    quantity: int = Field(default=1, ge=1, le=2)


@app.post("/__ab/cart/add")
def ab_add_cart(payload: LabCartAdd, request: Request):
    session_id = _customer_session(request)
    result = CartService(session_id).add_item(payload.product_id, payload.quantity)
    if not result.get("success"):
        raise HTTPException(status_code=409, detail=result.get("message", "Cart failed"))
    return result


@app.get("/__ab/status")
def ab_status():
    return {
        "backend": "core.api.server",
        "project": "green-hat-88213342",
        "database": ab_pool.DATABASES[MODE],
        "mode": MODE,
        "pool": ab_pool.snapshot(),
        "external_integrations": "disabled",
    }
