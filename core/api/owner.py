"""Owner-only API routes; no owner access is granted by client-provided roles."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, StrictBool

from core.services import auth_service, owner_dashboard_service as dashboard

router = APIRouter(prefix="/owner", tags=["owner"])


def require_owner(request: Request) -> dict:
    account = auth_service.authenticate_token(request.cookies.get(auth_service.SESSION_COOKIE_NAME))
    if account is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    if account["role"] != "owner":
        raise HTTPException(status_code=403, detail="Owner access required.")
    return account


class StatusUpdate(BaseModel):
    status: str


class MenuUpdate(BaseModel):
    price: int | None = Field(default=None, ge=0, le=100000000)
    available: StrictBool | None = None


@router.get("/me")
def owner_me(request: Request):
    return {"owner": require_owner(request)}


@router.get("/overview")
def owner_overview(request: Request):
    require_owner(request)
    return dashboard.overview()


@router.get("/orders")
def owner_orders(request: Request):
    require_owner(request)
    return {"orders": dashboard.list_orders()}


@router.put("/orders/{order_id}/status")
def owner_update_order_status(order_id: int, payload: StatusUpdate, request: Request):
    owner = require_owner(request)
    try:
        return dashboard.update_order_status(owner, order_id, payload.status)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/menu")
def owner_menu(request: Request):
    require_owner(request)
    return {"products": dashboard.list_menu()}


@router.put("/menu/{product_id}")
def owner_update_menu(product_id: int, payload: MenuUpdate, request: Request):
    owner = require_owner(request)
    try:
        return dashboard.update_product(owner, product_id, price=payload.price, available=payload.available)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/usage")
def owner_agent_usage(request: Request, days: int = 7):
    """Private owner-only totals; never expose customer messages or identities."""
    require_owner(request)
    if days < 1 or days > 30:
        raise HTTPException(status_code=422, detail="Days must be between 1 and 30.")
    from core.services.agent_usage_service import recent_usage_summary
    return recent_usage_summary(days)


@router.get("/customers")
def owner_customers(request: Request):
    require_owner(request)
    return {"customers": dashboard.list_customers()}
