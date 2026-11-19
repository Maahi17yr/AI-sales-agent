"""Owner-only API routes; no owner access is granted by client-provided roles."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, StrictBool

from core.services import auth_service, owner_dashboard_service as dashboard
from core.services import prepared_stock_service as prepared_stock
from core.services import owner_notifications_service as notifications
from core.services import owner_assistant_service as assistant

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



class PreparedStockConfig(BaseModel):
    product_id: int = Field(gt=0)
    variant: str = Field(default="standard", min_length=1, max_length=40)
    unit: str
    serving_units: int = Field(gt=0, le=1000000000)
    tolerance_units: int = Field(ge=0, le=1000000000)
    extra_units: int = Field(gt=0, le=1000000000)
    extra_tolerance_units: int = Field(ge=0, le=1000000000)
    extra_price: int = Field(ge=0, le=100000000)


class PreparedStockAdjustment(BaseModel):
    product_id: int = Field(gt=0)
    variant: str = Field(default="standard", min_length=1, max_length=40)
    action: str
    quantity_units: int = Field(ge=0, le=1000000000)
    reason: str = Field(min_length=3, max_length=240)


@router.get("/prepared-stock")
def owner_prepared_stock(request: Request):
    require_owner(request)
    items = prepared_stock.list_stocks()
    return {"items": items, "checkout_enabled": any(item["active"] for item in items)}


@router.get("/prepared-stock/history")
def owner_prepared_stock_history(request: Request):
    require_owner(request)
    return {"movements": prepared_stock.recent_movements()}


@router.post("/prepared-stock/config")
def owner_prepared_stock_config(payload: PreparedStockConfig, request: Request):
    owner = require_owner(request)
    try:
        return prepared_stock.configure(owner, payload.model_dump())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/prepared-stock/adjust")
def owner_prepared_stock_adjust(payload: PreparedStockAdjustment, request: Request):
    owner = require_owner(request)
    try:
        return prepared_stock.adjust(owner, payload.model_dump())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc



class StockActivation(BaseModel):
    product_id: int = Field(gt=0)
    variant: str = Field(default="standard", min_length=1, max_length=40)
    active: StrictBool


class AddonMapping(BaseModel):
    product_id: int = Field(gt=0)
    ingredient_id: int = Field(gt=0)
    source: str
    target_product_id: int | None = Field(default=None, gt=0)
    target_variant: str | None = Field(default=None, max_length=40)


class SelfExtra(BaseModel):
    product_id: int = Field(gt=0)
    display_name: str = Field(min_length=3, max_length=80)
    max_quantity: int = Field(default=2, ge=1, le=20)


@router.post("/prepared-stock/activate")
def owner_activate_prepared(payload: StockActivation, request: Request):
    owner = require_owner(request)
    try:
        return prepared_stock.set_active(owner, payload.product_id,
                                         payload.variant, payload.active)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/prepared-stock/addons/{product_id}")
def owner_prepared_addons(product_id: int, request: Request):
    require_owner(request)
    try:
        return {"addons": prepared_stock.list_addon_links(product_id)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/prepared-stock/addons")
def owner_map_prepared_addon(payload: AddonMapping, request: Request):
    owner = require_owner(request)
    try:
        return prepared_stock.map_addon(
            owner, payload.product_id, payload.ingredient_id, payload.source,
            payload.target_product_id, payload.target_variant)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/prepared-stock/self-extra")
def owner_prepared_self_extra(payload: SelfExtra, request: Request):
    owner = require_owner(request)
    try:
        return prepared_stock.create_self_extra(
            owner, payload.product_id, payload.display_name, payload.max_quantity)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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


@router.get("/customers")
def owner_customers(request: Request):
    require_owner(request)
    return {"customers": dashboard.list_customers()}


class NotificationReadUpdate(BaseModel):
    read: StrictBool


@router.get("/notifications")
def owner_notifications(request: Request):
    owner = require_owner(request)
    return notifications.list_notifications(owner["id"])


@router.put("/notifications/{event_key:path}/read")
def owner_notification_read(event_key: str, payload: NotificationReadUpdate, request: Request):
    owner = require_owner(request)
    try:
        return notifications.set_read(owner["id"], event_key, payload.read)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/notifications/read-all")
def owner_notifications_read_all(request: Request):
    owner = require_owner(request)
    return notifications.mark_all_read(owner["id"])


class OwnerAssistantTurn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=1200)


class OwnerAssistantRequest(BaseModel):
    message: str = Field(min_length=1, max_length=600)
    history: list[OwnerAssistantTurn] = Field(default_factory=list, max_length=8)


@router.post("/assistant")
def owner_assistant_message(payload: OwnerAssistantRequest, request: Request):
    """Owner-only, read-only Bedrock analysis, never the customer ordering agent."""
    require_owner(request)
    try:
        return assistant.answer(payload.message, [turn.model_dump() for turn in payload.history])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        # Provider failures must not leak credentials, model details or business data.
        import logging
        logging.getLogger(__name__).exception("Owner AI request failed")
        raise HTTPException(status_code=503, detail="Owner AI is temporarily unavailable. Please retry.") from None
