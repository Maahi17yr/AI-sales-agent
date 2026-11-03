
"""
Naija Bites AG-UI backend.

Local development and explicitly configured public DEMO mode.

Features:
- AI chat endpoint
- CORS for Next.js
- Local test customer
- Checkout summary retrieval
- Customer-controlled order confirmation

Run:
python -m uvicorn core.api.server:app --host 127.0.0.1 --port 8000

IMPORTANT:
Public demo mode requires explicit configuration and authenticated customer sessions.
Paystack remains in TEST mode. Free-tier storage is ephemeral.
"""

import json
import uuid
import os
import asyncio
import logging
import re

from contextlib import asynccontextmanager

from dotenv import load_dotenv

from fastapi import (
    FastAPI,
    Request,
    HTTPException,
)

from fastapi.middleware.cors import CORSMiddleware

from fastapi.responses import (
    JSONResponse,
    StreamingResponse,
)

from pydantic import BaseModel, StrictBool, Field
from typing import Literal
from uuid import UUID
import time
from core.services import paystack_service, auth_service
from core.api.owner import router as owner_router

from ag_ui.core import RunAgentInput
from ag_ui.encoder import EventEncoder
from ag_ui_strands import StrandsAgent, StrandsAgentConfig
from strands.hooks.events import AfterInvocationEvent


# ------------------------------------------
# ENVIRONMENT
# ------------------------------------------

load_dotenv()


# ------------------------------------------
# PROJECT IMPORTS
# ------------------------------------------

from data.database.models import get_connection
from data.database import repository
from core.intelligence.agent import make_agent

from core.services.checkout import CheckoutService
from core.services.checkout_state import get_checkout_state
from core.services import agent_usage_service
from core.memory.session import make_session_manager
from core.services.cart import CartService
from core.services.discount_service import DiscountService
from core import config


# ------------------------------------------
# REQUEST MODELS
# ------------------------------------------

class RegisterRequest(BaseModel):
    name: str
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


AUTH_COOKIE_NAME = auth_service.SESSION_COOKIE_NAME
AUTH_COOKIE_MAX_AGE = auth_service.SESSION_DAYS * 24 * 60 * 60
DEPLOYMENT_MODE = os.getenv("NAIJA_BITES_ENV", "development").strip().lower()
DEMO_FRONTEND_ORIGIN = os.getenv("NAIJA_BITES_FRONTEND_ORIGIN", "").strip().rstrip("/")
LOCAL_ORIGINS = {
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
}
# Additional exact HTTPS origins for temporary Vercel preview deployments.
# Keep the primary frontend origin unchanged for Paystack callback validation.
EXTRA_FRONTEND_ORIGINS = {
    origin.strip().rstrip("/")
    for origin in os.getenv("NAIJA_BITES_EXTRA_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
}
# Vercel generates a new hostname for every deployment. Restrict these origins
# to this project's name AND team slug; never allow arbitrary *.vercel.app.
# This also accepts the project's stable default hostname.
VERCEL_PROJECT_ORIGIN_RE = re.compile(
    r"https://ai-sales-agent-uerh(?:-[a-z0-9-]+-huhm2)?\.vercel\.app"
)
AUTH_ALLOWED_ORIGINS = (
    ({DEMO_FRONTEND_ORIGIN} | EXTRA_FRONTEND_ORIGINS)
    if DEPLOYMENT_MODE == "demo"
    else LOCAL_ORIGINS
)


def is_allowed_frontend_origin(origin: str) -> bool:
    return origin in AUTH_ALLOWED_ORIGINS or (
        DEPLOYMENT_MODE == "demo"
        and VERCEL_PROJECT_ORIGIN_RE.fullmatch(origin) is not None
    )


def _auth_cookie_secure() -> bool:
    return DEPLOYMENT_MODE == "demo"


def _set_auth_cookie(response: JSONResponse, token: str) -> None:
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        max_age=AUTH_COOKIE_MAX_AGE,
        httponly=True,
        secure=_auth_cookie_secure(),
        samesite="lax",
        path="/",
    )


def _require_authenticated_customer(request: Request) -> dict:
    customer = auth_service.authenticate_token(request.cookies.get(AUTH_COOKIE_NAME))
    if customer is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return customer


def _customer_session(request: Request) -> str:
    """Resolve the trusted customer identity from the login cookie only."""
    customer = _require_authenticated_customer(request)
    session_id = f"customer-{customer['id']}"
    repository.bind_session_customer(session_id, customer["id"])
    return session_id


_agent_locks: dict[str, asyncio.Lock] = {}


def _agent_lock(session_id: str) -> asyncio.Lock:
    if session_id not in _agent_locks:
        _agent_locks[session_id] = asyncio.Lock()
    return _agent_locks[session_id]


class ChatMessage(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    role: Literal["user", "assistant"]
    content: str = Field(max_length=20000)


class SaveChatRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    messages: list[ChatMessage] = Field(min_length=1, max_length=300)


class PaymentInitialize(BaseModel):
    order_id: int
    email: str
    frontend_origin: str | None = None


class PaymentVerify(BaseModel):
    reference: str
    # Chat ID is only used after authenticated, provider-verified payment.
    chat_id: UUID | None = None


class PreferenceDecision(BaseModel):
    request_id: str
    approved: StrictBool


class CheckoutApproval(BaseModel):
    customer_approved: StrictBool
    reviewed_fingerprint: str
    checkout_id: str | None = Field(default=None, max_length=100)


class CheckoutPrepareRequest(BaseModel):
    customer_name: str = Field(min_length=1, max_length=120)
    phone: str = Field(min_length=1, max_length=32)
    delivery_address: str = Field(min_length=1, max_length=500)
    delivery_area: str = Field(min_length=1, max_length=120)


# ------------------------------------------
# CREATE LOCAL TEST CUSTOMER
# ------------------------------------------

def _create_local_dev_session() -> str:
    """
    Create a fictional customer and session.

    For local testing only.
    Not a real authentication mechanism.
    """

    # Stable, LOCAL-ONLY test identity so restarts reuse the same session.
    # Never use this identity mechanism for real customers.
    from data.database import repository

    session_id = os.getenv("NAIJA_BITES_DEV_SESSION_ID", "naija-bites-local-dev-v1")
    if not session_id.strip():
        raise ValueError("NAIJA_BITES_DEV_SESSION_ID must not be empty")

    customer = repository.save_customer(
        phone="DEV-NAIJA-BITES-LOCAL-001",
        name="Naija Bites Local Test Customer",
    )
    repository.bind_session_customer(session_id, customer["id"])
    return session_id


# ------------------------------------------
# APPLICATION LIFESPAN
# ------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):

    if DEPLOYMENT_MODE not in {"development", "demo"}:
        raise RuntimeError("Unsupported environment: only development or demo is enabled.")
    if DEPLOYMENT_MODE == "demo":
        if not os.getenv("DATABASE_URL", "").strip():
            raise RuntimeError("Demo requires DATABASE_URL for durable PostgreSQL storage.")
        if not DEMO_FRONTEND_ORIGIN.startswith("https://"):
            raise RuntimeError("Demo requires HTTPS NAIJA_BITES_FRONTEND_ORIGIN.")
        if any(not origin.startswith("https://") for origin in EXTRA_FRONTEND_ORIGINS):
            raise RuntimeError("Demo extra allowed origins must use HTTPS.")
        if not os.getenv("PAYSTACK_SECRET_KEY", "").startswith("sk_test_"):
            raise RuntimeError("Demo requires a Paystack TEST secret key.")
        if not os.getenv("PAYSTACK_CALLBACK_URL", "").startswith(DEMO_FRONTEND_ORIGIN + "/"):
            raise RuntimeError("Demo Paystack callback must use the configured frontend HTTPS origin.")
        if not os.getenv("BEDROCK_MODEL_ID"):
            raise RuntimeError("Demo requires BEDROCK_MODEL_ID.")
        # Create only missing schema/catalogue; never overwrite existing orders or stock.
        from data.database.models import create_tables
        from data.database.seed import seed_products, seed_delivery_zones
        from data.database.customization_schema import create_customization_tables
        from data.database.seed_customizations import seed_customizations
        create_tables()
        with get_connection() as conn:
            seed_products(conn)
            seed_delivery_zones(conn)
        create_customization_tables()
        seed_customizations()
    print(f"Naija Bites backend initialized in {DEPLOYMENT_MODE} mode.")
    yield


# ------------------------------------------
# FASTAPI APPLICATION
# ------------------------------------------

app = FastAPI(
    title="Naija Bites API",
    description="AI Food Ordering Development Backend",
    version="0.2.0",
    lifespan=lifespan,
)
app.include_router(owner_router)


# ------------------------------------------
# CORS
# ------------------------------------------

app.add_middleware(
    CORSMiddleware,

    allow_origins=sorted(AUTH_ALLOWED_ORIGINS),
    allow_origin_regex=VERCEL_PROJECT_ORIGIN_RE.pattern if DEPLOYMENT_MODE == "demo" else None,

    allow_credentials=True,

    allow_methods=[
        "GET",
        "POST",
        "PUT",
        "PATCH",
    ],

    allow_headers=[
        "Content-Type",
        "X-Naija-Bites-Expected-Customer",
    ],
)


# ------------------------------------------
# LOCALHOST SECURITY
# ------------------------------------------

@app.middleware("http")
async def localhost_only(
    request: Request,
    call_next,
):
    """
    Restrict this development backend to
    requests originating from localhost.
    """

    if (
        DEPLOYMENT_MODE == "development"
        and request.client
        and request.client.host
        not in {"127.0.0.1", "::1"}
    ):
        return JSONResponse(
            {
                "detail": "Local development only"
            },
            status_code=403,
        )

    return await call_next(request)


# ------------------------------------------
# CUSTOMER AUTHENTICATION (LOCAL TESTING)
# ------------------------------------------

@app.middleware("http")
async def protect_auth_cookie_requests(request: Request, call_next):
    # Browsers send Origin for cross-origin POSTs. Reject unknown origins
    # before any authentication mutation; requests with no Origin (CLI)
    # remain usable for local testing.
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path != "/payments/webhook":
        origin = request.headers.get("origin")
        if origin is not None and not is_allowed_frontend_origin(origin):
            return JSONResponse({"detail": "Origin not allowed."}, status_code=403)
        # The browser sends the account it believes is active. Refuse writes if
        # another tab replaced the shared cookie after the frontend checked it.
        expected_customer = request.headers.get("x-naija-bites-expected-customer")
        if expected_customer is not None:
            current = auth_service.authenticate_token(request.cookies.get(AUTH_COOKIE_NAME))
            if current is None or str(current["id"]) != expected_customer:
                return JSONResponse({"detail": "Account changed. Refresh before continuing."}, status_code=409)
        # DELETE /chats has no request body; JSON is required only for body-bearing writes.
        if request.method in {"POST", "PUT", "PATCH"} and request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json" and request.url.path not in {"/auth/logout", "/agent"}:
            return JSONResponse({"detail": "JSON body required."}, status_code=415)
    return await call_next(request)


@app.post("/auth/register", status_code=201)
def auth_register(payload: RegisterRequest):
    try:
        customer = auth_service.register_customer(
            payload.name, payload.email, payload.password
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Registration does not automatically sign in.
    return {"customer": customer}


@app.post("/auth/login")
def auth_login(payload: LoginRequest):
    try:
        result = auth_service.login_customer(payload.email, payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid email or password.") from exc
    response = JSONResponse({"customer": result["customer"]})
    _set_auth_cookie(response, result["session_token"])
    response.headers["Cache-Control"] = "no-store"
    return response


# Durable chat history is always resolved from the session cookie, never a client customer ID.
@app.get("/chats")
def list_customer_chats(request: Request):
    customer_id = _require_authenticated_customer(request)["id"]
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT chat_id, title, messages, updated_at
            FROM customer_chats WHERE customer_id = ?
            ORDER BY updated_at DESC LIMIT 30
        """, (customer_id,)).fetchall()
    response = JSONResponse({"chats": [
        {"id": row["chat_id"], "title": row["title"],
         "messages": json.loads(row["messages"]), "updatedAt": row["updated_at"]}
        for row in rows
    ]})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.delete("/chats")
def clear_customer_chats(request: Request):
    """Permanently clear only the signed-in customer's saved conversations."""
    customer_id = _require_authenticated_customer(request)["id"]
    with get_connection() as conn:
        conn.execute("DELETE FROM customer_chats WHERE customer_id = ?", (customer_id,))
    response = JSONResponse({"cleared": True})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.put("/chats/{chat_id}")
def save_customer_chat(chat_id: UUID, payload: SaveChatRequest, request: Request):
    customer_id = _require_authenticated_customer(request)["id"]
    messages = [message.model_dump() for message in payload.messages]
    # Never persist a conversation consisting only of the welcome message.
    if not any(message["role"] == "user" for message in messages):
        raise HTTPException(status_code=400, detail="A user message is required.")
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO customer_chats (customer_id, chat_id, title, messages, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(customer_id, chat_id) DO UPDATE SET
                title=excluded.title, messages=excluded.messages,
                updated_at=excluded.updated_at
        """, (customer_id, str(chat_id), payload.title, json.dumps(messages),
              int(time.time() * 1000)))
    response = JSONResponse({"saved": True})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/auth/me")
def auth_me(request: Request):
    response = JSONResponse({"customer": _require_authenticated_customer(request)})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/auth/logout")
def auth_logout(request: Request):
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if token:
        auth_service.logout_customer(token)
    response = JSONResponse({"success": True})
    response.delete_cookie(
        key=AUTH_COOKIE_NAME,
        path="/",
        secure=_auth_cookie_secure(),
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


# ------------------------------------------
# HEALTH CHECK
# ------------------------------------------

@app.get("/ping")
async def ping():

    return {
        "status": "ok",
        "mode": "authenticated-demo" if DEPLOYMENT_MODE == "demo" else "authenticated-local-development",
        "checkout_enabled": True,
    }


# ------------------------------------------
# AI AGENT ENDPOINT
# ------------------------------------------

@app.get("/menu")
def menu(request: Request):
    """Catalogue prices and approved bulk discount tiers (no per-item sale)."""
    _customer_session(request)
    return {
        "discount_policy": {
            "enabled": config.DISCOUNTS_ENABLED,
            "min_order": config.MIN_ORDER_FOR_DISCOUNT,
            "max_percent": config.MAX_DISCOUNT_PERCENT,
            "tiers": [
                {"minimum": minimum, "percent": percent}
                for minimum, percent in sorted(config.BULK_DISCOUNT_TIERS.items())
            ] if config.DISCOUNTS_ENABLED else [],
        },
        "products": [
            {"id": p["id"], "name": p["name"], "category": p["category"],
             "description": p["description"], "price": p["price"]}
            for p in repository.get_all_products()
        ],
    }


@app.get("/cart")
def current_cart(request: Request):
    """The existing discount service decides eligibility and amounts."""
    session_id = _customer_session(request)
    cart = CartService(session_id).view_cart()
    discount = DiscountService(session_id).calculate_discount()
    return {**cart, "discount_percent": discount["discount_percent"],
            "discount": discount["discount_amount"],
            "discount_eligible": discount["eligible"],
            "discount_message": discount["message"]}


@app.post("/agent")
async def run_agent(
    input_data: RunAgentInput,
    request: Request,
):
    """
    Stream AI responses to the Next.js frontend.

    The trusted session is assigned by the server,
    not supplied by the browser or AI.
    """

    session_id = _customer_session(request)
    encoder = EventEncoder(accept=request.headers.get("accept"))

    async def stream():
        # Only numeric model usage and timing are recorded; no customer text.
        started = time.perf_counter()
        first_event_ms = None
        event_count = 0
        outcome = "completed"

        def record_live_usage(event: AfterInvocationEvent) -> None:
            # AG-UI builds a new per-thread Agent from our template. Only
            # AfterInvocationEvent.result reports the tokens actually spent.
            result = getattr(event, "result", None)
            if result is None:
                return
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            try:
                agent_usage_service.record_agent_usage(
                    session_id, result, "completed", elapsed_ms,
                )
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "agent_usage_record_failed error_type=%s", type(exc).__name__,
                )

        try:
            async with _agent_lock(session_id):
                agent = StrandsAgent(
                    agent=make_agent(session_id),
                    name="naija_bites",
                    description="Naija Bites food ordering assistant",
                    # The wrapper reconstructs the template per AG-UI thread.
                    # Its template session manager would otherwise be ignored.
                    config=StrandsAgentConfig(
                        session_manager_provider=lambda thread: make_session_manager(
                            session_id, thread.thread_id,
                        ),
                        # The persistent Strands session holds the model history.
                        # Avoid replaying the entire UI transcript into it.
                        replay_history_into_strands=False,
                    ),
                    hooks=[record_live_usage],
                )
                async for event in agent.run(input_data):
                    event_count += 1
                    if first_event_ms is None:
                        first_event_ms = round((time.perf_counter() - started) * 1000)
                    yield encoder.encode(event)
                # Structured, trusted UI action after the agent has finished.
                # This is independent of whatever the model says in its reply.
                state = get_checkout_state(session_id)
                yield "data: " + json.dumps({
                    "type": "CHECKOUT_STATE", "checkout": state,
                }, separators=(",", ":")) + "\n\n"
        except Exception:
            outcome = "error"
            raise
        finally:
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            logging.getLogger(__name__).info(
                "agent_stream_latency total_ms=%d first_event_ms=%s events=%d outcome=%s",
                elapsed_ms, first_event_ms, event_count, outcome,
            )


    return StreamingResponse(
        stream(),

        media_type=encoder.get_content_type(),

        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ------------------------------------------
# GET CHECKOUT SUMMARY
# ------------------------------------------

@app.get("/checkout")
async def get_checkout(request: Request):
    """
    Return the currently prepared checkout.

    If there is no pending checkout, return
    pending=False rather than a 404 error.
    """

    session_id = _customer_session(request)
    return get_checkout_state(session_id)


@app.post("/checkout/prepare")
async def prepare_checkout_direct(payload: CheckoutPrepareRequest, request: Request):
    """Recovery path when the AI failed to invoke its preparation tool.

    Uses the same trusted validation and saved state as the agent tool. This
    never confirms an order, charges a payment or trusts AI-generated totals.
    """
    session_id = _customer_session(request)
    async with _agent_lock(session_id):
        result = CheckoutService(session_id).prepare_checkout(
            customer_name=payload.customer_name,
            phone=payload.phone,
            delivery_address=payload.delivery_address,
            delivery_area=payload.delivery_area,
        )
        if not result.get("success"):
            raise HTTPException(status_code=409, detail=result.get("message", "Could not prepare checkout."))
        return get_checkout_state(session_id)


# ------------------------------------------
# CONFIRM CHECKOUT
# ------------------------------------------

@app.post("/checkout/confirm")
async def confirm_checkout(
    approval: CheckoutApproval,
    request: Request,
):
    """
    Create an order only after explicit
    customer approval in the frontend.

    The AI cannot call this endpoint as a tool.

    The checkout service validates the
    fingerprint and rechecks the order.
    """

    if approval.customer_approved is not True:

        raise HTTPException(
            status_code=400,
            detail=(
                "Explicit customer approval "
                "is required."
            ),
        )

    if not approval.reviewed_fingerprint.strip():

        raise HTTPException(
            status_code=400,
            detail=(
                "A reviewed order fingerprint "
                "is required."
            ),
        )

    session_id = _customer_session(request)

    checkout_service = CheckoutService(
        session_id
    )

    result = checkout_service.confirm_checkout(
        customer_approved=True,
        reviewed_fingerprint=(
            approval.reviewed_fingerprint
        ),
        checkout_id=approval.checkout_id,
    )

    if not result.get("success"):

        raise HTTPException(
            status_code=409,
            detail=result.get(
                "message",
                "Checkout confirmation failed.",
            ),
        )

    # Return the trusted service result.
    # The order should now be pending payment.
    return result


# ------------------------------------------
# DEVELOPMENT CUSTOMER DETAILS
# ------------------------------------------

@app.get("/checkout/dev-customer")
async def get_dev_customer(request: Request):
    """
    Return fictional customer details.

    Local development only.

    Useful because checkout validates the
    phone against the bound test customer.
    """

    session_id = _customer_session(request)

    conn = get_connection()

    try:

        row = conn.execute(
            """
            SELECT
                c.name,
                c.phone

            FROM customers AS c

            JOIN conversation_sessions AS s
                ON s.customer_id = c.id

            WHERE s.session_id = ?
            """,
            (session_id,),
        ).fetchone()

        if row is None:

            raise HTTPException(
                status_code=404,
                detail="Test customer not found.",
            )

        return {
            "name": row["name"],
            "phone": row["phone"],
        }

    finally:
        conn.close()


# Customer identity is always resolved from the authenticated cookie.
@app.get("/memory/pending")
async def memory_pending(request: Request):
    from core.memory import preferences
    pending = preferences.get_pending_preference(_customer_session(request))
    return {"success": True, "pending": pending is not None, "proposal": pending}


@app.post("/memory/decision")
async def memory_decision(decision: PreferenceDecision, request: Request):
    from core.memory import preferences
    try:
        result = preferences.resolve_preference(
            _customer_session(request), decision.request_id, decision.approved
        )
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"success": True, **result}


# Paystack TEST mode only; the customer is authenticated and scoped to their session.
@app.post("/payments/initialize")
def initialize_payment(payload: PaymentInitialize, request: Request):
    # A proxy may omit the browser Origin; accept only explicitly approved origins.
    origin = payload.frontend_origin or request.headers.get("origin")
    if origin is not None and not is_allowed_frontend_origin(origin):
        raise HTTPException(status_code=403, detail="Payment return origin not allowed.")
    try:
        return paystack_service.initialize(
            payload.order_id, _customer_session(request), payload.email, origin
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/payments/verify")
def verify_payment(payload: PaymentVerify, request: Request):
    # The Paystack verification and order ownership check remain authoritative.
    try:
        result = paystack_service.verify(payload.reference, _customer_session(request))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if result.get("payment_status") != "paid":
        return result

    order_id = result["order_id"]
    confirmation = {
        "id": f"verified-payment-order-{order_id}",
        "role": "assistant",
        "content": (
            f"🎉 Payment confirmed for order #{order_id}! "
            "Thank you for choosing Naija Bites. Your order is confirmed. "
            "If you'd like anything else, we can start a fresh order for you. 🍛"
        ),
    }
    result["chat_message"] = confirmation
    result["chat_saved"] = False

    # Persist the verified message to the user's own conversation, so a reload
    # or an interrupted frontend state update cannot silently lose it.
    # Never accept a chat belonging to a different authenticated customer.
    if payload.chat_id is not None:
        customer_id = _require_authenticated_customer(request)["id"]
        with get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            chat = conn.execute(
                "SELECT messages FROM customer_chats "
                "WHERE customer_id = ? AND chat_id = ?",
                (customer_id, str(payload.chat_id)),
            ).fetchone()
            if chat is not None:
                messages = json.loads(chat["messages"])
                if not any(m.get("id") == confirmation["id"] for m in messages):
                    messages.append(confirmation)
                    conn.execute(
                        "UPDATE customer_chats SET messages = ?, updated_at = ? "
                        "WHERE customer_id = ? AND chat_id = ?",
                        (json.dumps(messages), int(time.time() * 1000),
                         customer_id, str(payload.chat_id)),
                    )
                result["chat_saved"] = True
    return result


@app.post("/payments/webhook")
async def payment_webhook(request: Request):
    body = await request.body()
    signature = request.headers.get("x-paystack-signature", "")
    if not paystack_service.verify_webhook_signature(body, signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature.")
    event = json.loads(body)
    if event.get("event") == "charge.success":
        reference = event.get("data", {}).get("reference")
        if isinstance(reference, str):
            try:
                paystack_service.verify(reference)
            except ValueError:
                # Log internally, but do not disclose sensitive payment details.
                import logging
                logging.exception("Unable to verify Paystack webhook reference")
                raise HTTPException(status_code=503, detail="Verification pending.")
    return {"received": True}
